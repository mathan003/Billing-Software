import uuid
import base64
import csv
from datetime import datetime, timedelta
from decimal import Decimal
from functools import wraps
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.db.models import Sum, Count, Q, F
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.http import HttpResponse, JsonResponse
from .models import (
    Product, Customer, Invoice, InvoiceItem, Purchase,
    PaymentRecord, ActiveUserSession, RegisteredDevice, UserProfile, ActivityLog,
    CompanySettings, Branch, StockLog, SoftwareUpdate, purge_old_customer_data, log_activity
)
from .device_utils import (
    get_hardware_device_id, get_desktop_pos_config, save_desktop_pos_config,
    clear_desktop_remembered_user, is_desktop_environment, check_internet_connection
)
from .pdf_generator import generate_invoice_pdf, generate_statement_pdf, generate_sales_report_pdf


def admin_required(view_func):
    """
    Decorator that restricts view access strictly to users with Admin privileges.
    Normal client users are completely blocked and redirected to dashboard.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        is_admin = request.user.is_superuser or (
            hasattr(request.user, "profile") and request.user.profile.role == "admin"
        )
        if not is_admin:
            messages.error(request, "Access Denied: You do not have administrator privileges to access the Admin Panel.")
            return redirect("billing:dashboard")
        return view_func(request, *args, **kwargs)
    return _wrapped


def is_admin_user(user):
    """Checks if a user has administrator status."""
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or (hasattr(user, "profile") and user.profile.role == "admin")


def get_client_filter(request):
    """
    Returns a Q filter that guarantees strict multi-tenant isolation.
    - Administrators have global overview access across all records.
    - Client users are strictly constrained to records owned by their account.
    """
    if not request.user.is_authenticated:
        return Q(pk__in=[])
    if is_admin_user(request.user):
        return Q()
    return Q(client=request.user) | Q(client__isnull=True)


def get_client_user(request):
    """Returns current user for assigning FK ownership."""
    if not request.user.is_authenticated:
        return None
    return request.user


def get_branch_filter(request):
    """Returns branch filter: own branches or default/shared branches."""
    if not request.user.is_authenticated:
        return Q(is_active=True)
    if is_admin_user(request.user):
        return Q(is_active=True)
    return Q(is_active=True) & (Q(client=request.user) | Q(client__isnull=True))



# ==========================================
# AUTHENTICATION (Login only, Max 5 Sessions)
# ==========================================

def login_view(request):
    """
    Dedicated Login Page:
    - Same fixed login link for everyone: /login/
    - Desktop App (EXE):
        * First-time login: Mandatory internet connection to verify credentials and register device with server.
        * Subsequent logins on same machine: Remembers username, requires PASSWORD ONLY.
        * Offers option to switch account if needed.
    - Web Browser:
        * Always prompts for both username and password every time ('web login every time').
    - Strict Admin Device Quotas:
        * Admin specifies allowed devices (e.g., 5 devices).
        * Exactly allowed number can login. A 6th device is STRICTLY BLOCKED with a clear error.
    - Access Mode Split:
        * 'offline_only' clients cannot log in via Web.
        * 'online_only' clients cannot log in via Desktop App.
    """
    if request.user.is_authenticated:
        sec = request.session.get("sec_token")
        if not sec:
            sec = uuid.uuid4().hex[:12]
            request.session["sec_token"] = sec
        return redirect(f"/?sec={sec}")

    is_desktop = is_desktop_environment(request)
    pos_cfg = get_desktop_pos_config() if is_desktop else {}
    switch_user = request.GET.get("switch_user") == "1"

    if switch_user and is_desktop:
        clear_desktop_remembered_user()
        pos_cfg = {}

    remembered_username = pos_cfg.get("remembered_username", "") if is_desktop else ""
    is_first_time_desktop = is_desktop and not bool(pos_cfg.get("is_activated"))
    device_id = get_hardware_device_id() if is_desktop else (
        request.POST.get("device_id") or request.COOKIES.get("billing_device_id") or f"WEB-{uuid.uuid4().hex[:12].upper()}"
    )

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        # For desktop app in password-only mode, fallback to remembered username if not submitted
        if not username and is_desktop and remembered_username:
            username = remembered_username

        if not username or not password:
            messages.error(request, "Please enter your login credentials.")
            return render(request, "billing/login.html", {
                "is_desktop_app": is_desktop,
                "remembered_username": remembered_username,
                "is_first_time_desktop": is_first_time_desktop,
                "device_id": device_id,
            })

        # 1. Desktop App First-Time Login: Mandatory Internet Verification
        if is_first_time_desktop:
            has_internet, net_msg = check_internet_connection()
            if not has_internet:
                messages.error(
                    request,
                    "First-time system activation requires an active internet connection to register this computer. "
                    "முதல் முறை உள்நுழைய இணைய இணைப்பு (Internet) கட்டாயமாகும். Please connect to internet and retry."
                )
                return render(request, "billing/login.html", {
                    "is_desktop_app": is_desktop,
                    "remembered_username": "",
                    "is_first_time_desktop": True,
                    "device_id": device_id,
                })

        user = authenticate(request, username=username, password=password)
        if user is None:
            messages.error(request, "Invalid username or password. Please try again.")
            return render(request, "billing/login.html", {
                "is_desktop_app": is_desktop,
                "remembered_username": remembered_username,
                "is_first_time_desktop": is_first_time_desktop,
                "device_id": device_id,
            })

        profile = getattr(user, "profile", None)
        is_admin_user = user.is_superuser or (profile and profile.role == "admin")

        # 2. Access Mode Split Verification (Online & Offline vs Offline Only vs Online Only)
        if not is_admin_user and profile:
            access_mode = profile.access_mode
            if access_mode == "offline_only" and not is_desktop:
                messages.error(
                    request,
                    "Access Denied: This client account is authorized for Desktop Offline POS only. Web login is not permitted."
                )
                return render(request, "billing/login.html", {
                    "is_desktop_app": is_desktop,
                    "remembered_username": "",
                    "device_id": device_id,
                })
            elif access_mode == "online_only" and is_desktop:
                messages.error(
                    request,
                    "Access Denied: This client account is authorized for Web Portal only. Desktop POS login is not permitted."
                )
                return render(request, "billing/login.html", {
                    "is_desktop_app": is_desktop,
                    "remembered_username": "",
                    "device_id": device_id,
                })

        # 3. Strict Device Quota Enforcement
        device_type = "desktop_exe" if is_desktop else "web_browser"
        device_name = "Windows POS Terminal" if is_desktop else (request.META.get("HTTP_USER_AGENT", "Web Browser")[:100])
        user_agent = request.META.get("HTTP_USER_AGENT", "Desktop/Mobile")[:200]
        ip_addr = request.META.get("REMOTE_ADDR", "127.0.0.1")

        if is_admin_user:
            # Administrator: strictly restricted to 1 active device
            ActiveUserSession.objects.filter(user=user).delete()
            RegisteredDevice.objects.filter(user=user).update(is_active=False)
            RegisteredDevice.objects.update_or_create(
                user=user,
                device_id=device_id,
                defaults={
                    "device_name": device_name,
                    "device_type": device_type,
                    "ip_address": ip_addr,
                    "is_active": True,
                }
            )
        else:
            # Client: Admin specifies allowed devices (e.g. 5 devices). 6th device is STRICTLY BLOCKED!
            dev_limit = profile.device_limit if (profile and profile.device_limit) else 5

            # Check if this physical device/token is already registered
            existing_device = RegisteredDevice.objects.filter(
                user=user,
                device_id=device_id,
                is_active=True
            ).first()

            if not existing_device:
                current_active_devices = RegisteredDevice.objects.filter(user=user, is_active=True).count()
                if current_active_devices >= dev_limit:
                    # STRICTLY BLOCK 6th DEVICE!
                    log_activity(
                        request,
                        "DEVICE_LIMIT_BLOCKED",
                        f"Blocked login attempt from unauthorized device for client '{user.username}' (Quota: {dev_limit}/{dev_limit} devices already in use)."
                    )
                    messages.error(
                        request,
                        f"Device Limit Exceeded! Admin has configured a maximum of {dev_limit} devices for this account. "
                        f"Login on this device is strictly NOT ALLOWED ({current_active_devices + 1}th device blocked). "
                        f"அனுமதிக்கப்பட்ட சாதனங்கள் வரம்பு முடிந்துவிட்டது ({dev_limit}/{dev_limit}). புதிய சாதனம் உள்நுழைய அனுமதி இல்லை. "
                        f"Please contact your Administrator or revoke an existing device in the Admin Panel."
                    )
                    return render(request, "billing/login.html", {
                        "is_desktop_app": is_desktop,
                        "remembered_username": remembered_username,
                        "device_id": device_id,
                    })

                # Register new approved device within allowed quota
                RegisteredDevice.objects.create(
                    user=user,
                    device_id=device_id,
                    device_name=device_name,
                    device_type=device_type,
                    ip_address=ip_addr,
                    is_active=True,
                )
                log_activity(
                    request,
                    "DEVICE_REGISTER",
                    f"Registered new device '{device_name}' ({device_id[:12]}) for client '{user.username}' ({current_active_devices + 1}/{dev_limit})."
                )
            else:
                existing_device.ip_address = ip_addr
                existing_device.device_name = device_name
                existing_device.save()

        # 4. Save persistent local configuration for Desktop App
        if is_desktop:
            save_desktop_pos_config({
                "device_id": device_id,
                "remembered_username": user.username,
                "shop_name": getattr(profile, "shop_name", ""),
                "is_activated": True,
            })

        # 5. Login and create session
        login(request, user)
        if not request.session.session_key:
            request.session.save()

        session_key = request.session.session_key
        sec_token = uuid.uuid4().hex[:12]
        request.session["sec_token"] = sec_token

        ActiveUserSession.objects.create(
            user=user,
            session_key=session_key,
            device_info=f"{device_name} ({device_id[:12]})",
            ip_address=ip_addr,
        )

        role_title = "Administrator (Single Device)" if is_admin_user else f"Client Operator (Max {profile.device_limit if profile else 5} Devices)"
        log_activity(
            request,
            "LOGIN",
            f"{role_title} '{user.username}' logged in successfully ({device_name} | IP: {ip_addr})."
        )

        messages.success(request, f"Welcome back, {user.username}!")
        response = redirect(f"/?sec={sec_token}")
        response.set_cookie("billing_device_id", device_id, max_age=365*24*3600*5)
        if is_desktop:
            response.set_cookie("is_desktop_pos", "true", max_age=365*24*3600*5)
        return response

    return render(request, "billing/login.html", {
        "is_desktop_app": is_desktop,
        "remembered_username": remembered_username,
        "is_first_time_desktop": is_first_time_desktop,
        "device_id": device_id,
    })


def logout_view(request):
    """Logs out and removes the active device session"""
    session_key = request.session.session_key
    if request.user.is_authenticated:
        log_activity(request, "LOGOUT", f"User '{request.user.username}' logged out.")
    if session_key:
        ActiveUserSession.objects.filter(session_key=session_key).delete()
    logout(request)
    messages.info(request, "You have been logged out successfully.")
    return redirect("billing:login")


# ==========================================
# DASHBOARD (Metrics, Add Customer, Quick Bill)
# ==========================================

def dashboard(request):
    today = timezone.now().date()
    c_filter = get_client_filter(request)
    b_filter = get_branch_filter(request)

    # 1. Today's Invoices & Total Sales scoped to client
    today_invoices = Invoice.objects.filter(c_filter, created_at__date=today).select_related("customer", "branch").prefetch_related("items").order_by("-created_at")
    today_sales = today_invoices.aggregate(Sum("grand_total"))["grand_total__sum"] or Decimal("0.00")
    today_bills_count = today_invoices.count()

    # 2. Customer Paid TODAY (replaces previous Total Buy!)
    client_invoices_all = Invoice.objects.filter(c_filter)
    today_payments = PaymentRecord.objects.filter(invoice__in=today_invoices).aggregate(Sum("amount"))["amount__sum"] or Decimal("0.00")
    if today_payments == Decimal("0.00") and today_invoices.exists():
        today_payments = today_invoices.aggregate(Sum("paid_amount"))["paid_amount__sum"] or Decimal("0.00")
    today_customer_paid = today_payments

    # All-time customer payments for this client
    all_time_customer_paid = client_invoices_all.aggregate(Sum("paid_amount"))["paid_amount__sum"] or Decimal("0.00")

    # 3. Pending Amounts (Total Balance / Due across all customers of this client)
    total_pending_amount = client_invoices_all.aggregate(Sum("balance_amount"))["balance_amount__sum"] or Decimal("0.00")

    # Total counts
    total_invoices_count = client_invoices_all.count()
    low_stock_count = Product.objects.filter(c_filter, stock_quantity__lte=5, is_active=True).count()

    # 4. Pending Customers Breakdown (Customers who currently have pending payments)
    pending_customers_qs = Customer.objects.filter(c_filter).annotate(
        calc_pending=Sum("invoices__balance_amount"),
        calc_billed=Sum("invoices__grand_total"),
        calc_paid=Sum("invoices__paid_amount"),
        due_bills_count=Count("invoices", filter=Q(invoices__balance_amount__gt=0))
    ).filter(calc_pending__gt=0).order_by("-calc_pending")

    pending_customers = []
    for c in pending_customers_qs[:50]:
        pending_customers.append({
            "id": c.id,
            "customer": c,
            "name": c.name,
            "phone": c.phone or "-",
            "address": c.address or "-",
            "total_billed": c.calc_billed or Decimal("0.00"),
            "total_paid": c.calc_paid or Decimal("0.00"),
            "balance_amount": c.calc_pending or Decimal("0.00"),
            "bills_count": c.due_bills_count,
        })

    # One-off walkin bills with balance due
    walkin_pending = client_invoices_all.filter(customer__isnull=True, balance_amount__gt=0).order_by("-created_at")[:20]

    recent_invoices = client_invoices_all.select_related("customer", "branch").prefetch_related("items").order_by("-created_at")[:10]
    customers = Customer.objects.filter(c_filter).order_by("name")
    products = Product.objects.filter(c_filter, is_active=True).order_by("name_tamil", "name")
    branches = Branch.objects.filter(b_filter).order_by("-is_default", "name")
    default_branch = Branch.get_default_branch()

    context = {
        "today_sales": today_sales,
        "today_bills_count": today_bills_count,
        "today_customer_paid": today_customer_paid,
        "all_time_customer_paid": all_time_customer_paid,
        "total_pending_amount": total_pending_amount,
        "today_invoices": today_invoices,
        "pending_customers": pending_customers,
        "walkin_pending": walkin_pending,
        "total_invoices_count": total_invoices_count,
        "low_stock_count": low_stock_count,
        "recent_invoices": recent_invoices,
        "customers": customers,
        "products": products,
        "branches": branches,
        "default_branch": default_branch,
    }
    return render(request, "billing/dashboard.html", context)


def parse_decimal(val, default="0.00"):
    """Safely parse decimal without crashing on empty strings or invalid format"""
    if val is None or not str(val).strip():
        return Decimal(default)
    try:
        return Decimal(str(val).strip())
    except Exception:
        return Decimal(default)


# ==========================================
# DEDICATED BILLING TERMINAL & QUICK BILLING
# ==========================================

def billing_page(request):
    """
    Dedicated Billing Page Terminal:
    - Customer selection / customer name field left blank by default
    - Branch selection before generating bills
    - Multi-product bill items with dynamic units (KG, Pack, Box, Pcs, Ltr)
    - Unlimited quantities (>500 units without restriction)
    - Row-level 'Remove Product' option
    - Permanent '+ Add Product' button
    - Payment mode (Cash or Online), paid amount, auto-balance calculation
    - 'Print & Save' creates invoice, decrements stock, records StockLog, records payment, and shows receipt.
    """
    if request.method == "POST":
        customer_id = request.POST.get("customer_id", "").strip()
        customer_name = request.POST.get("customer_name", "").strip()
        customer_phone = request.POST.get("customer_phone", "").strip()
        branch_id = request.POST.get("branch_id", "").strip()
        payment_method = request.POST.get("payment_method", "Cash")
        paid_amount_str = request.POST.get("paid_amount", "").strip()
        notes = request.POST.get("notes", "").strip()

        product_ids = request.POST.getlist("product_id[]")
        units = request.POST.getlist("unit[]")
        unit_prices = request.POST.getlist("unit_price[]")
        quantities = request.POST.getlist("quantity[]")

        if not product_ids:
            messages.error(request, "Please add at least one product to the bill.")
            return redirect("billing:billing_page")

        c_filter = get_client_filter(request)
        client_user = get_client_user(request)

        # Branch association
        branch_obj = None
        if branch_id:
            branch_obj = Branch.objects.filter(pk=branch_id, is_active=True).first()
        if not branch_obj:
            branch_obj = Branch.get_default_branch()

        # Customer association (default blank if not selected or entered, falls back to 'Cash Customer')
        cust_obj = None
        if customer_id:
            cust_obj = Customer.objects.filter(c_filter, pk=customer_id).first()
            if cust_obj and not customer_name:
                customer_name = cust_obj.name
            if cust_obj and not customer_phone and cust_obj.phone:
                customer_phone = cust_obj.phone
        elif customer_phone:
            cust_obj, _ = Customer.objects.get_or_create(
                phone=customer_phone,
                client=client_user,
                defaults={"name": customer_name or "Cash Customer"}
            )

        final_customer_name = cust_obj.name if cust_obj else (customer_name or "Cash Customer")
        final_customer_phone = cust_obj.phone if cust_obj else customer_phone

        # Generate unique collision-free sequential Invoice Number
        prefix = "POS" if is_desktop_environment(request) else "WEB"
        date_str = timezone.now().strftime("%Y%m%d")
        count = Invoice.objects.filter(invoice_number__startswith=f"{prefix}-{date_str}").count() + 1
        inv_number = f"{prefix}-{date_str}-{count:04d}"
        while Invoice.objects.filter(invoice_number=inv_number).exists():
            count += 1
            inv_number = f"{prefix}-{date_str}-{count:04d}"

        # Calculate line items
        line_items = []
        subtotal = Decimal("0.00")
        total_tax = Decimal("0.00")

        for i in range(len(product_ids)):
            pid = product_ids[i]
            if not pid:
                continue

            product = Product.objects.filter(c_filter, pk=pid, is_active=True).first()
            if not product:
                continue

            unit = units[i] if i < len(units) else product.unit
            price = parse_decimal(unit_prices[i] if i < len(unit_prices) else str(product.price), str(product.price))
            qty = parse_decimal(quantities[i] if i < len(quantities) else "1", "1")

            if qty <= Decimal("0.00"):
                continue

            item_subtotal = price * qty
            item_tax = (item_subtotal * product.tax_percent) / Decimal("100.00")
            item_total = item_subtotal + item_tax

            subtotal += item_subtotal
            total_tax += item_tax

            line_items.append({
                "product": product,
                "name": product.display_name,
                "sku": product.sku,
                "unit": unit,
                "unit_price": price,
                "quantity": qty,
                "tax_percent": product.tax_percent,
                "tax_amount": item_tax,
                "total_price": item_total,
            })

        if not line_items:
            messages.error(request, "Please add at least one valid product with quantity > 0.")
            return redirect("billing:billing_page")

        discount_amount = parse_decimal(request.POST.get("discount_amount", "0"), "0.00")
        max_discount = subtotal + total_tax
        if discount_amount > max_discount:
            discount_amount = max_discount

        grand_total = max(Decimal("0.00"), subtotal + total_tax - discount_amount)
        paid_amount = parse_decimal(paid_amount_str, str(grand_total))

        # Create Invoice
        invoice = Invoice.objects.create(
            invoice_uuid=uuid.uuid4(),
            invoice_number=inv_number,
            client=client_user,
            branch=branch_obj,
            branch_name=branch_obj.name if branch_obj else "Main Shop Branch",
            customer=cust_obj,
            customer_name=final_customer_name,
            customer_phone=final_customer_phone,
            subtotal=subtotal,
            tax_amount=total_tax,
            discount_amount=discount_amount,
            grand_total=grand_total,
            paid_amount=paid_amount,
            payment_method=payment_method,
            source="windows_app" if is_desktop_environment(request) else "web_mobile",
            notes=notes,
        )

        # Create Invoice Items and update stock + StockLog
        user_name = request.user.username if request.user.is_authenticated else "Billing Operator"
        for item in line_items:
            InvoiceItem.objects.create(
                invoice=invoice,
                product=item["product"],
                product_name=item["name"],
                product_sku=item["sku"],
                unit=item["unit"],
                unit_price=item["unit_price"],
                quantity=item["quantity"],
                tax_percent=item["tax_percent"],
                tax_amount=item["tax_amount"],
                discount_percent=Decimal("0.00"),
                total_price=item["total_price"],
            )

            # Decrement stock
            prod = item["product"]
            old_stock = prod.stock_quantity
            new_stock = max(Decimal("0.00"), old_stock - item["quantity"])
            prod.stock_quantity = new_stock
            prod.save(update_fields=["stock_quantity", "updated_at"])

            # Create StockLog entry for audit ledger
            StockLog.objects.create(
                product=prod,
                change_type="BILLING_SALE",
                quantity_change=-item["quantity"],
                previous_quantity=old_stock,
                new_quantity=new_stock,
                invoice=invoice,
                notes=f"Express billing sale #{inv_number} ({item['quantity']} {item['unit']})",
                created_by=user_name,
            )

        # Create initial payment record if paid > 0
        if paid_amount > Decimal("0.00"):
            PaymentRecord.objects.create(
                invoice=invoice,
                amount=paid_amount,
                payment_method=payment_method,
                notes=f"Initial payment on billing via {payment_method}"
            )

        # Log invoice creation to cloud database
        log_activity(
            request,
            "INVOICE_CREATE",
            f"Created bill #{inv_number} at branch '{invoice.branch_name}' for customer '{invoice.customer_name}'. Grand Total: ₹{grand_total}, Paid: ₹{paid_amount}, Balance Due: ₹{invoice.balance_amount}"
        )

        messages.success(request, f"Invoice {inv_number} created successfully! Grand Total: ₹{grand_total}")
        return redirect(f"/invoices/{invoice.id}/?autoprint=1")

    # GET request
    c_filter = get_client_filter(request)
    b_filter = get_branch_filter(request)
    products = Product.objects.filter(c_filter, is_active=True).order_by("name_tamil", "name")
    customers = Customer.objects.filter(c_filter).order_by("name")
    branches = Branch.objects.filter(b_filter).order_by("-is_default", "name")
    default_branch = Branch.get_default_branch()
    return render(request, "billing/billing_screen.html", {
        "products": products,
        "customers": customers,
        "branches": branches,
        "default_branch": default_branch,
    })


def quick_bill_create(request):
    """
    Quick Bill feature:
    Select product, choose branch, choose unit (KG, Pack, Box, Pcs, Ltr), enter quantity,
    specify paid amount / mode (Cash or Online), customer name blank by default, and Print & Save!
    """
    if request.method == "POST":
        customer_id = request.POST.get("customer_id", "").strip()
        customer_name = request.POST.get("customer_name", "").strip()
        customer_phone = request.POST.get("customer_phone", "").strip()
        branch_id = request.POST.get("branch_id", "").strip()

        product_id = request.POST.get("product_id")
        unit_type = request.POST.get("unit_type", "KG")
        quantity_str = request.POST.get("quantity", "1")
        payment_method = request.POST.get("payment_method", "Cash")
        paid_amount_str = request.POST.get("paid_amount", "")

        c_filter = get_client_filter(request)
        client_user = get_client_user(request)

        try:
            qty = parse_decimal(quantity_str, "1")
            if qty <= 0:
                messages.error(request, "Quantity must be greater than zero.")
                return redirect("billing:dashboard")

            product = get_object_or_404(Product.objects.filter(c_filter, is_active=True), pk=product_id)
            unit_price = product.price
            subtotal = unit_price * qty
            tax_amount = (subtotal * product.tax_percent) / Decimal("100.00")
            discount_amount = parse_decimal(request.POST.get("discount_amount", "0"), "0.00")
            max_discount = subtotal + tax_amount
            if discount_amount > max_discount:
                discount_amount = max_discount

            grand_total = max(Decimal("0.00"), subtotal + tax_amount - discount_amount)

            # Parse paid amount
            if paid_amount_str.strip():
                paid_amount = parse_decimal(paid_amount_str, str(grand_total))
            else:
                paid_amount = grand_total  # Defaults to full payment if not specified

            # Branch association
            branch_obj = None
            if branch_id:
                branch_obj = Branch.objects.filter(pk=branch_id, is_active=True).first()
            if not branch_obj:
                branch_obj = Branch.get_default_branch()

            # Customer association
            cust_obj = None
            if customer_id:
                cust_obj = Customer.objects.filter(c_filter, pk=customer_id).first()
                if cust_obj and not customer_name:
                    customer_name = cust_obj.name
                if cust_obj and not customer_phone and cust_obj.phone:
                    customer_phone = cust_obj.phone
            elif customer_phone:
                cust_obj, _ = Customer.objects.get_or_create(
                    phone=customer_phone,
                    client=client_user,
                    defaults={"name": customer_name or "Cash Customer"}
                )

            final_customer_name = cust_obj.name if cust_obj else (customer_name or "Cash Customer")
            final_customer_phone = cust_obj.phone if cust_obj else customer_phone

            # Generate collision-free sequential Invoice Number
            prefix = "POS" if is_desktop_environment(request) else "WEB"
            date_str = timezone.now().strftime("%Y%m%d")
            count = Invoice.objects.filter(invoice_number__startswith=f"{prefix}-{date_str}").count() + 1
            inv_number = f"{prefix}-{date_str}-{count:04d}"
            while Invoice.objects.filter(invoice_number=inv_number).exists():
                count += 1
                inv_number = f"{prefix}-{date_str}-{count:04d}"

            # Create Invoice
            invoice = Invoice.objects.create(
                invoice_uuid=uuid.uuid4(),
                invoice_number=inv_number,
                client=client_user,
                branch=branch_obj,
                branch_name=branch_obj.name if branch_obj else "Main Shop Branch",
                customer=cust_obj,
                customer_name=final_customer_name,
                customer_phone=final_customer_phone,
                subtotal=subtotal,
                tax_amount=tax_amount,
                discount_amount=discount_amount,
                grand_total=grand_total,
                paid_amount=paid_amount,
                payment_method=payment_method,
                source="windows_app" if is_desktop_environment(request) else "web_mobile",
                notes=f"Express Bill ({unit_type})",
            )

            # Create line item
            item_name = product.display_name
            InvoiceItem.objects.create(
                invoice=invoice,
                product=product,
                product_name=item_name,
                product_sku=product.sku,
                unit=unit_type,
                unit_price=unit_price,
                quantity=qty,
                tax_percent=product.tax_percent,
                tax_amount=tax_amount,
                discount_percent=Decimal("0.00"),
                total_price=grand_total,
            )

            # Decrement stock & record StockLog
            user_name = request.user.username if request.user.is_authenticated else "Billing Operator"
            old_stock = product.stock_quantity
            new_stock = max(Decimal("0.00"), old_stock - qty)
            product.stock_quantity = new_stock
            product.save(update_fields=["stock_quantity", "updated_at"])

            StockLog.objects.create(
                product=product,
                change_type="BILLING_SALE",
                quantity_change=-qty,
                previous_quantity=old_stock,
                new_quantity=new_stock,
                invoice=invoice,
                notes=f"Quick bill #{inv_number} ({qty} {unit_type})",
                created_by=user_name,
            )

            # Create initial payment record if paid > 0
            if paid_amount > Decimal("0.00"):
                PaymentRecord.objects.create(
                    invoice=invoice,
                    amount=paid_amount,
                    payment_method=payment_method,
                    notes=f"Initial payment on billing via {payment_method}"
                )

            # Log to cloud database
            log_activity(
                request,
                "QUICK_BILL",
                f"Quick bill #{inv_number} created at branch '{invoice.branch_name}' for '{invoice.customer_name}'. Grand Total: ₹{grand_total}, Paid: ₹{paid_amount}, Balance Due: ₹{invoice.balance_amount}"
            )

            messages.success(request, f"Invoice {inv_number} created successfully! Grand Total: ₹{grand_total}")
            return redirect(f"/invoices/{invoice.id}/?autoprint=1")

        except Exception as e:
            messages.error(request, f"Error generating bill: {str(e)}")
            return redirect("billing:dashboard")

    return redirect("billing:dashboard")


# ==========================================
# INVOICES (View, Download PDF, Update Balance)
# ==========================================

def invoice_list(request):
    """
    Invoices page:
    - View receipts, download PDF, update payment (Cash / Online)
    - Filter by customer, view customer aggregate stats (Total Billed, Total Paid, Pending Balance)
    - Apply / Update discounts & offers with automatic recalculation of grand total and balance due
    """
    c_filter = get_client_filter(request)
    invoices = Invoice.objects.filter(c_filter).select_related("customer", "branch").prefetch_related("items", "payments").order_by("-created_at")
    search = request.GET.get("search", "").strip()
    status_filter = request.GET.get("status", "").strip()
    payment_method = request.GET.get("method", "").strip()
    customer_id = request.GET.get("customer", "").strip()

    selected_customer = None
    customer_stats = None

    if customer_id:
        try:
            selected_customer = Customer.objects.filter(c_filter, pk=customer_id).first()
            if selected_customer:
                cust_query = Q(customer=selected_customer)
                if selected_customer.phone:
                    cust_query |= Q(customer_phone=selected_customer.phone)
                invoices = invoices.filter(cust_query)

                all_cust_invs = Invoice.objects.filter(c_filter).filter(cust_query)
                agg = all_cust_invs.aggregate(
                    total_billed=Sum("grand_total"),
                    total_paid=Sum("paid_amount"),
                    total_pending=Sum("balance_amount"),
                    invoice_count=Count("id")
                )
                customer_stats = {
                    "count": agg["invoice_count"] or 0,
                    "total_billed": agg["total_billed"] or Decimal("0.00"),
                    "total_paid": agg["total_paid"] or Decimal("0.00"),
                    "total_pending": agg["total_pending"] or Decimal("0.00"),
                }
        except Exception:
            pass

    if search:
        invoices = invoices.filter(
            Q(invoice_number__icontains=search)
            | Q(customer_name__icontains=search)
            | Q(customer_phone__icontains=search)
        )
    if status_filter:
        invoices = invoices.filter(payment_status=status_filter)
    if payment_method:
        invoices = invoices.filter(payment_method=payment_method)

    customers = Customer.objects.filter(c_filter).order_by("name")

    context = {
        "invoices": invoices[:100],
        "customers": customers,
        "selected_customer": selected_customer,
        "customer_stats": customer_stats,
        "search": search,
        "status_filter": status_filter,
        "payment_method": payment_method,
    }
    return render(request, "billing/invoices.html", context)


def invoice_detail(request, invoice_id):
    c_filter = get_client_filter(request)
    invoice = get_object_or_404(Invoice.objects.filter(c_filter).prefetch_related("items", "payments"), pk=invoice_id)
    company = CompanySettings.get_settings()
    return render(request, "billing/invoice_detail.html", {
        "invoice": invoice,
        "company": company,
    })


def invoice_delete(request, invoice_id):
    """
    Permanently deletes an individual invoice, restores inventory product stock,
    and updates customer balances and dashboard metrics.
    """
    if not request.user.is_authenticated:
        return redirect("billing:login")
    if request.method == "POST":
        c_filter = get_client_filter(request)
        invoice = get_object_or_404(Invoice.objects.filter(c_filter).prefetch_related("items"), pk=invoice_id)
        inv_number = invoice.invoice_number
        inv_uuid = str(invoice.invoice_uuid)

        # Restore inventory stock
        for item in invoice.items.all():
            if item.product:
                Product.objects.filter(pk=item.product.id).update(
                    stock_quantity=F("stock_quantity") + item.quantity
                )

        invoice.items.all().delete()
        invoice.delete()

        log_activity(
            request,
            "INVOICE_DELETE",
            f"User '{request.user.username}' deleted invoice #{inv_number} (UUID: {inv_uuid}). Stock restored."
        )
        messages.success(request, f"Invoice #{inv_number} deleted successfully and product stock restored.")
    return redirect("billing:invoice_list")


def invoice_edit(request, invoice_id):
    """
    Alter / Edit previous customer order & invoice:
    - Modify customer details
    - Add, edit, or remove line items
    - Change quantities (>500 supported), units, and prices
    - Restores previous stock and applies updated stock quantities
    - Automatically recalculates subtotal, taxes, grand total, and balance due
    """
    c_filter = get_client_filter(request)
    invoice = get_object_or_404(Invoice.objects.filter(c_filter).prefetch_related("items"), pk=invoice_id)
    products = Product.objects.filter(c_filter, is_active=True).order_by("name_tamil", "name")
    customers = Customer.objects.filter(c_filter).order_by("name")

    if request.method == "POST":
        customer_id = request.POST.get("customer_id")
        customer_name = request.POST.get("customer_name", "").strip() or "Cash Customer"
        customer_phone = request.POST.get("customer_phone", "").strip()
        payment_method = request.POST.get("payment_method", invoice.payment_method)
        notes = request.POST.get("notes", "").strip()

        product_ids = request.POST.getlist("product_id[]")
        units = request.POST.getlist("unit[]")
        unit_prices = request.POST.getlist("unit_price[]")
        quantities = request.POST.getlist("quantity[]")

        if not product_ids:
            messages.error(request, "The bill must contain at least one product.")
            return redirect("billing:invoice_edit", invoice_id=invoice.id)

        # 1. Restore previous stock for existing items
        user_name = request.user.username if request.user.is_authenticated else "Staff"
        for old_item in invoice.items.all():
            if old_item.product:
                p = old_item.product
                p_old_stock = p.stock_quantity
                p_new_stock = p_old_stock + old_item.quantity
                p.stock_quantity = p_new_stock
                p.save(update_fields=["stock_quantity", "updated_at"])

                StockLog.objects.create(
                    product=p,
                    change_type="BILLING_RESTORE",
                    quantity_change=old_item.quantity,
                    previous_quantity=p_old_stock,
                    new_quantity=p_new_stock,
                    invoice=invoice,
                    notes=f"Restored stock from edited invoice #{invoice.invoice_number}",
                    created_by=user_name,
                )

        # 2. Delete existing items to replace with updated items
        invoice.items.all().delete()

        # 3. Associate Customer
        cust_obj = None
        if customer_id:
            cust_obj = Customer.objects.filter(pk=customer_id).first()
        elif customer_phone:
            cust_obj, _ = Customer.objects.get_or_create(
                phone=customer_phone,
                defaults={"name": customer_name}
            )

        invoice.customer = cust_obj
        invoice.customer_name = cust_obj.name if cust_obj else customer_name
        invoice.customer_phone = cust_obj.phone if cust_obj else customer_phone
        invoice.payment_method = payment_method
        invoice.notes = notes

        # 4. Process updated line items
        subtotal = Decimal("0.00")
        total_tax = Decimal("0.00")

        for i in range(len(product_ids)):
            pid = product_ids[i]
            if not pid:
                continue

            try:
                prod = Product.objects.get(pk=pid)
            except Product.DoesNotExist:
                continue

            qty = parse_decimal(quantities[i] if i < len(quantities) else "1", "1")
            if qty <= 0:
                continue

            unit = units[i] if i < len(units) else prod.unit
            unit_price = parse_decimal(unit_prices[i] if i < len(unit_prices) else str(prod.price), str(prod.price))

            line_sub = unit_price * qty
            line_tax = (line_sub * prod.tax_percent) / Decimal("100.00")
            line_total = line_sub + line_tax

            subtotal += line_sub
            total_tax += line_tax

            InvoiceItem.objects.create(
                invoice=invoice,
                product=prod,
                product_name=prod.display_name,
                product_sku=prod.sku,
                unit=unit,
                unit_price=unit_price,
                quantity=qty,
                tax_percent=prod.tax_percent,
                tax_amount=line_tax,
                discount_percent=Decimal("0.00"),
                total_price=line_total,
            )

            # Decrement new stock
            p_old = prod.stock_quantity
            p_new = max(Decimal("0.00"), p_old - qty)
            prod.stock_quantity = p_new
            prod.save(update_fields=["stock_quantity", "updated_at"])

            StockLog.objects.create(
                product=prod,
                change_type="BILLING_SALE",
                quantity_change=-qty,
                previous_quantity=p_old,
                new_quantity=p_new,
                invoice=invoice,
                notes=f"Updated item in bill #{invoice.invoice_number}",
                created_by=user_name,
            )

        # 5. Update invoice financial figures
        old_total = invoice.grand_total
        invoice.subtotal = subtotal
        invoice.tax_amount = total_tax
        # Keep discount amount bounded
        max_discount = subtotal + total_tax
        if invoice.discount_amount > max_discount:
            invoice.discount_amount = max_discount

        invoice.save()  # Auto updates grand_total, balance_amount, and payment_status

        log_activity(
            request,
            "INVOICE_EDIT",
            f"Altered/edited bill #{invoice.invoice_number} for customer '{invoice.customer_name}'. Grand Total changed from ₹{old_total} to ₹{invoice.grand_total}. Remaining Balance: ₹{invoice.balance_amount}"
        )

        messages.success(
            request,
            f"Invoice #{invoice.invoice_number} updated successfully! New Total: ₹{invoice.grand_total}, Remaining Due: ₹{invoice.balance_amount}"
        )
        return redirect("billing:invoice_detail", invoice_id=invoice.id)

    return render(request, "billing/invoice_edit.html", {
        "invoice": invoice,
        "products": products,
        "customers": customers,
    })


def invoice_download_pdf(request, invoice_id):
    """Generates and serves downloadable PDF receipt with company branding"""
    c_filter = get_client_filter(request)
    invoice = get_object_or_404(Invoice.objects.filter(c_filter).prefetch_related("items"), pk=invoice_id)
    pdf_bytes = generate_invoice_pdf(invoice)

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    filename = f"Receipt_{invoice.invoice_number}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def invoice_update_payment(request, invoice_id):
    """
    Records customer payment (Cash or Online Payment),
    updates paid_amount and recalculates remaining balance due.
    Logs activity directly into the cloud database.
    """
    if request.method == "POST":
        c_filter = get_client_filter(request)
        invoice = get_object_or_404(Invoice.objects.filter(c_filter), pk=invoice_id)
        amount_str = request.POST.get("amount", "0")
        pay_mode = request.POST.get("payment_method", "Cash")
        note = request.POST.get("notes", "").strip()

        try:
            pay_amt = Decimal(amount_str)
            if pay_amt <= 0:
                messages.error(request, "Payment amount must be greater than zero.")
                return redirect("billing:invoice_list")

            # Update paid amount on invoice
            invoice.paid_amount += pay_amt
            invoice.payment_method = pay_mode
            invoice.save()  # Auto updates balance_amount and payment_status

            # Record audit transaction
            PaymentRecord.objects.create(
                invoice=invoice,
                amount=pay_amt,
                payment_method=pay_mode,
                notes=note or f"Payment received via {pay_mode}"
            )

            # Log payment activity to cloud database
            log_activity(
                request,
                "PAYMENT_UPDATE",
                f"Recorded payment of ₹{pay_amt} via {pay_mode} for invoice #{invoice.invoice_number} ({invoice.customer_name}). Remaining Balance: ₹{invoice.balance_amount}"
            )

            messages.success(
                request,
                f"Recorded ₹{pay_amt} via {pay_mode} for {invoice.invoice_number}. Remaining Balance: ₹{invoice.balance_amount}"
            )
        except Exception as e:
            messages.error(request, f"Error updating payment: {str(e)}")

    return redirect("billing:invoice_list")


def invoice_apply_discount(request, invoice_id):
    """
    Applies or updates an offer/discount on an existing invoice.
    Accepts discount_type ('amount' or 'percent'), discount_value, and offer_name.
    Automatically recalculates grand_total and balance_amount in Invoice.save().
    Logs activity directly to the cloud database.
    """
    if request.method == "POST":
        c_filter = get_client_filter(request)
        invoice = get_object_or_404(Invoice.objects.filter(c_filter), pk=invoice_id)
        discount_type = request.POST.get("discount_type", "amount")
        discount_val_str = request.POST.get("discount_value", "0").strip()
        offer_name = request.POST.get("offer_name", "").strip()

        try:
            discount_val = Decimal(discount_val_str)
            if discount_val < 0:
                messages.error(request, "Discount amount cannot be negative.")
                return redirect("billing:invoice_list")

            if discount_type == "percent":
                if discount_val > 100:
                    messages.error(request, "Discount percentage cannot exceed 100%.")
                    return redirect("billing:invoice_list")
                discount_amount = (invoice.subtotal * discount_val) / Decimal("100.00")
            else:
                discount_amount = discount_val

            max_discount = invoice.subtotal + invoice.tax_amount
            if discount_amount > max_discount:
                discount_amount = max_discount

            old_discount = invoice.discount_amount
            old_total = invoice.grand_total

            invoice.discount_amount = discount_amount.quantize(Decimal("0.01"))
            if offer_name:
                clean_notes = (invoice.notes or "").split("[Offer:")[0].strip()
                invoice.notes = f"{clean_notes} [Offer: {offer_name}]".strip() if clean_notes else f"[Offer: {offer_name}]"
            invoice.save()  # Auto updates grand_total, balance_amount, and payment_status

            log_activity(
                request,
                "DISCOUNT_APPLIED",
                f"Updated discount on invoice #{invoice.invoice_number} ({invoice.customer_name}): ₹{old_discount} -> ₹{invoice.discount_amount}. Grand Total: ₹{old_total} -> ₹{invoice.grand_total}, Remaining Balance: ₹{invoice.balance_amount}. Offer: '{offer_name or 'None'}'"
            )

            messages.success(
                request,
                f"Discount/Offer applied to {invoice.invoice_number}! New Total: ₹{invoice.grand_total}, Remaining Balance: ₹{invoice.balance_amount}"
            )
        except Exception as e:
            messages.error(request, f"Error applying discount: {str(e)}")

    return redirect("billing:invoice_list")


# ==========================================
# PRODUCTS (Add, Edit, Update, Remove)
# ==========================================

def product_list(request):
    """
    Products page:
    Add, Edit, Update, and Remove products anytime.
    Bilingual (Tamil primary, English optional).
    Units: KG, Pack, Box, Pcs, Ltr.
    """
    c_filter = get_client_filter(request)
    products = Product.objects.filter(c_filter, is_active=True).order_by("name_tamil", "name")
    search = request.GET.get("search", "").strip()
    category = request.GET.get("category", "").strip()

    if search:
        products = products.filter(
            Q(name_tamil__icontains=search)
            | Q(name__icontains=search)
            | Q(sku__icontains=search)
        )
    if category:
        products = products.filter(category=category)

    categories = Product.objects.filter(c_filter, is_active=True).values_list("category", flat=True).distinct()

    return render(request, "billing/products.html", {
        "products": products,
        "categories": categories,
        "search": search,
        "selected_category": category,
        "unit_choices": Product.UNIT_CHOICES,
    })


def product_add(request):
    """Add a new product with Tamil & English names and Unit choices"""
    if request.method == "POST":
        name_tamil = request.POST.get("name_tamil", "").strip()
        name = request.POST.get("name", "").strip()
        sku = request.POST.get("sku", "").strip()
        category = request.POST.get("category", "General").strip() or "General"
        unit = request.POST.get("unit", "KG").strip() or "KG"
        price = parse_decimal(request.POST.get("price"), "0.00")
        cost_price = parse_decimal(request.POST.get("cost_price"), "0.00")
        tax_percent = parse_decimal(request.POST.get("tax_percent"), "0.00")
        stock = parse_decimal(request.POST.get("stock_quantity"), "0.00")

        if not name_tamil and not name:
            messages.error(request, "Please enter at least a Tamil or English product name.")
            return redirect("billing:product_list")

        if not sku:
            sku = f"SKU-{timezone.now().strftime('%y%m%d%H%M%S')}"

        c_filter = get_client_filter(request)
        if Product.objects.filter(c_filter, sku=sku).exists():
            messages.error(request, f"A product with SKU '{sku}' already exists.")
            return redirect("billing:product_list")

        prod = Product.objects.create(
            name_tamil=name_tamil,
            name=name,
            client=get_client_user(request),
            sku=sku,
            category=category,
            unit=unit,
            price=price,
            cost_price=cost_price,
            tax_percent=tax_percent,
            stock_quantity=stock,
        )
        log_activity(
            request,
            "PRODUCT_ADD",
            f"Added product '{prod.display_name}' (SKU: {prod.sku}, Unit: {prod.unit}, Price: ₹{prod.price}, Stock: {prod.stock_quantity})"
        )
        messages.success(request, f"Product '{name_tamil or name}' added successfully!")

    return redirect("billing:product_list")


def product_edit(request, product_id):
    """Edit / Update product details (Price, Stock, Unit, Names) anytime"""
    c_filter = get_client_filter(request)
    product = get_object_or_404(Product.objects.filter(c_filter), pk=product_id)

    if request.method == "POST":
        product.name_tamil = request.POST.get("name_tamil", product.name_tamil).strip()
        product.name = request.POST.get("name", "").strip()
        product.category = request.POST.get("category", product.category).strip() or product.category
        product.unit = request.POST.get("unit", product.unit).strip() or product.unit
        product.price = parse_decimal(request.POST.get("price"), str(product.price))
        product.cost_price = parse_decimal(request.POST.get("cost_price"), str(product.cost_price))
        product.tax_percent = parse_decimal(request.POST.get("tax_percent"), str(product.tax_percent))
        product.stock_quantity = parse_decimal(request.POST.get("stock_quantity"), str(product.stock_quantity))
        product.save()

        log_activity(
            request,
            "PRODUCT_EDIT",
            f"Updated product '{product.display_name}' (SKU: {product.sku}, Unit: {product.unit}, Price: ₹{product.price}, Stock: {product.stock_quantity})"
        )

        messages.success(request, f"Product '{product.display_name}' updated successfully!")

    return redirect("billing:product_list")


def product_delete(request, product_id):
    """Remove product from catalog"""
    if not request.user.is_authenticated:
        return redirect("billing:login")
    if request.method == "POST":
        c_filter = get_client_filter(request)
        product = get_object_or_404(Product.objects.filter(c_filter), pk=product_id)
        prod_name = product.display_name
        # Soft delete / deactivate so historical invoices remain intact
        product.is_active = False
        product.save(update_fields=["is_active", "updated_at"])

        log_activity(
            request,
            "PRODUCT_DELETE",
            f"Removed/deactivated product '{prod_name}' (SKU: {product.sku})"
        )
        messages.success(request, f"Product '{prod_name}' removed from active inventory.")

    return redirect("billing:product_list")


# ==========================================
# CUSTOMERS (Add & Alter / Edit Customers)
# ==========================================

def customer_add(request):
    """Add a new customer from dashboard or customers modal"""
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        phone = request.POST.get("phone", "").strip()
        email = request.POST.get("email", "").strip()
        address = request.POST.get("address", "").strip()
        gst = request.POST.get("gst_number", "").strip()
        notes = request.POST.get("notes", "").strip()

        if not name:
            messages.error(request, "Customer name is required.")
            return redirect(request.META.get("HTTP_REFERER", "billing:dashboard"))

        c_filter = get_client_filter(request)
        if phone and Customer.objects.filter(c_filter, phone=phone).exists():
            messages.warning(request, f"Customer with phone {phone} already exists.")
        else:
            cust = Customer.objects.create(
                name=name,
                client=get_client_user(request),
                phone=phone,
                email=email,
                address=address,
                gst_number=gst,
                notes=notes,
            )
            log_activity(
                request,
                "CUSTOMER_ADD",
                f"Added customer '{cust.name}' (Phone: {cust.phone or 'N/A'}, GST: {cust.gst_number or 'N/A'})"
            )
            messages.success(request, f"Customer '{name}' added successfully!")

    return redirect(request.META.get("HTTP_REFERER", "billing:dashboard"))


def customer_edit(request, customer_id):
    """Alter / Edit customer details"""
    c_filter = get_client_filter(request)
    customer = get_object_or_404(Customer.objects.filter(c_filter), pk=customer_id)

    if request.method == "POST":
        customer.name = request.POST.get("name", customer.name).strip()
        customer.phone = request.POST.get("phone", customer.phone).strip()
        customer.email = request.POST.get("email", customer.email).strip()
        customer.address = request.POST.get("address", customer.address).strip()
        customer.gst_number = request.POST.get("gst_number", customer.gst_number).strip()
        customer.notes = request.POST.get("notes", customer.notes).strip()
        customer.save()

        log_activity(
            request,
            "CUSTOMER_EDIT",
            f"Updated customer details for '{customer.name}' (Phone: {customer.phone or 'N/A'}, GST: {customer.gst_number or 'N/A'})"
        )
        messages.success(request, f"Customer '{customer.name}' updated successfully!")

    return redirect(request.META.get("HTTP_REFERER", "billing:dashboard"))


# ==========================================
# ADMIN PANEL (Users Management & Activity Log)
# STRICTLY ADMIN-ONLY (@admin_required)
# ==========================================

@admin_required
def admin_panel(request):
    """
    Dedicated Admin Panel:
    1. Client Accounts Management (Add multiple clients, upload photo/avatar, phone, email, password, delete client)
    2. Shop Branches Management (Add, edit, delete branches before billing)
    3. Admin Users Management (Single device mode)
    4. Active Devices Monitor (Revoke/disconnect devices)
    5. Cloud Activity Logs (Search, filter by action or user, export as TXT)
    6. Company Profile & Receipt Branding
    Client users are strictly blocked and redirected by @admin_required and SessionSecurityMiddleware.
    """
    users = User.objects.select_related("profile").prefetch_related("active_sessions").order_by("username")
    clients = [u for u in users if getattr(u, "profile", None) and u.profile.role == "client"]
    for c in clients:
        c.invoice_count = Invoice.objects.filter(client=c).count()
        c.customer_count = Customer.objects.filter(client=c).count()
        c.product_count = Product.objects.filter(client=c).count()
        c.sales_total = Invoice.objects.filter(client=c).aggregate(Sum("grand_total"))["grand_total__sum"] or Decimal("0.00")
    admin_users = [u for u in users if u.is_superuser or (getattr(u, "profile", None) and u.profile.role == "admin")]

    active_device_sessions = ActiveUserSession.objects.select_related("user", "user__profile").order_by("-last_activity")
    registered_devices = RegisteredDevice.objects.select_related("user", "user__profile").order_by("-last_login")

    log_qs = ActivityLog.objects.select_related("user").order_by("-created_at")
    action_filter = request.GET.get("action", "").strip()
    user_filter = request.GET.get("log_user", "").strip()
    search_log = request.GET.get("search_log", "").strip()

    if action_filter:
        log_qs = log_qs.filter(action_type=action_filter)
    if user_filter:
        log_qs = log_qs.filter(username__icontains=user_filter)
    if search_log:
        log_qs = log_qs.filter(
            Q(description__icontains=search_log) |
            Q(username__icontains=search_log) |
            Q(action_type__icontains=search_log)
        )

    action_types = ActivityLog.objects.values_list("action_type", flat=True).distinct()
    branches = Branch.objects.order_by("-is_default", "name")

    all_customers = Customer.objects.select_related("client").order_by("-created_at")
    for cust in all_customers:
        cust.bill_count = Invoice.objects.filter(customer=cust).count()
        cust.total_spend = Invoice.objects.filter(customer=cust).aggregate(Sum("grand_total"))["grand_total__sum"] or Decimal("0.00")

    context = {
        "admin_users": admin_users,
        "clients": clients,
        "branches": branches,
        "all_customers": all_customers,
        "active_device_sessions": active_device_sessions,
        "registered_devices": registered_devices,
        "activity_logs": log_qs[:200],
        "total_log_count": ActivityLog.objects.count(),
        "action_types": action_types,
        "action_filter": action_filter,
        "user_filter": user_filter,
        "search_log": search_log,
        "company": CompanySettings.get_settings(),
        "software_updates": SoftwareUpdate.objects.order_by("-created_at"),
    }
    return render(request, "billing/admin_panel.html", context)


@admin_required
def admin_user_create(request):
    """
    Admin adds a new user account (Client or Admin).
    Only Admin can create clients or admins.
    Supports username, password, business type (Grocery / Mobile Shop),
    device limit, shop details, bank details, and logo/avatar.
    """
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        shop_name = request.POST.get("shop_name", "").strip()
        shop_address = request.POST.get("shop_address", "").strip()
        business_type = request.POST.get("business_type", "grocery").strip()
        email = request.POST.get("email", "").strip()
        phone = request.POST.get("phone", "").strip()
        role = request.POST.get("role", "client").strip()
        access_mode = request.POST.get("access_mode", "online_offline").strip()
        bank_name = request.POST.get("bank_name", "").strip()
        account_number = request.POST.get("account_number", "").strip()
        ifsc_code = request.POST.get("ifsc_code", "").strip()
        device_limit_str = request.POST.get("device_limit", "5").strip()
        password = request.POST.get("password", "")
        password_confirm = request.POST.get("password_confirm", "")

        try:
            device_limit = int(device_limit_str)
        except Exception:
            device_limit = 5

        if not username or not password:
            messages.error(request, "Username and password are required.")
            return redirect("billing:admin_panel")

        if password != password_confirm:
            messages.error(request, "Passwords do not match.")
            return redirect("billing:admin_panel")

        if User.objects.filter(username=username).exists():
            messages.error(request, f"Username '{username}' already exists. Please choose a different username.")
            return redirect("billing:admin_panel")

        is_admin_role = (role == "admin")
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            is_staff=is_admin_role,
        )

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.phone = phone
        profile.shop_name = shop_name
        profile.shop_address = shop_address
        profile.business_type = business_type
        profile.access_mode = access_mode
        profile.device_limit = 1 if is_admin_role else device_limit
        profile.bank_name = bank_name
        profile.account_number = account_number
        profile.ifsc_code = ifsc_code
        profile.role = role
        profile.initial_password = password

        # Handle Client Profile Image (img) / Shop Logo upload
        if "client_image" in request.FILES:
            img_file = request.FILES["client_image"]
            try:
                img_data = img_file.read()
                b64_str = base64.b64encode(img_data).decode("utf-8")
                mime_type = img_file.content_type or "image/png"
                data_url = f"data:{mime_type};base64,{b64_str}"
                profile.avatar_base64 = data_url
                profile.shop_logo_base64 = data_url
            except Exception:
                pass

        profile.save()

        device_info_str = "1 Device Allowed (Single Device Mode)" if is_admin_role else f"Up to {profile.device_limit} Concurrent Devices Allowed"
        b_type_display = "Mobile & Computer Shop" if business_type == "mobile_computer" else "Grocery Shop"
        log_activity(
            request,
            "USER_CREATE",
            f"Created new {role.upper()} account '{username}' ({b_type_display}, Shop: {shop_name or 'N/A'}, Phone: {phone}, {device_info_str})"
        )
        messages.success(request, f"Client/User '{username}' created successfully! ({b_type_display}, {device_info_str})")

    return redirect("billing:admin_panel")


@admin_required
def admin_user_edit(request, user_id):
    """
    Admin updates a user's details (Name, Shop Name, Address, Business Type, Device Limit, Bank Details, Phone, Email, Role, Active Status, Profile Image).
    Client users cannot edit their own or any other user's details.
    """
    user = get_object_or_404(User, pk=user_id)

    if request.method == "POST":
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        shop_name = request.POST.get("shop_name", "").strip()
        shop_address = request.POST.get("shop_address", "").strip()
        business_type = request.POST.get("business_type", "grocery").strip()
        email = request.POST.get("email", "").strip()
        phone = request.POST.get("phone", "").strip()
        role = request.POST.get("role", "client").strip()
        access_mode = request.POST.get("access_mode", "").strip()
        bank_name = request.POST.get("bank_name", "").strip()
        account_number = request.POST.get("account_number", "").strip()
        ifsc_code = request.POST.get("ifsc_code", "").strip()
        device_limit_str = request.POST.get("device_limit", "5").strip()
        is_active = request.POST.get("is_active") == "1"

        try:
            device_limit = int(device_limit_str)
        except Exception:
            device_limit = 5

        user.first_name = first_name
        user.last_name = last_name
        user.email = email
        user.is_active = is_active
        if role == "admin":
            user.is_staff = True
        else:
            if not user.is_superuser:
                user.is_staff = False
        user.save()

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.phone = phone
        profile.shop_name = shop_name
        profile.shop_address = shop_address
        profile.business_type = business_type
        if access_mode in dict(UserProfile.ACCESS_MODES):
            profile.access_mode = access_mode
        profile.device_limit = 1 if role == "admin" else device_limit
        profile.bank_name = bank_name
        profile.account_number = account_number
        profile.ifsc_code = ifsc_code
        profile.role = role

        # Update avatar/logo if uploaded
        if "client_image" in request.FILES:
            img_file = request.FILES["client_image"]
            try:
                img_data = img_file.read()
                b64_str = base64.b64encode(img_data).decode("utf-8")
                mime_type = img_file.content_type or "image/png"
                data_url = f"data:{mime_type};base64,{b64_str}"
                profile.avatar_base64 = data_url
                profile.shop_logo_base64 = data_url
            except Exception:
                pass

        profile.save()

        log_activity(
            request,
            "USER_EDIT",
            f"Admin '{request.user.username}' updated user account '{user.username}' (Shop: {profile.shop_name}, Business: {profile.business_type}, Limit: {profile.device_limit} devices)"
        )
        messages.success(request, f"User account '{user.username}' updated successfully!")

    return redirect("billing:admin_panel")


@admin_required
def admin_client_delete(request, user_id):
    """
    Admin deletes a client account.
    Normal client users cannot delete clients.
    All active device sessions for this client are terminated immediately.
    """
    if request.method == "POST":
        target_user = get_object_or_404(User, pk=user_id)
        if target_user.id == request.user.id:
            messages.error(request, "Security violation: You cannot delete your currently logged-in administrator account.")
            return redirect("billing:admin_panel")
        if target_user.is_superuser:
            messages.error(request, "Security violation: Superuser accounts cannot be deleted.")
            return redirect("billing:admin_panel")

        uname = target_user.username
        # Invalidate all active device sessions for this user so they are immediately kicked out
        ActiveUserSession.objects.filter(user=target_user).delete()
        target_user.delete()

        log_activity(
            request,
            "USER_DELETE",
            f"Admin '{request.user.username}' deleted client account '{uname}' and terminated all associated device sessions."
        )
        messages.success(request, f"Client '{uname}' and all active device sessions deleted successfully.")

    return redirect("billing:admin_panel")


@admin_required
def admin_revoke_device_session(request, session_id):
    """
    Admin manually disconnects/revokes an active device session.
    """
    if request.method == "POST":
        dev_session = get_object_or_404(ActiveUserSession, pk=session_id)
        u_name = dev_session.user.username
        d_info = dev_session.device_info
        ip_addr = dev_session.ip_address

        dev_session.delete()

        log_activity(
            request,
            "SESSION_REVOKE",
            f"Admin revoked device session for user '{u_name}' ({d_info[:35]} | IP: {ip_addr})"
        )
        messages.success(request, f"Disconnected active device session for '{u_name}'.")

    return redirect("billing:admin_panel")


@admin_required
def admin_revoke_registered_device(request, device_id):
    """
    Admin revokes an approved device slot to allow a new or replacement device.
    Frees up the device quota slot immediately.
    """
    if request.method == "POST":
        device = get_object_or_404(RegisteredDevice, pk=device_id)
        u_name = device.user.username
        d_name = device.device_name
        d_id = device.device_id

        # Disconnect any active sessions for this device
        ActiveUserSession.objects.filter(user=device.user).delete()
        device.delete()

        log_activity(
            request,
            "DEVICE_REVOKE",
            f"Admin revoked approved device '{d_name}' ({d_id[:12]}) for client '{u_name}'."
        )
        messages.success(request, f"Approved device '{d_name}' removed successfully. Slot is now free for a new device.")

    return redirect("billing:admin_panel")


@admin_required
def admin_user_password_change(request, user_id):
    """
    Admin resets or updates a user's password.
    """
    user = get_object_or_404(User, pk=user_id)

    if request.method == "POST":
        new_password = request.POST.get("new_password", "")
        new_password_confirm = request.POST.get("new_password_confirm", "")

        if not new_password:
            messages.error(request, "Password cannot be empty.")
            return redirect("billing:admin_panel")

        if new_password != new_password_confirm:
            messages.error(request, "New passwords do not match.")
            return redirect("billing:admin_panel")

        user.set_password(new_password)
        user.save()

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.initial_password = new_password
        profile.save()

        log_activity(
            request,
            "PASSWORD_CHANGE",
            f"Admin reset/changed password for user '{user.username}'"
        )
        messages.success(request, f"Password for user '{user.username}' changed successfully!")

    return redirect("billing:admin_panel")


@admin_required
def admin_wipe_client_database(request, client_id):
    """
    Admin wipes all database records (Invoices, Customers, Products, Purchases, Branches)
    for a specific client account, without deleting their user login credentials.
    """
    if request.method == "POST":
        target_client = get_object_or_404(User, pk=client_id)
        if target_client.is_superuser or (hasattr(target_client, "profile") and target_client.profile.role == "admin"):
            messages.error(request, "Security violation: Cannot wipe administrator data.")
            return redirect("billing:admin_panel")

        u_name = target_client.username
        inv_count = Invoice.objects.filter(client=target_client).count()
        cust_count = Customer.objects.filter(client=target_client).count()
        prod_count = Product.objects.filter(client=target_client).count()

        Invoice.objects.filter(client=target_client).delete()
        Customer.objects.filter(client=target_client).delete()
        Product.objects.filter(client=target_client).delete()
        Purchase.objects.filter(client=target_client).delete()
        Branch.objects.filter(client=target_client).delete()

        log_activity(
            request,
            "DATA_CLEANUP",
            f"Admin '{request.user.username}' wiped database for client '{u_name}': {inv_count} bills, {cust_count} customers, {prod_count} products deleted."
        )
        messages.success(
            request,
            f"Successfully wiped database for client '{u_name}'! ({inv_count} bills, {cust_count} customers, {prod_count} products cleared)."
        )

    return redirect("billing:admin_panel")


def client_delete_data(request):
    """
    Shop Data Deletion & Cleanup Tool:
    Allows deleting:
    1. Particular Date (single_date): Delete all bills issued on a specific single date
    2. Date to Date Range (date_range): Delete all bills between start_date and end_date
    3. Particular Customer (customer): Delete bills for customer or customer + all bills
    4. All Invoices (all_invoices): Delete all store bills & reset dashboard sales to 0
    5. Full Store Reset (wipe_all): Purge all bills and all customers
    Automatically restores inventory stock and propagates deletions to the desktop app.
    """
    if not request.user.is_authenticated:
        return redirect("billing:login")

    redirect_url = request.POST.get("next_url") or request.META.get("HTTP_REFERER") or "billing:dashboard"

    if request.method == "POST":
        delete_type = request.POST.get("delete_type", "").strip()
        c_filter = get_client_filter(request)
        user_name = request.user.username

        def _restore_and_delete_invoices(qs):
            for inv in qs.prefetch_related("items"):
                for item in inv.items.all():
                    if item.product:
                        Product.objects.filter(pk=item.product.id).update(
                            stock_quantity=F("stock_quantity") + item.quantity
                        )
                inv.items.all().delete()
            count = qs.count()
            qs.delete()
            return count

        if delete_type == "single_date":
            single_date_str = request.POST.get("single_date", "").strip()
            if not single_date_str:
                messages.error(request, "Please choose a specific date to delete records.")
                return redirect(redirect_url)

            try:
                target_date = datetime.strptime(single_date_str, "%Y-%m-%d").date()
            except Exception as e:
                messages.error(request, f"Invalid date format: {e}")
                return redirect(redirect_url)

            invoices = Invoice.objects.filter(c_filter, created_at__date=target_date)
            inv_count = _restore_and_delete_invoices(invoices)

            log_activity(
                request,
                "DATA_CLEANUP",
                f"User '{user_name}' deleted {inv_count} invoice(s) for specific date {single_date_str}. Stock restored."
            )
            messages.success(request, f"Successfully deleted {inv_count} invoice(s) for date {single_date_str}. Inventory stock restored.")

        elif delete_type == "date_range":
            start_date_str = request.POST.get("start_date", "").strip()
            end_date_str = request.POST.get("end_date", "").strip()

            if not start_date_str or not end_date_str:
                messages.error(request, "Please specify both Start Date and End Date.")
                return redirect(redirect_url)

            try:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            except Exception as e:
                messages.error(request, f"Invalid date format: {e}")
                return redirect(redirect_url)

            if start_date > end_date:
                messages.error(request, "Start date cannot be after end date.")
                return redirect(redirect_url)

            invoices = Invoice.objects.filter(
                c_filter,
                created_at__date__gte=start_date,
                created_at__date__lte=end_date
            )
            inv_count = _restore_and_delete_invoices(invoices)

            log_activity(
                request,
                "DATA_CLEANUP",
                f"User '{user_name}' deleted {inv_count} invoice(s) between {start_date_str} and {end_date_str}. Stock restored."
            )
            messages.success(request, f"Successfully deleted {inv_count} invoice(s) from {start_date_str} to {end_date_str}. Stock restored.")

        elif delete_type == "customer":
            customer_id = request.POST.get("customer_id", "").strip()
            delete_customer_profile = request.POST.get("delete_customer_profile") == "1"

            if not customer_id:
                messages.error(request, "Please select a customer to delete data.")
                return redirect(redirect_url)

            customer = Customer.objects.filter(c_filter, pk=customer_id).first()
            if not customer:
                messages.error(request, "Customer not found or unauthorized.")
                return redirect(redirect_url)

            cust_name = customer.name
            invoices = Invoice.objects.filter(c_filter, customer=customer)
            inv_count = _restore_and_delete_invoices(invoices)

            if delete_customer_profile:
                customer.delete()
                log_activity(
                    request,
                    "DATA_CLEANUP",
                    f"User '{user_name}' deleted customer '{cust_name}' and all {inv_count} associated invoice(s)."
                )
                messages.success(request, f"Successfully deleted customer '{cust_name}' and {inv_count} invoice(s). Dashboard updated.")
            else:
                log_activity(
                    request,
                    "DATA_CLEANUP",
                    f"User '{user_name}' deleted {inv_count} invoice(s) for customer '{cust_name}'. Pending balance cleared."
                )
                messages.success(request, f"Successfully deleted {inv_count} bill(s) for '{cust_name}'. Customer balance reset to ₹0.00. Dashboard updated.")

        elif delete_type == "all_invoices":
            invoices = Invoice.objects.filter(c_filter)
            inv_count = _restore_and_delete_invoices(invoices)
            log_activity(
                request,
                "DATA_CLEANUP",
                f"User '{user_name}' deleted all {inv_count} store invoices. Stock restored."
            )
            messages.success(request, f"Successfully deleted all {inv_count} store invoices. Dashboard and sales figures reset to ₹0.00.")

        elif delete_type == "wipe_all":
            invoices = Invoice.objects.filter(c_filter)
            inv_count = _restore_and_delete_invoices(invoices)
            customers = Customer.objects.filter(c_filter)
            cust_count = customers.count()
            customers.delete()
            log_activity(
                request,
                "DATA_CLEANUP",
                f"User '{user_name}' performed full data cleanup: deleted {inv_count} invoices and {cust_count} customers."
            )
            messages.success(request, f"Full store data reset completed: purged {inv_count} invoices and {cust_count} customers.")

        else:
            messages.error(request, "Invalid delete action specified.")

    return redirect(redirect_url)


@admin_required
def admin_export_activity_log_txt(request):
    """
    Exports the cloud database activity log as a clean, formatted TXT file.
    The primary store is the database; this serves on-demand text download for audits.
    """
    logs = ActivityLog.objects.order_by("-created_at")

    lines = []
    lines.append("=" * 105)
    lines.append("SMART BILLING POS - CLOUD AUDIT & ACTIVITY LOG REPORT")
    lines.append("=" * 105)
    lines.append(f"Exported At : {timezone.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"Exported By : {request.user.username} ({request.user.get_full_name() or 'Administrator'})")
    lines.append(f"Total Logs  : {logs.count()}")
    lines.append("=" * 105)
    lines.append("")
    lines.append(f"{'TIMESTAMP':<20} | {'ACTION':<18} | {'USERNAME':<15} | {'IP ADDRESS':<16} | {'DETAILS'}")
    lines.append("-" * 105)

    for item in logs:
        ts = item.created_at.strftime("%Y-%m-%d %H:%M:%S")
        act = (item.action_type or "")[:18]
        uname = (item.username or "anonymous")[:15]
        ip = (item.ip_address or "-")[:16]
        desc = item.description or ""
        lines.append(f"{ts:<20} | {act:<18} | {uname:<15} | {ip:<16} | {desc}")

    lines.append("-" * 105)
    lines.append("END OF LOG REPORT")
    lines.append("=" * 105)

    content = "\r\n".join(lines)
    response = HttpResponse(content, content_type="text/plain; charset=utf-8")
    now_str = timezone.now().strftime("%Y%m%d_%H%M%S")
    response["Content-Disposition"] = f'attachment; filename="SmartBillingPOS_Activity_Log_{now_str}.txt"'

    log_activity(request, "LOG_EXPORT", f"Exported {logs.count()} activity log entries to TXT file")
    return response


# ==========================================
# CUSTOMER STATEMENTS (Date Range & Ledger)
# ==========================================

def get_customer_statement_data(customer, start_date, end_date):
    """
    Builds the financial statement ledger for a customer in a date range:
    - Calculates Opening Balance before start_date
    - Aggregates Invoices and PaymentRecords during the period
    - Computes running balances and Net Pending Due
    """
    cust_query = Q(customer=customer)
    if customer.phone:
        cust_query |= Q(customer_phone=customer.phone)

    # 1. Opening Balance prior to start_date
    prior_invoices = Invoice.objects.filter(cust_query, created_at__date__lt=start_date)
    prior_billed = prior_invoices.aggregate(Sum("grand_total"))["grand_total__sum"] or Decimal("0.00")
    prior_paid = prior_invoices.aggregate(Sum("paid_amount"))["paid_amount__sum"] or Decimal("0.00")
    opening_balance = max(Decimal("0.00"), prior_billed - prior_paid)

    # 2. Invoices in date range
    range_invoices = Invoice.objects.filter(
        cust_query,
        created_at__date__gte=start_date,
        created_at__date__lte=end_date
    ).prefetch_related("items").order_by("created_at")

    # 3. Payments in date range
    range_payments = PaymentRecord.objects.filter(
        invoice__in=Invoice.objects.filter(cust_query),
        created_at__date__gte=start_date,
        created_at__date__lte=end_date
    ).select_related("invoice").order_by("created_at")

    # 4. Merge into chronological ledger
    events = []
    for inv in range_invoices:
        events.append({
            "date": inv.created_at,
            "type": "INVOICE",
            "ref": inv.invoice_number,
            "details": f"Bill ({inv.items.count()} items)",
            "debit": inv.grand_total,
            "credit": Decimal("0.00"),
        })

    for pay in range_payments:
        events.append({
            "date": pay.created_at,
            "type": "PAYMENT",
            "ref": pay.invoice.invoice_number if pay.invoice else "Direct Payment",
            "details": f"{pay.payment_method} {pay.notes or ''}".strip(),
            "debit": Decimal("0.00"),
            "credit": pay.amount,
        })

    events.sort(key=lambda x: x["date"])

    transactions = []
    total_billed = Decimal("0.00")
    total_paid = Decimal("0.00")
    running_balance = opening_balance

    for ev in events:
        total_billed += ev["debit"]
        total_paid += ev["credit"]
        running_balance += (ev["debit"] - ev["credit"])
        ev["balance"] = max(Decimal("0.00"), running_balance)
        transactions.append(ev)

    summary = {
        "opening_balance": opening_balance,
        "total_billed": total_billed,
        "total_paid": total_paid,
        "net_pending": max(Decimal("0.00"), running_balance),
        "invoice_count": range_invoices.count(),
    }
    return transactions, summary


def customer_statement_view(request, customer_id):
    """
    Renders customer account statement for selected date range (or default last 45 days)
    """
    c_filter = get_client_filter(request)
    customer = get_object_or_404(Customer.objects.filter(c_filter), pk=customer_id)
    today = timezone.now().date()
    default_start = today - timedelta(days=45)

    start_str = request.GET.get("start_date", "").strip()
    end_str = request.GET.get("end_date", "").strip()

    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").date() if start_str else default_start
    except Exception:
        start_date = default_start

    try:
        end_date = datetime.strptime(end_str, "%Y-%m-%d").date() if end_str else today
    except Exception:
        end_date = today

    transactions, summary = get_customer_statement_data(customer, start_date, end_date)
    company = CompanySettings.get_settings()

    return render(request, "billing/customer_statement.html", {
        "customer": customer,
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "transactions": transactions,
        "summary": summary,
        "company": company,
    })


def customer_statement_pdf(request, customer_id):
    """
    Generates and downloads the Customer Account Statement as PDF
    """
    c_filter = get_client_filter(request)
    customer = get_object_or_404(Customer.objects.filter(c_filter), pk=customer_id)
    today = timezone.now().date()
    default_start = today - timedelta(days=45)

    start_str = request.GET.get("start_date", "").strip()
    end_str = request.GET.get("end_date", "").strip()

    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").date() if start_str else default_start
    except Exception:
        start_date = default_start

    try:
        end_date = datetime.strptime(end_str, "%Y-%m-%d").date() if end_str else today
    except Exception:
        end_date = today

    transactions, summary = get_customer_statement_data(customer, start_date, end_date)
    pdf_bytes = generate_statement_pdf(customer, start_date, end_date, transactions, summary)

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    safe_name = "".join(c for c in customer.name if c.isalnum() or c in (" ", "_")).strip().replace(" ", "_")
    filename = f"Statement_{safe_name}_{start_date}_{end_date}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ==========================================
# COMPANY PROFILE & 45-DAY DATA RETENTION
# ==========================================

@admin_required
def company_settings_update(request):
    """
    Admin updates Company Profile, Address, Contact, Logo and Watermark images.
    Converts image uploads to Base64 so they work offline and in standalone EXE seamlessly.
    """
    if request.method == "POST":
        company = CompanySettings.get_settings()
        company.company_name = request.POST.get("company_name", company.company_name).strip() or company.company_name
        company.address = request.POST.get("address", company.address).strip()
        company.phone = request.POST.get("phone", company.phone).strip()
        company.email = request.POST.get("email", company.email).strip()
        company.gst_number = request.POST.get("gst_number", company.gst_number).strip()
        try:
            company.auto_delete_days = int(request.POST.get("auto_delete_days", company.auto_delete_days or 45))
        except Exception:
            company.auto_delete_days = 45

        # Handle Logo File Upload -> Convert to portable Base64 Data URL
        logo_file = request.FILES.get("logo_file")
        if logo_file:
            content_type = logo_file.content_type or "image/png"
            b64_data = base64.b64encode(logo_file.read()).decode("utf-8")
            company.logo_base64 = f"data:{content_type};base64,{b64_data}"

        # Handle Watermark File Upload -> Convert to portable Base64 Data URL
        watermark_file = request.FILES.get("watermark_file")
        if watermark_file:
            content_type = watermark_file.content_type or "image/png"
            b64_data = base64.b64encode(watermark_file.read()).decode("utf-8")
            company.watermark_base64 = f"data:{content_type};base64,{b64_data}"

        company.save()

        log_activity(
            request,
            "COMPANY_UPDATE",
            f"Updated Company Profile & Branding: '{company.company_name}', Logo: {'Uploaded' if company.logo_base64 else 'Default'}, Watermark: {'Uploaded' if company.watermark_base64 else 'Default'}"
        )
        messages.success(request, f"Company branding & receipt profile updated successfully!")

    return redirect("billing:admin_panel")


@admin_required
def run_45day_purge(request):
    """
    Admin triggers 45-day automatic customer data cleanup.
    """
    company = CompanySettings.get_settings()
    days = company.auto_delete_days or 45
    purged_count = purge_old_customer_data(days=days)
    log_activity(request, "DATA_CLEANUP", f"Admin triggered manual {days}-day data purge. Cleaned {purged_count} records.")
    messages.success(request, f"45-Day Cleanup complete: Purged {purged_count} old customer invoice records created more than {days} days ago.")
    return redirect("billing:admin_panel")


# ==========================================
# STOCK MANAGEMENT (Real-time Stock, Inward, Adjustment, Audit Ledger)
# ==========================================

def stock_management(request):
    """
    Stock Management section:
    - Real-time stock levels of all products
    - Inward Restock form
    - Manual stock adjustments
    - Audit ledger of all stock changes (sales, restocks, manual adjustments)
    - Low stock alerts
    - Export inventory to CSV
    """
    c_filter = get_client_filter(request)
    search = request.GET.get("search", "").strip()
    category = request.GET.get("category", "").strip()
    stock_status = request.GET.get("status", "").strip()

    products_qs = Product.objects.filter(c_filter, is_active=True).order_by("name_tamil", "name")

    if search:
        products_qs = products_qs.filter(
            Q(name_tamil__icontains=search) |
            Q(name__icontains=search) |
            Q(sku__icontains=search)
        )
    if category:
        products_qs = products_qs.filter(category=category)
    if stock_status == "low":
        products_qs = products_qs.filter(stock_quantity__lte=5, stock_quantity__gt=0)
    elif stock_status == "out":
        products_qs = products_qs.filter(stock_quantity__lte=0)
    elif stock_status == "ok":
        products_qs = products_qs.filter(stock_quantity__gt=5)

    all_active = Product.objects.filter(c_filter, is_active=True)
    total_sku_count = all_active.count()
    low_stock_count = all_active.filter(stock_quantity__lte=5, stock_quantity__gt=0).count()
    out_of_stock_count = all_active.filter(stock_quantity__lte=0).count()

    total_inventory_value = all_active.aggregate(
        val=Sum(F("cost_price") * F("stock_quantity"))
    )["val"] or Decimal("0.00")

    stock_logs = StockLog.objects.filter(product__in=all_active).select_related("product", "invoice").order_by("-created_at")[:100]
    categories = Product.objects.filter(c_filter, is_active=True).values_list("category", flat=True).distinct()

    return render(request, "billing/stock_management.html", {
        "products": products_qs,
        "total_sku_count": total_sku_count,
        "low_stock_count": low_stock_count,
        "out_of_stock_count": out_of_stock_count,
        "total_inventory_value": total_inventory_value,
        "stock_logs": stock_logs,
        "categories": categories,
        "search": search,
        "selected_category": category,
        "stock_status": stock_status,
        "all_products": all_active.order_by("name_tamil", "name"),
    })


def stock_add_inward(request):
    """
    Adds inward stock / restock for a product.
    Updates stock_quantity, records StockLog and ActivityLog.
    """
    if request.method == "POST":
        c_filter = get_client_filter(request)
        product_id = request.POST.get("product_id")
        qty_str = request.POST.get("quantity", "0").strip()
        supplier = request.POST.get("supplier_name", "").strip()
        cost_str = request.POST.get("cost_price", "").strip()
        notes = request.POST.get("notes", "").strip()

        try:
            qty = parse_decimal(qty_str, "0")
            if qty <= 0:
                messages.error(request, "Inward quantity must be greater than zero.")
                return redirect("billing:stock_management")

            product = get_object_or_404(Product.objects.filter(c_filter), pk=product_id)
            old_qty = product.stock_quantity
            new_qty = old_qty + qty
            product.stock_quantity = new_qty

            if cost_str:
                new_cost = parse_decimal(cost_str, str(product.cost_price))
                if new_cost > 0:
                    product.cost_price = new_cost

            product.save()

            StockLog.objects.create(
                product=product,
                change_type="RESTOCK",
                quantity_change=qty,
                previous_quantity=old_qty,
                new_quantity=new_qty,
                notes=f"Supplier: {supplier or 'N/A'}. {notes}".strip(),
                created_by=request.user.username if request.user.is_authenticated else "Staff",
            )

            if product.cost_price > 0:
                p_count = Purchase.objects.count() + 1
                Purchase.objects.create(
                    purchase_number=f"PO-{timezone.now().strftime('%Y%m%d')}-{p_count:04d}",
                    client=get_client_user(request),
                    supplier_name=supplier or "Stock Inward",
                    total_amount=product.cost_price * qty,
                    payment_status="Paid",
                    notes=f"Inward restock of {product.display_name} ({qty} {product.unit})",
                )

            log_activity(
                request,
                "STOCK_ADD",
                f"Added {qty} {product.unit} to '{product.display_name}'. Stock: {old_qty} -> {new_qty}. Supplier: {supplier or 'N/A'}"
            )

            messages.success(request, f"Added {qty} {product.unit} to '{product.display_name}'! New stock: {new_qty} {product.unit}")
        except Exception as e:
            messages.error(request, f"Error adding stock: {str(e)}")

    return redirect("billing:stock_management")


def stock_adjust_quantity(request, product_id):
    """
    Manually modifies or adjusts stock quantity for a product.
    Supports set, add, or subtract.
    """
    c_filter = get_client_filter(request)
    product = get_object_or_404(Product.objects.filter(c_filter), pk=product_id)

    if request.method == "POST":
        action = request.POST.get("adjustment_type", "set")
        val_str = request.POST.get("quantity", "0").strip()
        reason = request.POST.get("reason", "").strip()

        try:
            val = parse_decimal(val_str, "0")
            old_qty = product.stock_quantity

            if action == "set":
                new_qty = max(Decimal("0.00"), val)
                change = new_qty - old_qty
            elif action == "add":
                change = val
                new_qty = old_qty + change
            elif action == "subtract":
                change = -val
                new_qty = max(Decimal("0.00"), old_qty + change)
            else:
                new_qty = val
                change = new_qty - old_qty

            product.stock_quantity = new_qty
            product.save(update_fields=["stock_quantity", "updated_at"])

            StockLog.objects.create(
                product=product,
                change_type="MANUAL_ADJUSTMENT",
                quantity_change=change,
                previous_quantity=old_qty,
                new_quantity=new_qty,
                notes=f"Adjustment ({action}): {reason or 'Physical stock count verified'}",
                created_by=request.user.username if request.user.is_authenticated else "Staff",
            )

            log_activity(
                request,
                "STOCK_UPDATE",
                f"Adjusted stock for '{product.display_name}': {old_qty} -> {new_qty} ({change:+}). Reason: {reason or 'N/A'}"
            )

            messages.success(request, f"Stock for '{product.display_name}' updated to {new_qty} {product.unit}!")
        except Exception as e:
            messages.error(request, f"Error adjusting stock: {str(e)}")

    return redirect("billing:stock_management")


def stock_export_csv(request):
    """Exports active inventory list as a CSV file"""
    c_filter = get_client_filter(request)
    products = Product.objects.filter(c_filter, is_active=True).order_by("name_tamil", "name")

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    now_str = timezone.now().strftime("%Y%m%d_%H%M%S")
    response["Content-Disposition"] = f'attachment; filename="SmartBilling_Stock_Inventory_{now_str}.csv"'
    response.write("\ufeff")

    writer = csv.writer(response)
    writer.writerow([
        "Product SKU", "Tamil Name", "English Name", "Category",
        "Unit", "Selling Price (INR)", "Purchase Cost (INR)",
        "Stock Quantity", "Total Stock Value (INR)", "Status"
    ])

    for p in products:
        status = "Out of Stock" if p.stock_quantity <= 0 else ("Low Stock" if p.stock_quantity <= 5 else "In Stock")
        stock_val = (p.cost_price * p.stock_quantity).quantize(Decimal("0.01"))
        writer.writerow([
            p.sku,
            p.name_tamil,
            p.name,
            p.category,
            p.unit,
            f"{p.price:.2f}",
            f"{p.cost_price:.2f}",
            f"{p.stock_quantity:.2f}",
            f"{stock_val:.2f}",
            status
        ])

    return response


# ==========================================
# OWN SHOP BILLS (Comprehensive Shop Bills Ledger)
# ==========================================

def shop_bills(request):
    """
    Own Shop Bills section:
    - Deprecated: Redirects to Statements & Reports dashboard
    """
    query = request.GET.urlencode()
    if query:
        return redirect(f"/reports/?{query}")
    return redirect("billing:reports_dashboard")


def shop_bills_export_csv(request):
    """Deprecated: Redirects to reports_export_csv"""
    query = request.GET.urlencode()
    if query:
        return redirect(f"/reports/export-csv/?{query}")
    return redirect("billing:reports_export_csv")


# ==========================================
# CUSTOMER DIRECTORY & DOWNLOADS
# ==========================================

def customer_list(request):
    """
    Dedicated Customers Directory page:
    - View all customers with total billed, total paid, pending balances
    - Search by name, phone, email, address
    - Filter for customers with pending balances only
    - Single customer CSV ledger export & PDF statement
    - Bulk download ALL customers directory as CSV
    - Manual admin delete option
    """
    c_filter = get_client_filter(request)
    search = request.GET.get("search", "").strip()
    pending_only = request.GET.get("pending_only") == "1"

    customers_qs = Customer.objects.filter(c_filter).annotate(
        calc_billed=Sum("invoices__grand_total"),
        calc_paid=Sum("invoices__paid_amount"),
        calc_pending=Sum("invoices__balance_amount"),
        invoices_count=Count("invoices")
    ).order_by("name")

    if search:
        customers_qs = customers_qs.filter(
            Q(name__icontains=search) |
            Q(phone__icontains=search) |
            Q(email__icontains=search) |
            Q(gst_number__icontains=search) |
            Q(address__icontains=search)
        )

    if pending_only:
        customers_qs = customers_qs.filter(calc_pending__gt=0)

    all_custs = Customer.objects.filter(c_filter).annotate(calc_pending=Sum("invoices__balance_amount"))
    total_customers_count = all_custs.count()
    pending_custs_count = all_custs.filter(calc_pending__gt=0).count()
    total_pending_all = Invoice.objects.filter(c_filter).aggregate(Sum("balance_amount"))["balance_amount__sum"] or Decimal("0.00")

    return render(request, "billing/customer_list.html", {
        "customers": customers_qs,
        "total_customers_count": total_customers_count,
        "pending_custs_count": pending_custs_count,
        "total_pending_all": total_pending_all,
        "search": search,
        "pending_only": pending_only,
    })


def customer_delete(request, customer_id):
    """
    Deletes a customer record.
    Accessible to authenticated shop clients and administrators.
    """
    if not request.user.is_authenticated:
        return redirect("billing:login")
    if request.method == "POST":
        c_filter = get_client_filter(request)
        customer = get_object_or_404(Customer.objects.filter(c_filter), pk=customer_id)
        cust_name = customer.name
        cust_phone = customer.phone or "N/A"

        Invoice.objects.filter(c_filter, customer=customer).update(customer=None)
        customer.delete()

        log_activity(
            request,
            "CUSTOMER_DELETE",
            f"User '{request.user.username}' deleted customer record '{cust_name}' (Phone: {cust_phone})."
        )
        messages.success(request, f"Customer record '{cust_name}' was deleted successfully.")

    return redirect("billing:customer_list")


def customer_export_csv(request, customer_id):
    """Exports a single customer's transaction ledger / statement as CSV"""
    c_filter = get_client_filter(request)
    customer = get_object_or_404(Customer.objects.filter(c_filter), pk=customer_id)
    today = timezone.now().date()
    default_start = today - timedelta(days=90)

    start_str = request.GET.get("start_date", "").strip()
    end_str = request.GET.get("end_date", "").strip()

    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").date() if start_str else default_start
    except Exception:
        start_date = default_start

    try:
        end_date = datetime.strptime(end_str, "%Y-%m-%d").date() if end_str else today
    except Exception:
        end_date = today

    transactions, summary = get_customer_statement_data(customer, start_date, end_date)

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    safe_name = "".join(c for c in customer.name if c.isalnum() or c in (" ", "_")).strip().replace(" ", "_")
    response["Content-Disposition"] = f'attachment; filename="Ledger_{safe_name}_{start_date}_{end_date}.csv"'
    response.write("\ufeff")

    writer = csv.writer(response)
    writer.writerow([f"CUSTOMER ACCOUNT STATEMENT - {customer.name}"])
    writer.writerow([f"Phone: {customer.phone or 'N/A'}", f"Email: {customer.email or 'N/A'}", f"GST: {customer.gst_number or 'N/A'}"])
    writer.writerow([f"Period: {start_date} to {end_date}"])
    writer.writerow([])
    writer.writerow(["Opening Balance (INR)", f"{summary['opening_balance']:.2f}"])
    writer.writerow(["Total Billed (INR)", f"{summary['total_billed']:.2f}"])
    writer.writerow(["Total Paid (INR)", f"{summary['total_paid']:.2f}"])
    writer.writerow(["Net Pending Due (INR)", f"{summary['net_pending']:.2f}"])
    writer.writerow([])
    writer.writerow(["Date & Time", "Type", "Reference / Bill #", "Particulars", "Debit / Billed (INR)", "Credit / Paid (INR)", "Running Balance (INR)"])

    for tx in transactions:
        dt_str = tx["date"].strftime("%Y-%m-%d %H:%M")
        writer.writerow([
            dt_str,
            tx["type"],
            tx["ref"],
            tx["details"],
            f"{tx['debit']:.2f}",
            f"{tx['credit']:.2f}",
            f"{tx['balance']:.2f}",
        ])

    return response


def customers_export_all_csv(request):
    """Exports ALL customers directory summary to a CSV file"""
    c_filter = get_client_filter(request)
    customers_qs = Customer.objects.filter(c_filter).annotate(
        calc_billed=Sum("invoices__grand_total"),
        calc_paid=Sum("invoices__paid_amount"),
        calc_pending=Sum("invoices__balance_amount"),
        invoices_count=Count("invoices")
    ).order_by("name")

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    now_str = timezone.now().strftime("%Y%m%d_%H%M%S")
    response["Content-Disposition"] = f'attachment; filename="SmartBilling_All_Customers_{now_str}.csv"'
    response.write("\ufeff")

    writer = csv.writer(response)
    writer.writerow([
        "Customer Name", "Phone Number", "Email Address", "Address",
        "GST Number", "Total Invoices Count", "Total Billed (INR)",
        "Total Paid (INR)", "Pending Balance Due (INR)", "Customer Since"
    ])

    for c in customers_qs:
        dt_str = c.created_at.strftime("%Y-%m-%d")
        billed = c.calc_billed or Decimal("0.00")
        paid = c.calc_paid or Decimal("0.00")
        pending = c.calc_pending or Decimal("0.00")
        writer.writerow([
            c.name,
            c.phone or "",
            c.email or "",
            c.address or "",
            c.gst_number or "",
            c.invoices_count or 0,
            f"{billed:.2f}",
            f"{paid:.2f}",
            f"{pending:.2f}",
            dt_str
        ])

    return response


# ==========================================
# SHOP BRANCHES MANAGEMENT (Admin Only)
# ==========================================

@admin_required
def branch_add(request):
    """Admin adds a new shop branch"""
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        branch_code = request.POST.get("branch_code", "").strip()
        phone = request.POST.get("phone", "").strip()
        email = request.POST.get("email", "").strip()
        address = request.POST.get("address", "").strip()
        manager_name = request.POST.get("manager_name", "").strip()
        is_default = request.POST.get("is_default") == "1"

        if not name:
            messages.error(request, "Branch name is required.")
            return redirect("billing:admin_panel")

        if not branch_code:
            branch_code = f"BR-{timezone.now().strftime('%y%m%d%H%M')}"

        if Branch.objects.filter(branch_code=branch_code).exists():
            messages.error(request, f"Branch code '{branch_code}' already exists.")
            return redirect("billing:admin_panel")

        if is_default:
            Branch.objects.filter(is_default=True).update(is_default=False)

        branch = Branch.objects.create(
            name=name,
            branch_code=branch_code,
            phone=phone,
            email=email,
            address=address,
            manager_name=manager_name,
            is_default=is_default,
            is_active=True,
        )

        log_activity(
            request,
            "BRANCH_CREATE",
            f"Created new shop branch '{branch.name}' (Code: {branch.branch_code}, Manager: {manager_name or 'N/A'})"
        )
        messages.success(request, f"Shop Branch '{branch.name}' created successfully!")

    return redirect("billing:admin_panel")


@admin_required
def branch_edit(request, branch_id):
    """Admin edits shop branch details"""
    branch = get_object_or_404(Branch, pk=branch_id)

    if request.method == "POST":
        branch.name = request.POST.get("name", branch.name).strip()
        branch.branch_code = request.POST.get("branch_code", branch.branch_code).strip()
        branch.phone = request.POST.get("phone", branch.phone).strip()
        branch.email = request.POST.get("email", branch.email).strip()
        branch.address = request.POST.get("address", branch.address).strip()
        branch.manager_name = request.POST.get("manager_name", branch.manager_name).strip()
        is_default = request.POST.get("is_default") == "1"
        is_active = request.POST.get("is_active") == "1"

        if is_default and not branch.is_default:
            Branch.objects.filter(is_default=True).update(is_default=False)

        branch.is_default = is_default
        branch.is_active = is_active
        branch.save()

        log_activity(
            request,
            "BRANCH_EDIT",
            f"Updated shop branch '{branch.name}' (Code: {branch.branch_code}, Manager: {branch.manager_name or 'N/A'}, Active: {branch.is_active})"
        )
        messages.success(request, f"Branch '{branch.name}' updated successfully!")

    return redirect("billing:admin_panel")


@admin_required
def branch_delete(request, branch_id):
    """Admin deletes or deactivates a shop branch"""
    if request.method == "POST":
        branch = get_object_or_404(Branch, pk=branch_id)
        b_name = branch.name

        if branch.is_default:
            messages.error(request, f"Cannot delete default branch '{b_name}'. Please assign another default branch first.")
            return redirect("billing:admin_panel")

        if branch.invoices.exists():
            branch.is_active = False
            branch.save(update_fields=["is_active", "updated_at"])
            msg = f"Branch '{b_name}' has existing bills and was deactivated to preserve invoice history."
        else:
            branch.delete()
            msg = f"Shop Branch '{b_name}' deleted successfully."

        log_activity(request, "BRANCH_DELETE", f"Admin removed/deactivated branch '{b_name}'")
        messages.success(request, msg)

    return redirect("billing:admin_panel")


# ==========================================
# STATEMENTS & REPORTS (DAILY / WEEKLY / MONTHLY / CUSTOM)
# ==========================================

def _get_filtered_report_data(request):
    """
    Helper to extract filtered invoices and compute summary KPIs for Statements & Reports.
    Supports ranges: today, yesterday, this_week, this_month, last_30_days, custom date range.
    """
    today = timezone.now().date()
    range_filter = request.GET.get("range", "this_week").strip()
    start_date_str = request.GET.get("start_date", "").strip()
    end_date_str = request.GET.get("end_date", "").strip()
    branch_id = request.GET.get("branch", "").strip()
    status_filter = request.GET.get("status", "").strip()
    method_filter = request.GET.get("method", "").strip()
    search = request.GET.get("search", "").strip()

    if range_filter == "today":
        start_date = today
        end_date = today
        period_label = f"Today ({today.strftime('%d %b %Y')})"
    elif range_filter == "yesterday":
        start_date = today - timedelta(days=1)
        end_date = today - timedelta(days=1)
        period_label = f"Yesterday ({start_date.strftime('%d %b %Y')})"
    elif range_filter == "this_week":
        start_date = today - timedelta(days=today.weekday())
        end_date = today
        period_label = f"This Week ({start_date.strftime('%d %b')} - {end_date.strftime('%d %b %Y')})"
    elif range_filter == "this_month":
        start_date = today.replace(day=1)
        end_date = today
        period_label = f"This Month ({today.strftime('%B %Y')})"
    elif range_filter == "last_30_days":
        start_date = today - timedelta(days=30)
        end_date = today
        period_label = f"Last 30 Days ({start_date.strftime('%d %b')} - {end_date.strftime('%d %b %Y')})"
    elif range_filter == "custom" or start_date_str or end_date_str:
        range_filter = "custom"
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date() if start_date_str else today - timedelta(days=7)
        except Exception:
            start_date = today - timedelta(days=7)
        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date() if end_date_str else today
        except Exception:
            end_date = today
        period_label = f"{start_date.strftime('%d %b %Y')} to {end_date.strftime('%d %b %Y')}"
    else:
        range_filter = "this_week"
        start_date = today - timedelta(days=today.weekday())
        end_date = today
        period_label = f"This Week ({start_date.strftime('%d %b')} - {end_date.strftime('%d %b %Y')})"

    c_filter = get_client_filter(request)
    bills_qs = Invoice.objects.filter(
        c_filter,
        created_at__date__gte=start_date,
        created_at__date__lte=end_date
    ).select_related("customer", "branch").prefetch_related("items", "payments").order_by("-created_at")

    if branch_id:
        bills_qs = bills_qs.filter(branch_id=branch_id)
    if status_filter:
        bills_qs = bills_qs.filter(payment_status=status_filter)
    if method_filter:
        bills_qs = bills_qs.filter(payment_method=method_filter)
    if search:
        bills_qs = bills_qs.filter(
            Q(invoice_number__icontains=search) |
            Q(customer_name__icontains=search) |
            Q(customer_phone__icontains=search)
        )

    aggregates = bills_qs.aggregate(
        total_sales=Sum("grand_total"),
        total_paid=Sum("paid_amount"),
        total_pending=Sum("balance_amount"),
        total_discount=Sum("discount_amount"),
        bills_count=Count("id")
    )

    paid_count = bills_qs.filter(payment_status="paid").count()
    part_count = bills_qs.filter(payment_status="partially_paid").count()
    unpaid_count = bills_qs.filter(payment_status="unpaid").count()

    cash_total = bills_qs.filter(payment_method="cash").aggregate(Sum("grand_total"))["grand_total__sum"] or Decimal("0.00")
    online_total = bills_qs.filter(payment_method="online").aggregate(Sum("grand_total"))["grand_total__sum"] or Decimal("0.00")

    summary = {
        "count": aggregates["bills_count"] or 0,
        "total_sales": aggregates["total_sales"] or Decimal("0.00"),
        "total_paid": aggregates["total_paid"] or Decimal("0.00"),
        "total_pending": aggregates["total_pending"] or Decimal("0.00"),
        "total_discount": aggregates["total_discount"] or Decimal("0.00"),
        "paid_count": paid_count,
        "partial_count": part_count,
        "unpaid_count": unpaid_count,
        "cash_total": cash_total,
        "online_total": online_total,
    }

    # -------------------------------------------------------------------------
    # Customer Footfall & Visits Analytics (வாடிக்கையாளர்கள் வருகை)
    # Today, Week, Month, Year, and Date-to-Date Range
    # -------------------------------------------------------------------------
    def _compute_customer_footfall(qs):
        # Registered customers
        reg_count = qs.values("customer_id").distinct().exclude(customer_id=None).count()
        # Walk-in customers with contact phone
        walkin_phone_count = qs.filter(customer_id=None).exclude(customer_phone="").values("customer_phone").distinct().count()
        # Walk-in customers without phone
        walkin_anon_count = qs.filter(customer_id=None, customer_phone="").count()
        total_unique = reg_count + walkin_phone_count + (1 if walkin_anon_count > 0 else 0)
        total_bills = qs.count()
        return {
            "total_customers": total_unique if total_unique > 0 else total_bills,
            "total_bills": total_bills,
            "registered": reg_count,
            "walkin": walkin_phone_count + walkin_anon_count,
        }

    # 1. Today
    today_bills = Invoice.objects.filter(c_filter, created_at__date=today)
    today_footfall = _compute_customer_footfall(today_bills)

    # 2. This Week
    week_start = today - timedelta(days=today.weekday())
    week_bills = Invoice.objects.filter(c_filter, created_at__date__gte=week_start, created_at__date__lte=today)
    week_footfall = _compute_customer_footfall(week_bills)

    # 3. This Month
    month_start = today.replace(day=1)
    month_bills = Invoice.objects.filter(c_filter, created_at__date__gte=month_start, created_at__date__lte=today)
    month_footfall = _compute_customer_footfall(month_bills)

    # 4. This Year
    year_start = today.replace(month=1, day=1)
    year_bills = Invoice.objects.filter(c_filter, created_at__date__gte=year_start, created_at__date__lte=today)
    year_footfall = _compute_customer_footfall(year_bills)

    # 5. Selected Period / Date-to-Date Range
    range_footfall = _compute_customer_footfall(bills_qs)
    total_sales_val = aggregates["total_sales"] or Decimal("0.00")
    avg_spend = (total_sales_val / range_footfall["total_customers"]) if range_footfall["total_customers"] > 0 else Decimal("0.00")
    range_footfall["avg_spend"] = avg_spend

    customer_footfall = {
        "today": today_footfall,
        "week": week_footfall,
        "month": month_footfall,
        "year": year_footfall,
        "range": range_footfall,
    }

    return {
        "range_filter": range_filter,
        "start_date": start_date,
        "end_date": end_date,
        "period_label": period_label,
        "branch_id": branch_id,
        "status_filter": status_filter,
        "method_filter": method_filter,
        "search": search,
        "bills_qs": bills_qs,
        "summary": summary,
        "customer_footfall": customer_footfall,
    }


def reports_dashboard_view(request):
    """
    Dedicated Statements & Reports Dashboard:
    - Provides Daily, Weekly, Monthly, and Custom Range revenue and invoice statements.
    - KPI cards: Total Sales, Customer Paid, Pending Balance, Bills Count, Cash/Online splits.
    - Side Section: Customer Footfall Analytics (Today, Week, Month, Year, Date-to-Date).
    - Export buttons: Download CSV and Download PDF.
    """
    data = _get_filtered_report_data(request)
    branches = Branch.objects.filter(is_active=True).order_by("-is_default", "name")

    return render(request, "billing/reports.html", {
        "bills": data["bills_qs"][:300],
        "summary": data["summary"],
        "customer_footfall": data["customer_footfall"],
        "branches": branches,
        "range_filter": data["range_filter"],
        "start_date": data["start_date"].strftime("%Y-%m-%d"),
        "end_date": data["end_date"].strftime("%Y-%m-%d"),
        "period_label": data["period_label"],
        "selected_branch": data["branch_id"],
        "status_filter": data["status_filter"],
        "method_filter": data["method_filter"],
        "search": data["search"],
    })


def reports_export_csv(request):
    """Exports filtered Statements & Reports data as Excel-compatible CSV (with UTF-8 BOM)"""
    data = _get_filtered_report_data(request)

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    now_str = timezone.now().strftime("%Y%m%d_%H%M%S")
    response["Content-Disposition"] = f'attachment; filename="SmartBilling_Sales_Report_{data["range_filter"]}_{now_str}.csv"'
    response.write("\ufeff")

    writer = csv.writer(response)
    writer.writerow(["SMARTBILLING POS - STATEMENTS & SALES REPORT"])
    writer.writerow([f"Period: {data['period_label']}"])
    writer.writerow([])
    writer.writerow(["SUMMARY METRICS"])
    writer.writerow(["Total Bills Generated", data["summary"]["count"]])
    writer.writerow(["Total Sales Amount (INR)", f"{data['summary']['total_sales']:.2f}"])
    writer.writerow(["Total Customer Paid (INR)", f"{data['summary']['total_paid']:.2f}"])
    writer.writerow(["Total Pending Due (INR)", f"{data['summary']['total_pending']:.2f}"])
    writer.writerow(["Total Discount (INR)", f"{data['summary']['total_discount']:.2f}"])
    writer.writerow(["Cash Revenue (INR)", f"{data['summary']['cash_total']:.2f}"])
    writer.writerow(["Online Revenue (INR)", f"{data['summary']['online_total']:.2f}"])
    writer.writerow([])
    writer.writerow([
        "Invoice Number", "Date & Time", "Branch", "Customer Name",
        "Customer Phone", "Subtotal (INR)", "Tax (INR)", "Discount (INR)",
        "Grand Total (INR)", "Paid Amount (INR)", "Balance Due (INR)",
        "Payment Method", "Payment Status"
    ])

    for b in data["bills_qs"]:
        writer.writerow([
            b.invoice_number,
            b.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            b.branch_name,
            b.customer_name,
            b.customer_phone,
            f"{b.subtotal:.2f}",
            f"{b.tax_amount:.2f}",
            f"{b.discount_amount:.2f}",
            f"{b.grand_total:.2f}",
            f"{b.paid_amount:.2f}",
            f"{b.balance_amount:.2f}",
            b.payment_method.capitalize(),
            b.payment_status.replace("_", " ").title(),
        ])

    log_activity(
        request,
        "REPORT_EXPORT",
        f"Exported sales report CSV for period '{data['period_label']}' ({data['summary']['count']} bills)"
    )
    return response


def reports_export_pdf(request):
    """Exports filtered Statements & Reports data as high-quality PDF report with PyMuPDF"""
    data = _get_filtered_report_data(request)
    company = CompanySettings.get_settings()
    client_profile = getattr(request.user, "profile", None) if request.user.is_authenticated else None

    title = f"Statements & Sales Report - {data['period_label']}"
    pdf_bytes = generate_sales_report_pdf(
        title=title,
        period_label=data["period_label"],
        summary=data["summary"],
        invoices=data["bills_qs"][:500],
        company=company,
        client_profile=client_profile,
    )

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    now_str = timezone.now().strftime("%Y%m%d_%H%M%S")
    response["Content-Disposition"] = f'attachment; filename="SmartBilling_Sales_Report_{data["range_filter"]}_{now_str}.pdf"'

    log_activity(
        request,
        "REPORT_EXPORT",
        f"Exported sales report PDF for period '{data['period_label']}' ({data['summary']['count']} bills)"
    )
    return response


# ==========================================
# CLIENT PROFILE & BRANCHES MANAGEMENT
# ==========================================

def client_profile_view(request):
    """
    Client Profile:
    - Allows client to manage business information: Shop Name, Shop Address, Phone, Email,
      Bank Name, Account Number, IFSC Code, Shop Logo, and Branches.
    - Username and Password CANNOT be edited from Client Panel (locked/read-only).
    - Business Type is locked/read-only (set by Admin).
    - All input fields have clean placeholder text only.
    """
    if not request.user.is_authenticated:
        return redirect(f"/login/?next={request.path}")

    profile = getattr(request.user, "profile", None)
    if not profile:
        profile, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == "POST":
        shop_name = request.POST.get("shop_name", "").strip()
        shop_address = request.POST.get("shop_address", "").strip()
        phone = request.POST.get("phone", "").strip()
        email = request.POST.get("email", "").strip()
        bank_name = request.POST.get("bank_name", "").strip()
        account_number = request.POST.get("account_number", "").strip()
        ifsc_code = request.POST.get("ifsc_code", "").strip()

        profile.shop_name = shop_name
        profile.shop_address = shop_address
        profile.phone = phone
        profile.bank_name = bank_name
        profile.account_number = account_number
        profile.ifsc_code = ifsc_code

        if email:
            request.user.email = email
            request.user.save(update_fields=["email"])

        # Handle Shop Logo upload
        logo_file = request.FILES.get("shop_logo")
        if logo_file:
            profile.shop_logo_image = logo_file
            try:
                logo_file.seek(0)
                b64_content = base64.b64encode(logo_file.read()).decode("utf-8")
                mime_type = logo_file.content_type or "image/png"
                profile.shop_logo_base64 = f"data:{mime_type};base64,{b64_content}"
            except Exception:
                pass

        profile.save()

        log_activity(
            request,
            "PROFILE_UPDATE",
            f"User '{request.user.username}' updated business profile (Shop: {shop_name or 'N/A'}, Phone: {phone or 'N/A'}, Bank: {bank_name or 'N/A'})"
        )
        messages.success(request, "Shop Profile & Bank Details updated successfully!")
        return redirect("billing:client_profile")

    # Fetch branches associated with this client or default branches
    if request.user.is_superuser or profile.role == "admin":
        branches = Branch.objects.all().order_by("-is_default", "name")
    else:
        branches = Branch.objects.filter(Q(client=request.user) | Q(is_default=True)).order_by("-is_default", "name")

    return render(request, "billing/client_profile.html", {
        "profile": profile,
        "branches": branches,
    })


def client_branch_add(request):
    """Client adds a branch under their account"""
    if not request.user.is_authenticated:
        return redirect(f"/login/?next={request.path}")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        branch_code = request.POST.get("branch_code", "").strip()
        phone = request.POST.get("phone", "").strip()
        email = request.POST.get("email", "").strip()
        address = request.POST.get("address", "").strip()
        manager_name = request.POST.get("manager_name", "").strip()

        if not name:
            messages.error(request, "Branch name is required.")
            return redirect("billing:client_profile")

        if not branch_code:
            branch_code = f"BR-{timezone.now().strftime('%y%m%d%H%M')}"

        if Branch.objects.filter(branch_code=branch_code).exists():
            messages.error(request, f"Branch code '{branch_code}' already exists.")
            return redirect("billing:client_profile")

        branch = Branch.objects.create(
            name=name,
            branch_code=branch_code,
            client=request.user,
            phone=phone,
            email=email,
            address=address,
            manager_name=manager_name,
            is_active=True,
        )

        log_activity(
            request,
            "BRANCH_CREATE",
            f"Client '{request.user.username}' added branch '{branch.name}' (Code: {branch.branch_code})"
        )
        messages.success(request, f"Shop Branch '{branch.name}' added successfully!")

    return redirect("billing:client_profile")


def client_branch_edit(request, branch_id):
    """Client edits branch details"""
    if not request.user.is_authenticated:
        return redirect(f"/login/?next={request.path}")

    is_admin = request.user.is_superuser or (
        hasattr(request.user, "profile") and request.user.profile.role == "admin"
    )
    if is_admin:
        branch = get_object_or_404(Branch, pk=branch_id)
    else:
        branch = get_object_or_404(Branch, pk=branch_id, client=request.user)

    if request.method == "POST":
        branch.name = request.POST.get("name", branch.name).strip()
        branch.phone = request.POST.get("phone", branch.phone).strip()
        branch.email = request.POST.get("email", branch.email).strip()
        branch.address = request.POST.get("address", branch.address).strip()
        branch.manager_name = request.POST.get("manager_name", branch.manager_name).strip()
        branch.save()

        log_activity(
            request,
            "BRANCH_EDIT",
            f"Updated branch '{branch.name}' (Code: {branch.branch_code})"
        )
        messages.success(request, f"Branch '{branch.name}' updated successfully!")

    return redirect("billing:client_profile")


def client_branch_delete(request, branch_id):
    """Client deletes a branch"""
    if not request.user.is_authenticated:
        return redirect(f"/login/?next={request.path}")

    if request.method == "POST":
        is_admin = request.user.is_superuser or (
            hasattr(request.user, "profile") and request.user.profile.role == "admin"
        )
        if is_admin:
            branch = get_object_or_404(Branch, pk=branch_id)
        else:
            branch = get_object_or_404(Branch, pk=branch_id, client=request.user)

        b_name = branch.name
        if branch.is_default:
            messages.error(request, f"Cannot delete default branch '{b_name}'.")
            return redirect("billing:client_profile")

        if branch.invoices.exists():
            branch.is_active = False
            branch.save(update_fields=["is_active", "updated_at"])
            messages.info(request, f"Branch '{b_name}' has existing bills and was deactivated.")
        else:
            branch.delete()
            messages.success(request, f"Branch '{b_name}' deleted successfully.")

        log_activity(request, "BRANCH_DELETE", f"User '{request.user.username}' deleted/deactivated branch '{b_name}'")

    return redirect("billing:client_profile")


# ==========================================
# SOFTWARE UPDATES MANAGEMENT
# ==========================================

@admin_required
def admin_publish_software_update(request):
    """Admin publishes a new software update for all clients"""
    if request.method == "POST":
        version = request.POST.get("version", "").strip()
        title = request.POST.get("title", "").strip()
        release_notes = request.POST.get("release_notes", "").strip()

        if not version:
            messages.error(request, "Update version (e.g. v2.5.0) is required.")
            return redirect("billing:admin_panel")

        update_obj, created = SoftwareUpdate.objects.update_or_create(
            version=version,
            defaults={
                "title": title or f"SmartBilling POS {version}",
                "release_notes": release_notes,
                "is_published": True,
                "published_at": timezone.now(),
            }
        )

        log_activity(
            request,
            "UPDATE_PUBLISH",
            f"Admin published software update '{update_obj.version}': {update_obj.title}"
        )
        messages.success(request, f"Software Update {update_obj.version} successfully published to all clients!")

    return redirect("billing:admin_panel")


def client_apply_software_update(request):
    """
    Client applies/acknowledges software update:
    - Does NOT disrupt active billing or reset current invoice forms.
    - Updates session state and returns JSON for AJAX calls or redirects back.
    """
    latest = SoftwareUpdate.get_latest_update()
    version = latest.version if latest else "v2.5.0"
    request.session["applied_update_version"] = version

    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.GET.get("format") == "json":
        return JsonResponse({
            "status": "success",
            "message": f"Software updated to {version} successfully.",
            "version": version
        })

    messages.success(request, f"Software updated to {version} successfully.")
    return redirect(request.META.get("HTTP_REFERER", "/"))


@admin_required
def admin_download_all_data(request):
    """
    Exports and downloads all database records (Customers, Products, Invoices, Items, Branches, Settings)
    as a complete, structured JSON backup file.
    """
    import json

    customers = []
    for c in Customer.objects.all():
        customers.append({
            "id": c.id,
            "name": c.name,
            "phone": c.phone,
            "email": c.email,
            "address": c.address,
            "gst_number": c.gst_number,
            "notes": c.notes,
            "total_billed": str(c.total_billed),
            "total_pending": str(c.total_pending),
            "created_at": str(c.created_at),
        })

    products = list(Product.objects.values(
        "id", "sku", "name", "name_tamil", "category", "unit", "price", "cost_price",
        "stock_quantity", "tax_percent", "is_active", "created_at"
    ))
    invoices = []
    for inv in Invoice.objects.prefetch_related("items", "payments").all().order_by("created_at"):
        items = list(inv.items.values(
            "product_sku", "product_name", "unit_price", "quantity", "tax_percent", "tax_amount", "discount_percent", "total_price"
        ))
        payments = list(inv.payments.values(
            "amount", "payment_method", "notes", "created_at"
        ))
        invoices.append({
            "id": inv.id,
            "invoice_number": inv.invoice_number,
            "invoice_uuid": inv.invoice_uuid,
            "customer_name": inv.customer_name,
            "customer_phone": inv.customer_phone,
            "subtotal": str(inv.subtotal),
            "discount_amount": str(inv.discount_amount),
            "grand_total": str(inv.grand_total),
            "paid_amount": str(inv.paid_amount),
            "balance_amount": str(inv.balance_amount),
            "payment_status": inv.payment_status,
            "source": inv.source,
            "created_at": str(inv.created_at),
            "items": items,
            "payments": payments,
        })
    branches = list(Branch.objects.values("id", "name", "branch_code", "address", "phone", "is_default", "is_active"))
    comp = CompanySettings.get_settings()
    company_data = {
        "company_name": comp.company_name,
        "phone": comp.phone,
        "email": comp.email,
        "address": comp.address,
        "gst_number": comp.gst_number,
        "bank_name": comp.bank_name,
        "account_number": comp.account_number,
        "ifsc_code": comp.ifsc_code,
    }

    backup_payload = {
        "export_timestamp": timezone.now().isoformat(),
        "exported_by": request.user.username,
        "system": "MathanHub Cloud & POS",
        "company": company_data,
        "branches": branches,
        "products": products,
        "customers": customers,
        "invoices": invoices,
        "stats": {
            "total_products": len(products),
            "total_customers": len(customers),
            "total_invoices": len(invoices),
        }
    }

    filename = f"mathanhub_full_backup_{timezone.now().strftime('%Y%m%d_%H%M%S')}.json"
    response = HttpResponse(
        json.dumps(backup_payload, indent=2, default=str),
        content_type="application/json"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    log_activity(request, "DATA_BACKUP", f"Admin '{request.user.username}' downloaded full database backup ({filename}).")
    return response


@admin_required
def admin_delete_customer_or_data(request, customer_id):
    """
    Admin deletes a customer or deletes all bills for that customer.
    Supports:
    - 'bills_only': clears all invoices for customer, restores inventory, sets balance to 0
    - 'full': permanently deletes customer and all associated bills
    """
    customer = get_object_or_404(Customer, pk=customer_id)
    cust_name = customer.name
    action_type = request.POST.get("action_type", "full")

    if request.method == "POST":
        invoices = Invoice.objects.filter(customer=customer)
        inv_count = invoices.count()

        # Restore stock for invoice items
        for inv in invoices.prefetch_related("items"):
            for item in inv.items.all():
                if item.product:
                    Product.objects.filter(pk=item.product.id).update(
                        stock_quantity=F("stock_quantity") + item.quantity
                    )
            inv.items.all().delete()
        invoices.delete()

        if action_type == "full":
            customer.delete()
            log_activity(
                request,
                "CUSTOMER_DELETE",
                f"Admin '{request.user.username}' permanently deleted customer '{cust_name}' and {inv_count} bills."
            )
            messages.success(request, f"Customer '{cust_name}' and all {inv_count} bills deleted permanently.")
        else:
            log_activity(
                request,
                "DATA_CLEANUP",
                f"Admin '{request.user.username}' cleared all {inv_count} bills for customer '{cust_name}'."
            )
            messages.success(request, f"All {inv_count} bills for customer '{cust_name}' cleared successfully (Profile retained).")

    return redirect("billing:admin_panel")

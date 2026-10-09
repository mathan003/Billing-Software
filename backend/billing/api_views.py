import logging
from decimal import Decimal
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Sum, F, Q
from django.utils import timezone
from rest_framework import status, views, viewsets
from rest_framework.response import Response
from .models import (
    Product, Customer, Invoice, InvoiceItem, SyncLog,
    CompanySettings, SoftwareUpdate, Purchase, PaymentRecord,
    Branch, UserProfile, RegisteredDevice, ProductCategory, ActiveUserSession, DeletedClient
)
from .serializers import (
    ProductSerializer,
    CustomerSerializer,
    InvoiceSerializer,
    SyncPushRequestSerializer,
)

logger = logging.getLogger(__name__)


class HealthCheckView(views.APIView):
    """
    Lightweight health check endpoint for Windows desktop app and Postman
    to check connectivity before auto-syncing.
    """
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response({
            "status": "online",
            "server": "Django Billing Cloud Server",
            "version": "1.0.0",
            "server_time": timezone.now().isoformat(),
        })


class ClientAuthVerifyView(views.APIView):
    """
    Endpoint for Windows Desktop App to verify and fetch client credentials from Cloud Server.
    Enables clients created in the Web Admin Panel to seamlessly log into the Desktop App.
    """
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        username = request.data.get("username", "").strip()
        password = request.data.get("password", "")
        device_id = request.data.get("device_id", "").strip()
        device_name = request.data.get("device_name", "Windows POS Terminal")

        if not username or not password:
            return Response({"status": "error", "message": "Username and password are required."}, status=status.HTTP_400_BAD_REQUEST)

        user = authenticate(username=username, password=password)
        if not user:
            return Response({"status": "error", "message": "Invalid username or password on Cloud Server."}, status=status.HTTP_401_UNAUTHORIZED)

        if not user.is_active:
            return Response({"status": "error", "message": "This account is inactive. Please contact Administrator."}, status=status.HTTP_403_FORBIDDEN)

        profile = getattr(user, "profile", None)
        is_admin = user.is_superuser or (profile and profile.role == "admin")

        if not is_admin and profile and profile.access_mode == "online_only":
            return Response({
                "status": "error",
                "message": "Access Denied: This client account is authorized for Web Portal only. Desktop POS login is not permitted."
            }, status=status.HTTP_403_FORBIDDEN)

        if not is_admin and profile and device_id:
            dev_limit = profile.device_limit or 5
            admin_otp = str(request.data.get("admin_otp", "")).strip()
            ip_addr = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", "127.0.0.1")

            existing_dev = RegisteredDevice.objects.filter(
                user=user,
                device_id=device_id,
                is_active=True,
                is_verified=True
            ).first()

            if not existing_dev:
                # New system connecting for this client!
                current_active_devices = RegisteredDevice.objects.filter(user=user, is_active=True, is_verified=True).count()
                if current_active_devices >= dev_limit:
                    return Response({
                        "status": "error",
                        "message": f"Device Limit Exceeded ({current_active_devices}/{dev_limit}). Admin has configured maximum {dev_limit} devices."
                    }, status=status.HTTP_403_FORBIDDEN)

                pending_dev, _ = RegisteredDevice.objects.get_or_create(
                    user=user,
                    device_id=device_id,
                    defaults={
                        "device_name": device_name,
                        "device_type": "desktop_exe",
                        "ip_address": ip_addr,
                        "is_active": True,
                        "is_verified": False,
                    }
                )

                if admin_otp:
                    if pending_dev.verify_otp(admin_otp):
                        from .models import log_activity
                        log_activity(
                            user,
                            "DEVICE_VERIFY",
                            f"Admin OTP verified successfully for device '{device_name}' ({device_id[:12]}). Client '{username}' authorized.",
                            ip_address=ip_addr
                        )
                    else:
                        return Response({
                            "status": "invalid_otp",
                            "message": "Invalid or expired 6-Digit Admin Verification OTP. Please check the code in the Website Admin Panel.",
                        }, status=status.HTTP_400_BAD_REQUEST)
                else:
                    # Generate 6-digit OTP and send to Website Admin Panel
                    otp_code = pending_dev.generate_otp()
                    pending_dev.ip_address = ip_addr
                    pending_dev.device_name = device_name
                    pending_dev.save(update_fields=["ip_address", "device_name"])

                    from .models import log_activity
                    log_activity(
                        user,
                        "DEVICE_OTP",
                        f"New system activation request for client '{username}' on '{device_name}' (IP: {ip_addr}). Admin 6-Digit Verification OTP: {otp_code}",
                        ip_address=ip_addr
                    )

                    return Response({
                        "status": "otp_required",
                        "message": "New system detected. A 6-digit authorization OTP has been generated in the Website Admin Panel. Please obtain the OTP from your Administrator to authorize this computer.",
                        "device_id": device_id,
                        "device_name": device_name,
                        "username": username,
                    }, status=status.HTTP_200_OK)
            else:
                existing_dev.ip_address = ip_addr
                existing_dev.device_name = device_name
                existing_dev.save(update_fields=["ip_address", "device_name", "last_login"])

            # Create or update active session in cloud DB so Admin can monitor and disconnect
            ActiveUserSession.objects.update_or_create(
                session_key=f"EXE-{device_id}"[:40],
                defaults={
                    "user": user,
                    "device_info": f"Desktop POS (EXE): {device_name} ({device_id[:12]})",
                    "ip_address": ip_addr,
                    "last_activity": timezone.now(),
                }
            )

        profile_data = {}
        if profile:
            profile_data = {
                "role": profile.role,
                "shop_name": profile.shop_name,
                "shop_address": profile.shop_address,
                "business_type": profile.business_type,
                "access_mode": profile.access_mode,
                "device_limit": profile.device_limit,
                "phone": profile.phone,
                "gst_number": profile.gst_number,
                "bank_name": profile.bank_name,
                "account_number": profile.account_number,
                "ifsc_code": profile.ifsc_code,
                "avatar_base64": profile.avatar_base64,
                "shop_logo_base64": profile.shop_logo_base64,
            }

        return Response({
            "status": "success",
            "user": {
                "username": user.username,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "is_staff": user.is_staff,
                "is_superuser": user.is_superuser,
            },
            "profile": profile_data,
        })


class ProductViewSet(viewsets.ModelViewSet):
    authentication_classes = []
    permission_classes = []
    queryset = Product.objects.filter(is_active=True)
    serializer_class = ProductSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        category = self.request.query_params.get("category")
        search = self.request.query_params.get("search")
        if category:
            qs = qs.filter(category__iexact=category)
        if search:
            qs = qs.filter(name__icontains=search) | qs.filter(sku__icontains=search)
        return qs


class CustomerViewSet(viewsets.ModelViewSet):
    authentication_classes = []
    permission_classes = []
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(name__icontains=search) | qs.filter(phone__icontains=search)
        return qs


class InvoiceViewSet(viewsets.ReadOnlyModelViewSet):
    authentication_classes = []
    permission_classes = []
    queryset = Invoice.objects.prefetch_related("items").all()
    serializer_class = InvoiceSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        source = self.request.query_params.get("source")
        payment_status = self.request.query_params.get("status")
        if source:
            qs = qs.filter(source=source)
        if payment_status:
            qs = qs.filter(payment_status=payment_status)
        return qs


class SyncPushView(views.APIView):
    """
    Receives batch of invoices created offline in the Windows Desktop App or Postman.
    Guarantees idempotency using invoice_uuid.
    Updates stock and creates customers if needed.
    """
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = SyncPushRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        device_id = data["device_id"]
        client_username = data.get("client_username", "").strip()
        client_user = User.objects.filter(username=client_username).first() if client_username else None

        invoices_data = data.get("invoices", [])
        products_data = data.get("products", [])
        customers_data = data.get("customers", [])
        payments_data = data.get("payments", [])
        categories_data = data.get("categories", [])

        synced_uuids = []
        synced_products = []
        deleted_categories_data = data.get("deleted_categories", [])
        deleted_invoices_data = data.get("deleted_invoices", [])
        synced_customers = []
        synced_payments = []

        try:
            with transaction.atomic():
                # 0. Sync Invoice Deletions from desktop
                from billing.models import DeletedInvoice
                for del_inv_uuid in deleted_invoices_data:
                    d_inv_clean = str(del_inv_uuid or "").strip()
                    if d_inv_clean:
                        del_inv_q = Q(invoice_uuid=d_inv_clean)
                        if client_user:
                            del_inv_q &= Q(client=client_user)
                        target_inv = Invoice.objects.filter(del_inv_q).first()
                        if target_inv:
                            target_inv.is_deleted = True
                            target_inv.deleted_at = timezone.now()
                            target_inv.save(update_fields=["is_deleted", "deleted_at"])
                            DeletedInvoice.objects.update_or_create(
                                invoice_uuid=d_inv_clean,
                                defaults={
                                    "invoice_number": target_inv.invoice_number,
                                    "client": target_inv.client or client_user,
                                    "deleted_at": timezone.now()
                                }
                            )
                        else:
                            DeletedInvoice.objects.update_or_create(
                                invoice_uuid=d_inv_clean,
                                defaults={
                                    "client": client_user,
                                    "deleted_at": timezone.now()
                                }
                            )

                # 0a. Sync Category Deletions from desktop
                for del_cat in deleted_categories_data:
                    d_clean = (del_cat or "").strip()
                    if d_clean:
                        cat_filter = Q(name__iexact=d_clean)
                        prod_cat_filter = Q(category__iexact=d_clean)
                        if client_user:
                            cat_filter &= Q(client=client_user)
                            prod_cat_filter &= Q(client=client_user)
                            ProductCategory.objects.get_or_create(
                                client=client_user,
                                name=d_clean,
                                defaults={"is_deleted": True, "deleted_at": timezone.now()}
                            )
                        ProductCategory.objects.filter(cat_filter).update(is_deleted=True, deleted_at=timezone.now())
                        Product.objects.filter(prod_cat_filter).update(category="General")

                # 0b. Sync Active Categories created offline (do NOT revive soft-deleted categories!)
                deleted_cat_lower = {d.strip().lower() for d in deleted_categories_data if d}
                for cat_name in categories_data:
                    c_clean = (cat_name or "").strip()
                    if c_clean and c_clean.lower() not in deleted_cat_lower:
                        check_q = Q(name__iexact=c_clean, is_deleted=True)
                        if client_user:
                            check_q &= Q(client=client_user)
                        if not ProductCategory.objects.filter(check_q).exists():
                            ProductCategory.objects.get_or_create(name=c_clean, client=client_user, defaults={"is_deleted": False})

                # 0c. Sync Client Account Deletions from desktop
                deleted_clients_data = data.get("deleted_clients", [])
                for del_client in deleted_clients_data:
                    d_c_clean = (del_client or "").strip()
                    if d_c_clean and d_c_clean not in ("admin", "Mathan003"):
                        DeletedClient.objects.get_or_create(username=d_c_clean)
                        User.objects.filter(username=d_c_clean).update(is_active=False)
                        UserProfile.objects.filter(user__username=d_c_clean).update(is_deleted=True, deleted_at=timezone.now())
                        ActiveUserSession.objects.filter(user__username=d_c_clean).delete()
                        RegisteredDevice.objects.filter(user__username=d_c_clean).update(is_active=False, is_verified=False)

                # 0d. Sync Product Deletions from desktop
                from billing.models import DeletedProduct
                deleted_products_data = data.get("deleted_products", [])
                for del_sku in deleted_products_data:
                    d_sku_clean = (del_sku or "").strip()
                    if d_sku_clean:
                        del_p_q = Q(sku=d_sku_clean)
                        if client_user:
                            del_p_q &= Q(client=client_user)
                        Product.objects.filter(del_p_q).update(
                            is_deleted=True,
                            is_active=False,
                            deleted_at=timezone.now(),
                            updated_at=timezone.now()
                        )
                        DeletedProduct.objects.update_or_create(
                            sku=d_sku_clean,
                            client=client_user,
                            defaults={"deleted_at": timezone.now()}
                        )

                # 1. Sync Products (created, updated offline; strictly skip deleted products)
                del_sku_set = {str(s).strip() for s in deleted_products_data if s}
                tombstone_q = Q(client=client_user) if client_user else Q()
                known_deleted_skus = set(DeletedProduct.objects.filter(tombstone_q).values_list("sku", flat=True))
                known_deleted_skus |= set(Product.objects.filter(tombstone_q, is_deleted=True).values_list("sku", flat=True))

                for p_data in products_data:
                    sku = (p_data.get("sku") or "").strip()
                    if not sku or sku in del_sku_set or sku in known_deleted_skus:
                        continue

                    # If this product is already soft-deleted in cloud, do NOT revive!
                    existing_p_q = Q(sku=sku)
                    if client_user:
                        existing_p_q &= Q(client=client_user)
                    existing_p = Product.objects.filter(existing_p_q).first()
                    if existing_p and existing_p.is_deleted:
                        continue

                    product, created = Product.objects.get_or_create(
                        sku=sku,
                        client=client_user,
                        defaults={
                            "client": client_user,
                            "name": p_data.get("name", ""),
                            "name_tamil": p_data.get("name_tamil", ""),
                            "category": p_data.get("category", "General"),
                            "unit": p_data.get("unit", "KG"),
                            "price": p_data["price"],
                            "cost_price": p_data.get("cost_price", 0.00),
                            "tax_percent": p_data.get("tax_percent", 0.00),
                            "stock_quantity": p_data.get("stock_quantity", 0.00),
                            "is_active": p_data.get("is_active", True),
                            "is_deleted": False,
                        }
                    )
                    if not created and not product.is_deleted:
                        product.name = p_data.get("name", product.name)
                        product.name_tamil = p_data.get("name_tamil", product.name_tamil)
                        product.category = p_data.get("category", product.category)
                        product.unit = p_data.get("unit", product.unit)
                        product.price = p_data.get("price", product.price)
                        product.cost_price = p_data.get("cost_price", product.cost_price)
                        product.tax_percent = p_data.get("tax_percent", product.tax_percent)
                        product.stock_quantity = p_data.get("stock_quantity", product.stock_quantity)
                        product.is_active = p_data.get("is_active", product.is_active)
                        if not product.client and client_user:
                            product.client = client_user
                        product.save()
                    synced_products.append(sku)

                # 2. Sync Customers (created or altered offline)
                for c_data in customers_data:
                    phone = c_data.get("phone", "").strip()
                    if phone:
                        cust_filter = Q(phone=phone)
                        if client_user:
                            cust_filter &= Q(client=client_user)
                        cust = Customer.objects.filter(cust_filter).first()
                        if not cust:
                            cust = Customer.objects.create(
                                name=c_data["name"],
                                client=client_user,
                                phone=phone,
                                email=c_data.get("email", ""),
                                address=c_data.get("address", ""),
                                gst_number=c_data.get("gst_number", ""),
                            )
                        else:
                            cust.name = c_data["name"]
                            cust.email = c_data.get("email", cust.email)
                            cust.address = c_data.get("address", cust.address)
                            cust.gst_number = c_data.get("gst_number", cust.gst_number)
                            if not cust.client and client_user:
                                cust.client = client_user
                            cust.save()
                        synced_customers.append(phone)

                # 3. Sync Invoices (created or modified offline)
                from billing.models import DeletedInvoice
                active_deleted_invoices = set(DeletedInvoice.objects.values_list("invoice_uuid", flat=True)) | set(deleted_invoices_data)

                for inv_data in invoices_data:
                    inv_uuid = str(inv_data["invoice_uuid"])
                    if inv_uuid in active_deleted_invoices:
                        continue

                    # Determine specific client ownership per invoice
                    inv_owner = client_user
                    inv_uname = str(inv_data.get("client_username") or "").strip()
                    if inv_uname:
                        target_u = User.objects.filter(username=inv_uname).first()
                        if target_u:
                            inv_owner = target_u

                    paid_amt = inv_data.get("paid_amount", inv_data["grand_total"])
                    bal_amt = inv_data.get("balance_amount", Decimal("0.00"))
                    cust_phone = inv_data.get("customer_phone", "").strip()
                    cust_name = inv_data.get("customer_name", "Cash Customer").strip()

                    # Check or create customer if phone is provided
                    cust_obj = None
                    if cust_phone:
                        cust_q = Q(phone=cust_phone)
                        if inv_owner:
                            cust_q &= Q(client=inv_owner)
                        cust_obj = Customer.objects.filter(cust_q).first()
                        if not cust_obj and cust_name:
                            cust_obj = Customer.objects.create(
                                name=cust_name,
                                client=inv_owner,
                                phone=cust_phone,
                                email=inv_data.get("customer_email", ""),
                            )

                    cust_addr = inv_data.get("customer_address")
                    if not cust_addr and cust_obj and cust_obj.address:
                        cust_addr = cust_obj.address
                    cust_addr = str(cust_addr or "").strip()

                    # Check if invoice already exists: update financial fields and customer info!
                    existing_inv = Invoice.objects.filter(invoice_uuid=inv_uuid).first()
                    if existing_inv:
                        if not existing_inv.client and inv_owner:
                            existing_inv.client = inv_owner
                        if cust_obj and not existing_inv.customer:
                            existing_inv.customer = cust_obj
                        existing_inv.customer_name = cust_name or existing_inv.customer_name
                        existing_inv.customer_phone = cust_phone or existing_inv.customer_phone
                        existing_inv.customer_address = cust_addr or existing_inv.customer_address
                        existing_inv.subtotal = Decimal(str(inv_data["subtotal"]))
                        existing_inv.tax_amount = Decimal(str(inv_data.get("tax_amount", 0.00)))
                        existing_inv.discount_amount = Decimal(str(inv_data.get("discount_amount", 0.00)))
                        existing_inv.grand_total = Decimal(str(inv_data["grand_total"]))
                        existing_inv.paid_amount = Decimal(str(paid_amt))
                        existing_inv.balance_amount = Decimal(str(bal_amt))
                        existing_inv.payment_method = inv_data.get("payment_method", existing_inv.payment_method)
                        existing_inv.payment_status = inv_data.get("payment_status", existing_inv.payment_status)
                        existing_inv.notes = inv_data.get("notes", existing_inv.notes)
                        existing_inv.is_deleted = False
                        existing_inv.save()

                        # If items are provided on update, sync items
                        if inv_data.get("items"):
                            existing_inv.items.all().delete()
                            for item_data in inv_data["items"]:
                                sku = item_data.get("product_sku", "").strip()
                                product_match = None
                                if sku:
                                    product_match = Product.objects.filter(sku=sku, client=inv_owner).first()
                                    if not product_match and is_admin_actor:
                                        product_match = Product.objects.filter(sku=sku).first()
                                InvoiceItem.objects.create(
                                    invoice=existing_inv,
                                    product=product_match,
                                    product_name=item_data["product_name"],
                                    product_sku=sku,
                                    unit_price=item_data["unit_price"],
                                    quantity=item_data["quantity"],
                                    tax_percent=item_data.get("tax_percent", 0.00),
                                    tax_amount=item_data.get("tax_amount", 0.00),
                                    discount_percent=item_data.get("discount_percent", 0.00),
                                    total_price=item_data["total_price"],
                                )

                        synced_uuids.append(str(inv_uuid))
                        continue

                    # Ensure unique invoice_number on server
                    inv_num = inv_data["invoice_number"]
                    if Invoice.objects.filter(invoice_number=inv_num).exists():
                        existing_same = Invoice.objects.filter(invoice_number=inv_num, invoice_uuid=inv_uuid).first()
                        if not existing_same:
                            inv_num = f"{inv_num}-{str(inv_uuid)[:6]}"

                    invoice = Invoice.objects.create(
                        invoice_uuid=inv_uuid,
                        invoice_number=inv_num,
                        client=inv_owner,
                        customer=cust_obj,
                        customer_name=cust_name or "Cash Customer",
                        customer_phone=cust_phone,
                        customer_address=cust_addr,
                        subtotal=inv_data["subtotal"],
                        tax_amount=inv_data["tax_amount"],
                        discount_amount=inv_data["discount_amount"],
                        grand_total=inv_data["grand_total"],
                        paid_amount=paid_amt,
                        balance_amount=bal_amt,
                        payment_method=inv_data.get("payment_method", "Cash"),
                        payment_status=inv_data.get("payment_status", "Paid"),
                        source="windows_app",
                        notes=inv_data.get("notes", ""),
                        created_at=inv_data["created_at"],
                    )

                    # Create Invoice Items & update stock
                    for item_data in inv_data["items"]:
                        sku = item_data.get("product_sku", "").strip()
                        product_match = None
                        if sku:
                            product_match = Product.objects.filter(sku=sku, client=inv_owner).first()
                            if not product_match and is_admin_actor:
                                product_match = Product.objects.filter(sku=sku).first()
                            if product_match:
                                qty = Decimal(str(item_data["quantity"]))
                                product_match.stock_quantity = max(Decimal("0.00"), product_match.stock_quantity - qty)
                                product_match.save(update_fields=["stock_quantity", "updated_at"])

                        InvoiceItem.objects.create(
                            invoice=invoice,
                            product=product_match,
                            product_name=item_data["product_name"],
                            product_sku=sku,
                            unit_price=item_data["unit_price"],
                            quantity=item_data["quantity"],
                            tax_percent=item_data.get("tax_percent", 0.00),
                            tax_amount=item_data.get("tax_amount", 0.00),
                            discount_percent=item_data.get("discount_percent", 0.00),
                            total_price=item_data["total_price"],
                        )

                    synced_uuids.append(str(inv_uuid))

                # 4. Sync Payments (recorded offline)
                for pay_data in payments_data:
                    inv_num = pay_data.get("invoice_number")
                    amt = pay_data.get("amount")
                    method = pay_data.get("payment_method", "Cash")
                    notes = pay_data.get("notes", "")
                    inv = Invoice.objects.filter(invoice_number=inv_num).first()
                    if inv and amt:
                        PaymentRecord.objects.create(
                            invoice=inv,
                            amount=amt,
                            payment_method=method,
                            notes=notes or f"Payment received via {method}"
                        )
                        inv.paid_amount += amt
                        inv.payment_method = method
                        inv.save()
                        synced_payments.append(inv_num)

                # 5. Sync Clients created or updated offline (Admin action in Desktop App)
                clients_data = data.get("clients", [])
                synced_clients = []
                active_deleted_set = set(DeletedClient.objects.values_list("username", flat=True)) | set(deleted_clients_data)

                for cl_d in clients_data:
                    c_uname = cl_d.get("username", "").strip()
                    if not c_uname or c_uname in ("admin", "Mathan003") or c_uname in active_deleted_set:
                        continue

                    u_obj, u_created = User.objects.get_or_create(username=c_uname)
                    u_prof, _ = UserProfile.objects.get_or_create(user=u_obj)
                    if u_prof.is_deleted:
                        continue

                    # Compare updated_at: only overwrite if desktop update timestamp is newer than server
                    desktop_updated_str = cl_d.get("updated_at")
                    should_update = u_created
                    if not should_update and desktop_updated_str and u_prof.updated_at:
                        try:
                            from django.utils.dateparse import parse_datetime
                            desktop_dt = parse_datetime(desktop_updated_str)
                            if desktop_dt and desktop_dt >= u_prof.updated_at:
                                should_update = True
                        except Exception:
                            should_update = True
                    elif not should_update and not desktop_updated_str:
                        should_update = True

                    if should_update:
                        u_obj.first_name = cl_d.get("first_name", u_obj.first_name)
                        u_obj.last_name = cl_d.get("last_name", u_obj.last_name)
                        u_obj.email = cl_d.get("email", u_obj.email)
                        u_obj.is_active = cl_d.get("is_active", True)
                        if cl_d.get("password_hash") and (u_created or not u_obj.has_usable_password()):
                            u_obj.password = cl_d["password_hash"]
                        elif cl_d.get("initial_password") and (u_created or not u_obj.has_usable_password()):
                            u_obj.set_password(cl_d["initial_password"])
                        u_obj.save()

                        u_prof.role = cl_d.get("role", "client")
                        u_prof.shop_name = cl_d.get("shop_name", u_prof.shop_name)
                        u_prof.shop_address = cl_d.get("shop_address", u_prof.shop_address)
                        u_prof.business_type = cl_d.get("business_type", u_prof.business_type)
                        u_prof.access_mode = cl_d.get("access_mode", u_prof.access_mode)
                        cl_lim = cl_d.get("device_limit")
                        if cl_lim:
                            u_prof.device_limit = cl_lim
                        u_prof.phone = cl_d.get("phone", u_prof.phone)
                        u_prof.gst_number = cl_d.get("gst_number", u_prof.gst_number)
                        u_prof.bank_name = cl_d.get("bank_name", u_prof.bank_name)
                        u_prof.account_number = cl_d.get("account_number", u_prof.account_number)
                        u_prof.ifsc_code = cl_d.get("ifsc_code", u_prof.ifsc_code)
                        if cl_d.get("avatar_base64"):
                            u_prof.avatar_base64 = cl_d["avatar_base64"]
                        if cl_d.get("shop_logo_base64"):
                            u_prof.shop_logo_base64 = cl_d["shop_logo_base64"]
                        if cl_d.get("initial_password"):
                            u_prof.initial_password = cl_d["initial_password"]
                        u_prof.is_deleted = False
                        u_prof.save()
                    synced_clients.append(c_uname)

                # Log sync operation
                total_synced = len(synced_uuids) + len(synced_products) + len(synced_customers) + len(synced_payments) + len(synced_clients)
                SyncLog.objects.create(
                    device_id=device_id,
                    sync_type="push",
                    records_count=total_synced,
                    status="success",
                    details=f"Synced {len(synced_uuids)} invoices, {len(synced_products)} products, {len(synced_customers)} customers, {len(synced_payments)} payments, {len(synced_clients)} clients.",
                )

                # Keep client device session and registered device record active on cloud
                is_admin_push = (
                    (client_user and (client_user.is_superuser or (hasattr(client_user, "profile") and client_user.profile.role == "admin")))
                    or (client_username in ("admin", "Mathan003"))
                )

                if client_user and device_id and not is_admin_push:
                    ip_addr = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", "127.0.0.1")
                    reg_dev = RegisteredDevice.objects.filter(user=client_user, device_id=device_id).first()
                    if reg_dev and (not reg_dev.is_active or not reg_dev.is_verified):
                        # Device was explicitly revoked or deactivated by Administrator
                        session_key = f"EXE-{device_id}"[:40]
                        ActiveUserSession.objects.filter(session_key=session_key).delete()
                        ActiveUserSession.objects.filter(user=client_user, device_info__contains=device_id[:12]).delete()
                        return Response({
                            "status": "error",
                            "error": "DEVICE_REVOKED",
                            "session_revoked": True,
                            "message": "This device has been disconnected/revoked by Administrator.",
                        }, status=403)
                    elif not reg_dev:
                        # Auto-register device within client's device limit
                        p_prof = getattr(client_user, "profile", None)
                        dev_limit = p_prof.device_limit if (p_prof and p_prof.device_limit) else 5
                        cur_cnt = RegisteredDevice.objects.filter(user=client_user, is_active=True, is_verified=True).count()
                        if cur_cnt < dev_limit:
                            reg_dev = RegisteredDevice.objects.create(
                                user=client_user,
                                device_id=device_id,
                                device_name=f"Desktop POS ({device_id[:8]})",
                                device_type="desktop_exe",
                                ip_address=ip_addr,
                                is_active=True,
                                is_verified=True,
                            )
                        else:
                            return Response({
                                "status": "error",
                                "error": "DEVICE_LIMIT_EXCEEDED",
                                "message": f"Device limit exceeded ({cur_cnt}/{dev_limit}).",
                            }, status=403)

                    if reg_dev:
                        reg_dev.is_active = True
                        reg_dev.is_verified = True
                        reg_dev.ip_address = ip_addr
                        reg_dev.last_login = timezone.now()
                        reg_dev.save(update_fields=["is_active", "is_verified", "ip_address", "last_login"])

                        session_key = f"EXE-{device_id}"[:40]
                        ActiveUserSession.objects.update_or_create(
                            session_key=session_key,
                            defaults={
                                "user": client_user,
                                "device_info": f"Desktop POS (EXE): {reg_dev.device_name} ({device_id[:12]})",
                                "ip_address": ip_addr,
                                "last_activity": timezone.now(),
                            }
                        )

            # Calculate active devices count for client
            push_active_cnt = 1
            push_max_dev = 5
            if client_user:
                s_cnt = ActiveUserSession.objects.filter(user=client_user).count()
                r_cnt = RegisteredDevice.objects.filter(user=client_user, is_active=True, is_verified=True).count()
                p_prof = getattr(client_user, "profile", None)
                push_max_dev = p_prof.device_limit if (p_prof and p_prof.device_limit) else 5
                push_active_cnt = min(push_max_dev, max(s_cnt, r_cnt, 1))

            return Response({
                "status": "success",
                "synced_count": total_synced,
                "synced_invoices": synced_uuids,
                "synced_uuids": synced_uuids,
                "synced_products": synced_products,
                "synced_customers": synced_customers,
                "synced_payments": synced_payments,
                "synced_clients": synced_clients,
                "active_devices_count": push_active_cnt,
                "max_allowed_devices": push_max_dev,
                "server_time": timezone.now().isoformat(),
            })

        except Exception as e:
            logger.exception("Error syncing offline data")
            SyncLog.objects.create(
                device_id=device_id,
                sync_type="push",
                records_count=0,
                status="error",
                details=str(e),
            )
            return Response(
                {"status": "error", "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class SyncPullView(views.APIView):
    """
    Supplies the Windows Desktop App and Postman with latest product catalog (active and inactive),
    prices, stock levels, customers, and recent invoices from Django server.
    """
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        since_str = request.query_params.get("since")
        client_username = request.query_params.get("client_username", "").strip()
        device_id = request.query_params.get("device_id", "").strip()
        client_user = User.objects.filter(username=client_username).first() if client_username else None
        is_admin_actor = (
            (client_user and (client_user.is_superuser or (hasattr(client_user, "profile") and client_user.profile.role == "admin")))
            or (client_username in ("admin", "Mathan003"))
        )
        is_client_only = client_user and not is_admin_actor

        ip_addr = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", "127.0.0.1")
        session_revoked = False

        if client_user and device_id and not is_admin_actor:
            session_key = f"EXE-{device_id}"[:40]
            reg_dev = RegisteredDevice.objects.filter(user=client_user, device_id=device_id).first()
            if reg_dev and (not reg_dev.is_active or not reg_dev.is_verified):
                session_revoked = True
                ActiveUserSession.objects.filter(session_key=session_key).delete()
                ActiveUserSession.objects.filter(user=client_user, device_info__contains=device_id[:12]).delete()
            elif not reg_dev:
                # If not registered yet, auto-register within client's limit instead of locking out
                p_prof = getattr(client_user, "profile", None)
                dev_limit = p_prof.device_limit if (p_prof and p_prof.device_limit) else 5
                cur_cnt = RegisteredDevice.objects.filter(user=client_user, is_active=True, is_verified=True).count()
                if cur_cnt < dev_limit:
                    reg_dev = RegisteredDevice.objects.create(
                        user=client_user,
                        device_id=device_id,
                        device_name=f"Desktop POS ({device_id[:8]})",
                        device_type="desktop_exe",
                        ip_address=ip_addr,
                        is_active=True,
                        is_verified=True,
                    )
                else:
                    session_revoked = True
                    ActiveUserSession.objects.filter(session_key=session_key).delete()
                    ActiveUserSession.objects.filter(user=client_user, device_info__contains=device_id[:12]).delete()

            if reg_dev and not session_revoked:
                reg_dev.is_active = True
                reg_dev.is_verified = True
                reg_dev.ip_address = ip_addr
                reg_dev.last_login = timezone.now()
                reg_dev.save(update_fields=["is_active", "is_verified", "ip_address", "last_login"])

                ActiveUserSession.objects.update_or_create(
                    session_key=session_key,
                    defaults={
                        "user": client_user,
                        "device_info": f"Desktop POS (EXE): {reg_dev.device_name} ({device_id[:12]})",
                        "ip_address": ip_addr,
                        "last_activity": timezone.now(),
                    }
                )

        # Calculate live concurrent active devices count and limit for this client
        active_devices_count = 1
        max_allowed_devices = 5
        if client_user:
            from datetime import timedelta
            # Purge sessions with no heartbeat for over 48 hours to ensure clean count
            cutoff = timezone.now() - timedelta(hours=48)
            ActiveUserSession.objects.filter(user=client_user, last_activity__lt=cutoff).delete()

            sess_cnt = ActiveUserSession.objects.filter(user=client_user).count()
            reg_cnt = RegisteredDevice.objects.filter(user=client_user, is_active=True, is_verified=True).count()
            p = getattr(client_user, "profile", None)
            max_allowed_devices = p.device_limit if (p and p.device_limit) else 5
            active_devices_count = max(sess_cnt, reg_cnt, 1)
            active_devices_count = min(active_devices_count, max_allowed_devices)

        if is_client_only:
            has_any_cat = ProductCategory.objects.filter(client=client_user).exists()
            has_any_prod = Product.objects.filter(client=client_user).exists()
            if not has_any_cat and not has_any_prod:
                for c_name in ["General", "Grocery", "Fruits", "Vegetables", "Snacks", "Beverages", "Dairy", "Spices", "Stationery", "Electronics"]:
                    ProductCategory.objects.get_or_create(name=c_name, client=client_user, defaults={"is_deleted": False})

            from billing.models import DeletedProduct
            tombstone_skus = set(DeletedProduct.objects.filter(
                client=client_user
            ).values_list("sku", flat=True))
            soft_del_skus = set(Product.objects.filter(client=client_user, is_deleted=True).values_list("sku", flat=True))
            all_del_prod_skus = list(tombstone_skus | soft_del_skus)

            products_qs = Product.objects.filter(client=client_user, is_deleted=False, is_active=True).exclude(sku__in=all_del_prod_skus)
            deleted_products_qs = Product.objects.filter(client=client_user, is_deleted=True)
            customers_qs = Customer.objects.filter(client=client_user, is_deleted=False)
            categories_qs = ProductCategory.objects.filter(client=client_user, is_deleted=False)
            deleted_categories_qs = ProductCategory.objects.filter(client=client_user, is_deleted=True)
            invoices_qs = Invoice.objects.filter(client=client_user, is_deleted=False).prefetch_related("items").order_by("-created_at")[:200]
            branches_qs = Branch.objects.filter(Q(client=client_user) | Q(is_default=True, client__isnull=True), is_active=True)
            active_uuids = [str(u) for u in Invoice.objects.filter(client=client_user, is_deleted=False).values_list("invoice_uuid", flat=True)]
            active_customer_phones = list(Customer.objects.filter(client=client_user, is_deleted=False).exclude(phone="").values_list("phone", flat=True))
            active_product_skus = list(products_qs.values_list("sku", flat=True))
        elif is_admin_actor:
            from billing.models import DeletedProduct
            tombstone_skus = set(DeletedProduct.objects.values_list("sku", flat=True))
            soft_del_skus = set(Product.objects.filter(is_deleted=True).values_list("sku", flat=True))
            all_del_prod_skus = list(tombstone_skus | soft_del_skus)

            products_qs = Product.objects.filter(is_deleted=False, is_active=True).exclude(sku__in=all_del_prod_skus)
            deleted_products_qs = Product.objects.filter(is_deleted=True)
            customers_qs = Customer.objects.filter(is_deleted=False)
            categories_qs = ProductCategory.objects.filter(is_deleted=False)
            deleted_categories_qs = ProductCategory.objects.filter(is_deleted=True)
            invoices_qs = Invoice.objects.filter(is_deleted=False).prefetch_related("items").order_by("-created_at")[:200]
            branches_qs = Branch.objects.filter(is_active=True)
            active_uuids = [str(u) for u in Invoice.objects.filter(is_deleted=False).values_list("invoice_uuid", flat=True)]
            active_customer_phones = list(Customer.objects.filter(is_deleted=False).exclude(phone="").values_list("phone", flat=True))
            active_product_skus = list(products_qs.values_list("sku", flat=True))
        else:
            all_del_prod_skus = []
            # Unidentified client request: strictly return empty queries to prevent any data leak across clients!
            products_qs = Product.objects.none()
            deleted_products_qs = Product.objects.none()
            customers_qs = Customer.objects.none()
            categories_qs = ProductCategory.objects.filter(client__isnull=True, is_deleted=False)
            deleted_categories_qs = ProductCategory.objects.none()
            invoices_qs = Invoice.objects.none()
            branches_qs = Branch.objects.filter(client__isnull=True, is_active=True)
            active_uuids = []
            active_customer_phones = []
            active_product_skus = []

        if since_str:
            try:
                products_qs = products_qs.filter(updated_at__gte=since_str)
                customers_qs = customers_qs.filter(updated_at__gte=since_str)
            except Exception:
                pass

        products_data = ProductSerializer(products_qs, many=True).data
        customers_data = CustomerSerializer(customers_qs, many=True).data

        cs = CompanySettings.get_settings()
        company_data = {
            "company_name": getattr(cs, "company_name", "MathanHub"),
            "phone": getattr(cs, "phone", ""),
            "email": getattr(cs, "email", ""),
            "address": getattr(cs, "address", ""),
            "gst_number": getattr(cs, "gst_number", ""),
            "bank_name": getattr(cs, "bank_name", ""),
            "account_number": getattr(cs, "account_number", ""),
            "ifsc_code": getattr(cs, "ifsc_code", ""),
            "logo_base64": getattr(cs, "logo_base64", ""),
            "watermark_base64": getattr(cs, "watermark_base64", ""),
            "updated_at": cs.updated_at.isoformat() if (cs and hasattr(cs, "updated_at") and cs.updated_at) else "",
        }

        # Client profile metadata for instant reflection of Admin edits on Web
        client_profile_data = None
        if client_user and hasattr(client_user, "profile"):
            p = client_user.profile
            client_profile_data = {
                "username": client_user.username,
                "first_name": client_user.first_name,
                "last_name": client_user.last_name,
                "email": client_user.email,
                "is_active": client_user.is_active,
                "role": p.role,
                "shop_name": p.shop_name,
                "shop_address": p.shop_address,
                "business_type": p.business_type,
                "access_mode": p.access_mode,
                "device_limit": p.device_limit,
                "phone": p.phone,
                "gst_number": p.gst_number,
                "bank_name": p.bank_name,
                "account_number": p.account_number,
                "ifsc_code": p.ifsc_code,
                "avatar_base64": p.avatar_base64,
                "shop_logo_base64": p.shop_logo_base64,
            }

        branches_data = []
        for br in branches_qs:
            branches_data.append({
                "name": br.name,
                "branch_code": br.branch_code,
                "phone": br.phone,
                "email": br.email,
                "address": br.address,
                "manager_name": br.manager_name,
                "is_default": br.is_default,
            })

        # Cloud Invoices to pull down to desktop app (with items)
        invoices_data = []
        for inv in invoices_qs:
            items_list = []
            for item in inv.items.all():
                items_list.append({
                    "product_name": item.product_name,
                    "product_sku": item.product_sku,
                    "unit": item.unit,
                    "unit_price": str(item.unit_price),
                    "quantity": str(item.quantity),
                    "tax_percent": str(item.tax_percent),
                    "tax_amount": str(item.tax_amount),
                    "discount_percent": str(item.discount_percent),
                    "total_price": str(item.total_price),
                })
            invoices_data.append({
                "invoice_uuid": str(inv.invoice_uuid),
                "invoice_number": inv.invoice_number,
                "client_username": inv.client.username if inv.client else "",
                "branch_name": inv.branch_name,
                "customer_name": inv.customer_name,
                "customer_phone": inv.customer_phone,
                "customer_address": inv.customer_address,
                "subtotal": str(inv.subtotal),
                "tax_amount": str(inv.tax_amount),
                "discount_amount": str(inv.discount_amount),
                "grand_total": str(inv.grand_total),
                "paid_amount": str(inv.paid_amount),
                "balance_amount": str(inv.balance_amount),
                "payment_method": inv.payment_method,
                "payment_status": inv.payment_status,
                "source": inv.source,
                "notes": inv.notes,
                "created_at": inv.created_at.isoformat(),
                "items": items_list,
            })

        # Active clients catalog for seamless synchronization across web and desktop apps
        deleted_client_names = list(DeletedClient.objects.values_list("username", flat=True))
        deleted_profile_names = list(UserProfile.objects.filter(is_deleted=True).values_list("user__username", flat=True))
        all_deleted_clients = list(set(deleted_client_names + deleted_profile_names))

        clients_data = []
        for u in User.objects.filter(is_active=True).exclude(username="admin"):
            if u.username in all_deleted_clients:
                continue
            prof = getattr(u, "profile", None)
            if prof and prof.is_deleted:
                continue
            clients_data.append({
                "id": u.id,
                "username": u.username,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "email": u.email,
                "role": prof.role if prof else "client",
                "shop_name": prof.shop_name if prof else "",
                "shop_address": prof.shop_address if prof else "",
                "business_type": prof.business_type if prof else "grocery",
                "access_mode": prof.access_mode if prof else "online_offline",
                "device_limit": prof.device_limit if prof else 5,
                "phone": prof.phone if prof else "",
                "gst_number": prof.gst_number if prof else "",
                "bank_name": prof.bank_name if prof else "",
                "account_number": prof.account_number if prof else "",
                "ifsc_code": prof.ifsc_code if prof else "",
                "avatar_base64": prof.avatar_base64 if prof else "",
                "shop_logo_base64": prof.shop_logo_base64 if prof else "",
                "password_hash": u.password,
                "initial_password": getattr(prof, "initial_password", ""),
                "is_active": u.is_active,
                "updated_at": prof.updated_at.isoformat() if (prof and prof.updated_at) else "",
            })
        active_client_usernames = [u.username for u in User.objects.filter(is_active=True).exclude(username__in=all_deleted_clients)]

        reg_devices_data = []
        for rd in RegisteredDevice.objects.select_related("user").order_by("-last_login")[:50]:
            reg_devices_data.append({
                "id": rd.id,
                "username": rd.user.username,
                "device_id": rd.device_id,
                "device_name": rd.device_name,
                "device_type": rd.device_type,
                "ip_address": rd.ip_address,
                "is_active": rd.is_active,
                "is_verified": rd.is_verified,
            })

        del_cat_names = list(deleted_categories_qs.values_list("name", flat=True).distinct())
        del_cat_lower = {d.strip().lower() for d in del_cat_names if d}
        clean_cats = [c for c in categories_qs.values_list("name", flat=True).distinct() if c and (c.lower() == "general" or c.lower() not in del_cat_lower)]

        from billing.models import DeletedInvoice
        del_inv_scope = Q() if is_admin_actor else Q(client=client_user)
        deleted_invoices_list = list(set(
            list(DeletedInvoice.objects.filter(del_inv_scope).values_list("invoice_uuid", flat=True)) +
            list(Invoice.objects.filter(del_inv_scope, is_deleted=True).values_list("invoice_uuid", flat=True))
        ))

        return Response({
            "status": "success",
            "server_time": timezone.now().isoformat(),
            "products_count": len(products_data),
            "products": products_data,
            "deleted_products": list(set(list(deleted_products_qs.values_list("sku", flat=True)) + all_del_prod_skus)),
            "categories": clean_cats,
            "deleted_categories": del_cat_names,
            "deleted_clients": all_deleted_clients,
            "customers_count": len(customers_data),
            "customers": customers_data,
            "invoices_count": len(invoices_data),
            "invoices": invoices_data,
            "deleted_invoices": deleted_invoices_list,
            "clients": clients_data,
            "company_settings": company_data,
            "client_profile": client_profile_data,
            "branches": branches_data,
            "active_invoice_uuids": active_uuids,
            "active_customer_phones": active_customer_phones,
            "active_product_skus": active_product_skus,
            "active_client_usernames": active_client_usernames,
            "registered_devices": reg_devices_data,
            "session_revoked": session_revoked,
            "active_devices_count": active_devices_count,
            "max_allowed_devices": max_allowed_devices,
        })


class LiveStatusView(views.APIView):
    """
    Real-time status endpoint for web dashboard and billing terminals.
    Supplies live sales, invoice counts, customer collections, dues, and version timestamps
    so browser screens and desktop clients update automatically without manual page reloads.
    """
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        today = timezone.localtime().date()
        today_invoices = Invoice.objects.filter(created_at__date=today)
        today_sales = today_invoices.aggregate(s=Sum("grand_total"))["s"] or Decimal("0.00")
        today_bills_count = today_invoices.count()
        today_customer_paid = today_invoices.aggregate(s=Sum("paid_amount"))["s"] or Decimal("0.00")
        total_invoices = Invoice.objects.count()

        total_pending = Invoice.objects.filter(
            payment_status__in=["Pending", "Partial"]
        ).aggregate(p=Sum(F("grand_total") - F("paid_amount")))["p"] or Decimal("0.00")

        latest_prod = Product.objects.order_by("-updated_at").first()
        prod_ver = latest_prod.updated_at.isoformat() if (latest_prod and latest_prod.updated_at) else str(Product.objects.count())

        cs = CompanySettings.get_settings()
        company_ver = cs.updated_at.isoformat() if (cs and hasattr(cs, "updated_at") and cs.updated_at) else "v1"

        return Response({
            "status": "online",
            "server_time": timezone.now().isoformat(),
            "today_sales": str(today_sales),
            "today_bills_count": today_bills_count,
            "today_customer_paid": str(today_customer_paid),
            "total_pending_amount": str(total_pending),
            "total_invoices": total_invoices,
            "products_count": Product.objects.count(),
            "products_version": prod_ver,
            "company_version": company_ver,
        })


class UpdateCheckView(views.APIView):
    """
    Checks for available software updates.
    Returns the latest published version metadata, release notes, and download URL for Windows desktop POS.
    """
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        from .version import APP_VERSION, APP_TITLE, APP_RELEASE_NOTES
        from django.urls import reverse
        from django.conf import settings

        current_version = request.query_params.get("current_version", "").strip()
        latest = SoftwareUpdate.get_latest_update()

        if latest:
            latest_version = latest.version
            title = latest.title
            release_notes = latest.release_notes
            published_at = latest.published_at.isoformat() if latest.published_at else ""
        else:
            latest_version = APP_VERSION
            title = APP_TITLE
            release_notes = APP_RELEASE_NOTES
            published_at = timezone.now().isoformat()

        update_available = bool(current_version and current_version != latest_version)
        download_url = request.build_absolute_uri(reverse("billing:api-update-download-exe"))

        file_size = 0
        possible_paths = [
            settings.BASE_DIR.parent / "MathanHub.exe",
            settings.BASE_DIR / "MathanHub.exe",
            settings.BASE_DIR.parent / "SmartBillingPOS.exe",
            settings.BASE_DIR / "SmartBillingPOS.exe",
        ]
        for p in possible_paths:
            try:
                if p.exists() and p.is_file():
                    file_size = p.stat().st_size
                    break
            except Exception:
                pass

        return Response({
            "status": "success",
            "current_version": current_version,
            "latest_version": latest_version,
            "update_available": update_available,
            "title": title,
            "release_notes": release_notes,
            "published_at": published_at,
            "download_url": download_url,
            "file_size": file_size,
        })


class UpdateDownloadView(views.APIView):
    """
    Serves or redirects to the latest compiled Windows Desktop Executable binary.
    """
    authentication_classes = []
    permission_classes = []

    def _get_target_file(self):
        from django.conf import settings
        possible_paths = [
            settings.BASE_DIR.parent / "MathanHub.exe",
            settings.BASE_DIR / "MathanHub.exe",
            settings.BASE_DIR.parent / "dist" / "MathanHub.exe",
            settings.BASE_DIR.parent / "SmartBillingPOS.exe",
            settings.BASE_DIR / "SmartBillingPOS.exe",
        ]
        for p in possible_paths:
            try:
                if p.exists() and p.is_file():
                    return p
            except Exception:
                pass
        return None

    def get(self, request):
        import os
        from django.http import FileResponse, Http404, HttpResponseRedirect

        external_url = os.getenv("EXE_DOWNLOAD_URL")
        if external_url:
            return HttpResponseRedirect(external_url)

        target_file = self._get_target_file()
        if not target_file:
            return HttpResponseRedirect("https://github.com/mathan003/Billing-Software/raw/main/MathanHub.exe")

        response = FileResponse(
            open(target_file, "rb"),
            as_attachment=True,
            filename=target_file.name,
            content_type="application/vnd.microsoft.portable-executable"
        )
        response["Content-Length"] = target_file.stat().st_size
        return response

    def head(self, request):
        from django.http import HttpResponse
        target_file = self._get_target_file()
        if not target_file:
            return HttpResponse(status=404)
        response = HttpResponse()
        response["Content-Length"] = target_file.stat().st_size
        response["Content-Type"] = "application/vnd.microsoft.portable-executable"
        return response


class DeviceRevokeApiView(views.APIView):
    """Admin API endpoint to revoke or disconnect a device across cloud and local terminals"""
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        device_id = request.data.get("device_id", "").strip()
        username = request.data.get("username", "").strip()
        if not device_id:
            return Response({"status": "error", "message": "device_id required"}, status=400)

        user = User.objects.filter(username=username).first() if username else None
        q = RegisteredDevice.objects.filter(device_id=device_id)
        if user:
            q = q.filter(user=user)
        q.update(is_active=False, is_verified=False)
        q.delete()

        ActiveUserSession.objects.filter(session_key=f"EXE-{device_id}"[:40]).delete()
        if user:
            ActiveUserSession.objects.filter(user=user, device_info__contains=device_id[:12]).delete()

        return Response({"status": "success", "message": f"Device {device_id} revoked successfully"})


class DeviceLimitUpdateApiView(views.APIView):
    """Admin API endpoint to instantly update client device quota"""
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        username = request.data.get("username", "").strip()
        limit = request.data.get("device_limit")
        if not username or limit is None:
            return Response({"status": "error", "message": "username and device_limit required"}, status=400)

        user = User.objects.filter(username=username).first()
        if not user:
            return Response({"status": "error", "message": "User not found"}, status=404)

        try:
            new_limit = max(1, min(50, int(limit)))
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.device_limit = new_limit
            profile.save(update_fields=["device_limit"])
            return Response({"status": "success", "device_limit": new_limit})
        except Exception as e:
            return Response({"status": "error", "message": str(e)}, status=400)



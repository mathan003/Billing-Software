import os
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
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import HttpResponse, JsonResponse, HttpResponseForbidden
from django.views.decorators.cache import never_cache
from .models import (
    Product, ProductCategory, Customer, Invoice, InvoiceItem, Purchase,
    PaymentRecord, ActiveUserSession, RegisteredDevice, UserProfile, ActivityLog,
    CompanySettings, Branch, StockLog, SoftwareUpdate, purge_old_customer_data, log_activity,
    SavedReport, purge_expired_deleted_items, DeletedClient
)
from .device_utils import (
    get_hardware_device_id, get_desktop_pos_config, save_desktop_pos_config,
    clear_desktop_remembered_user, is_desktop_environment, check_internet_connection
)
from .pdf_generator import generate_invoice_pdf, generate_statement_pdf, generate_sales_report_pdf
from .views_admin import admin_module_view, admin_db_health_api
from .views_client import client_module_view, client_quick_status_api
from .views_customer import customer_module_view, customer_quick_metrics_api


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
            messages.warning(request, "Access Denied: You are in Client/Cashier mode. Please click 'Admin Panel (நிர்வாகம்)' on the navigation bar to unlock using the Administrator password.")
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
    return Q(client=request.user)


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


def get_date_bounds(start_d, end_d=None):
    """
    Constructs robust timezone-aware datetime bounds for date-range queries.
    Works seamlessly on both MySQL (where CONVERT_TZ may return NULL if time zones aren't loaded)
    and SQLite (where strings are stored in ISO format), guaranteeing zero date-shift errors.
    """
    from datetime import time as dt_time
    if end_d is None:
        end_d = start_d
    start_dt = timezone.make_aware(datetime.combine(start_d, dt_time.min))
    end_dt = timezone.make_aware(datetime.combine(end_d, dt_time.max))
    return start_dt, end_dt


def check_user_has_gst(user):
    """
    Returns True only if the specified user or shop has an active, non-empty GST number.
    GST is strictly optional and not mandatory for clients. If not configured, tax calculation is skipped.
    """
    if not user or not user.is_authenticated:
        return False
    if hasattr(user, "profile") and user.profile.gst_number and user.profile.gst_number.strip():
        return True
    if is_admin_user(user):
        from .models import CompanySettings
        c_set = CompanySettings.objects.first()
        if c_set and c_set.gst_number and c_set.gst_number.strip():
            return True
    return False


def get_available_categories(request):
    """
    Returns a sorted distinct list of categories combining ProductCategory records,
    distinct Product categories, and default retail departments for the active client/admin.
    Guarantees strict multi-tenant isolation and persistent category deletion across page refreshes.
    """
    if not request.user.is_authenticated:
        return ["General"]

    c_filter = get_client_filter(request)
    client_user = get_client_user(request)

    # Initial seeding: ONLY if this client has zero categories (neither active nor deleted)
    # AND zero products created. Once initialized or if any categories/tombstones exist, never re-seed!
    if not is_admin_user(request.user) and client_user:
        has_any_cat = ProductCategory.objects.filter(client=client_user).exists()
        has_any_prod = Product.objects.filter(client=client_user).exists()
        if not has_any_cat and not has_any_prod:
            initial_cats = [
                "General", "Grocery", "Fruits", "Vegetables",
                "Snacks", "Beverages", "Dairy", "Spices",
                "Stationery", "Electronics"
            ]
            for c_name in initial_cats:
                ProductCategory.objects.get_or_create(name=c_name, client=client_user, defaults={"is_deleted": False})

    # Find all deleted categories for this client and globally
    deleted_names = set(ProductCategory.objects.filter(c_filter, is_deleted=True).values_list("name", flat=True))
    if client_user:
        deleted_names |= set(ProductCategory.objects.filter(client=client_user, is_deleted=True).values_list("name", flat=True))
    deleted_names_lower = {d.strip().lower() for d in deleted_names if d}

    # Active saved categories
    saved_cats = set(ProductCategory.objects.filter(c_filter, is_deleted=False).exclude(name="").values_list("name", flat=True))

    # Product categories from active products
    prod_cats = set(Product.objects.filter(c_filter, is_active=True, is_deleted=False).exclude(category="").values_list("category", flat=True))

    # Exclude deleted categories from both saved_cats and prod_cats
    active_saved = {c.strip() for c in saved_cats if c and c.strip().lower() not in deleted_names_lower}
    active_prod = {c.strip() for c in prod_cats if c and c.strip().lower() not in deleted_names_lower}

    all_cats = {"General"} | active_saved | active_prod
    return sorted([c for c in all_cats if c and (c.lower() == "general" or c.lower() not in deleted_names_lower)])





def trigger_desktop_sync_safe():
    """Triggers background desktop sync to immediately reflect changes on web cloud database"""
    try:
        try:
            from sync_manager import trigger_desktop_sync
            trigger_desktop_sync()
        except ImportError:
            from windows_app.sync_manager import trigger_desktop_sync
            trigger_desktop_sync()
    except Exception:
        pass


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
    login_admin = request.GET.get("login_admin") == "1"

    if (switch_user or login_admin) and is_desktop:
        clear_desktop_remembered_user()
        pos_cfg = get_desktop_pos_config()

    remembered_username = pos_cfg.get("remembered_username", "") if is_desktop else ""
    if login_admin:
        remembered_username = "Mathan003"
    is_first_time_desktop = is_desktop and not bool(pos_cfg.get("is_activated"))
    device_id = get_hardware_device_id() if is_desktop else (
        request.POST.get("device_id") or request.COOKIES.get("billing_device_id") or f"WEB-{uuid.uuid4().hex[:12].upper()}"
    )

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        admin_otp = request.POST.get("admin_otp", "").strip()

        device_type = "desktop_exe" if is_desktop else "web_browser"
        device_name = "Windows POS Terminal" if is_desktop else (request.META.get("HTTP_USER_AGENT", "Web Browser")[:100])
        ip_addr = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", "127.0.0.1")

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

        # Check whether this physical device is already verified locally for THIS user
        is_device_verified_locally = (
            is_desktop and
            RegisteredDevice.objects.filter(user__username=username, device_id=device_id, is_verified=True).exists()
        )
        is_admin_candidate = username in ("admin", "Mathan003") or User.objects.filter(username=username, is_superuser=True).exists()

        # In Desktop App:
        # If device is NOT yet verified locally for this client OR admin_otp is supplied,
        # authentication & OTP verification MUST be routed to Railway Cloud!
        cloud_err_msg = ""
        user = None

        if is_desktop and not is_admin_candidate and (not is_device_verified_locally or bool(admin_otp)):
            has_internet, net_msg = check_internet_connection()
            if not has_internet:
                messages.error(
                    request,
                    "First-time system activation requires an active internet connection to register this computer with the cloud server. "
                    "முதல் முறை உள்நுழைய இணைய இணைப்பு (Internet) கட்டாயமாகும். Please connect to internet and retry."
                )
                return render(request, "billing/login.html", {
                    "is_desktop_app": is_desktop,
                    "remembered_username": "",
                    "is_first_time_desktop": True,
                    "device_id": device_id,
                    "otp_required": bool(admin_otp),
                    "username": username,
                    "password": password,
                })

            candidate_urls = []
            cfg_url = pos_cfg.get("server_url", "").strip() if is_desktop else ""
            if cfg_url:
                candidate_urls.append(cfg_url.rstrip("/"))
            railway_url = "https://billing-software-production-d0f2.up.railway.app"
            if railway_url not in candidate_urls:
                candidate_urls.append(railway_url)
            env_url = os.getenv("CLOUD_SERVER_URL", "").strip()
            if env_url and env_url not in candidate_urls:
                candidate_urls.append(env_url.rstrip("/"))
            for dev_url in ["http://127.0.0.1:8000", "http://localhost:8000"]:
                if dev_url not in candidate_urls:
                    candidate_urls.append(dev_url)

            cloud_success = False
            for candidate_url in candidate_urls:
                candidate_url = candidate_url.rstrip("/")
                try:
                    import requests
                    auth_payload = {
                        "username": username,
                        "password": password,
                        "device_id": device_id,
                        "device_name": device_name,
                    }
                    if admin_otp:
                        auth_payload["admin_otp"] = admin_otp

                    auth_resp = requests.post(
                        f"{candidate_url}/api/sync/auth/",
                        json=auth_payload,
                        timeout=7.0
                    )
                    if auth_resp.status_code == 200:
                        auth_data = auth_resp.json()
                        if auth_data.get("status") == "otp_required":
                            messages.info(
                                request,
                                "New system detected. A 6-digit activation code has been generated in the Website Admin Panel. Please ask your administrator for the code."
                            )
                            return render(request, "billing/login.html", {
                                "is_desktop_app": is_desktop,
                                "remembered_username": "",
                                "device_id": device_id,
                                "otp_required": True,
                                "username": username,
                                "password": password,
                            })
                        elif auth_data.get("status") == "success":
                            u_data = auth_data.get("user", {})
                            p_data = auth_data.get("profile", {})
                            # Provision or update client user in local SQLite DB
                            loc_u, _ = User.objects.get_or_create(username=username)
                            loc_u.email = u_data.get("email", loc_u.email)
                            loc_u.first_name = u_data.get("first_name", loc_u.first_name)
                            loc_u.last_name = u_data.get("last_name", loc_u.last_name)
                            loc_u.is_staff = u_data.get("is_staff", False)
                            loc_u.is_superuser = u_data.get("is_superuser", False)
                            loc_u.set_password(password)  # Store password hash locally so subsequent logins work offline!
                            loc_u.save()

                            loc_p, _ = UserProfile.objects.get_or_create(user=loc_u)
                            loc_p.role = p_data.get("role", loc_p.role)
                            loc_p.shop_name = p_data.get("shop_name", loc_p.shop_name)
                            loc_p.shop_address = p_data.get("shop_address", loc_p.shop_address)
                            loc_p.business_type = p_data.get("business_type", loc_p.business_type)
                            loc_p.access_mode = p_data.get("access_mode", loc_p.access_mode)
                            loc_p.device_limit = p_data.get("device_limit", loc_p.device_limit)
                            loc_p.phone = p_data.get("phone", loc_p.phone)
                            loc_p.gst_number = p_data.get("gst_number", loc_p.gst_number)
                            loc_p.bank_name = p_data.get("bank_name", loc_p.bank_name)
                            loc_p.account_number = p_data.get("account_number", loc_p.account_number)
                            loc_p.ifsc_code = p_data.get("ifsc_code", loc_p.ifsc_code)
                            loc_p.avatar_base64 = p_data.get("avatar_base64", loc_p.avatar_base64)
                            loc_p.shop_logo_base64 = p_data.get("shop_logo_base64", loc_p.shop_logo_base64)
                            loc_p.save()

                            # Register this device locally as verified
                            RegisteredDevice.objects.update_or_create(
                                user=loc_u,
                                device_id=device_id,
                                defaults={
                                    "device_name": device_name,
                                    "device_type": device_type,
                                    "ip_address": ip_addr,
                                    "is_active": True,
                                    "is_verified": True,
                                    "otp_code": "",
                                }
                            )

                            cloud_success = True
                            user = authenticate(request, username=username, password=password)
                            break
                    elif auth_resp.status_code == 400 and auth_resp.json().get("status") == "invalid_otp":
                        messages.error(
                            request,
                            "Invalid or expired 6-Digit Admin Verification OTP. Please check the code in the Website Admin Panel."
                        )
                        return render(request, "billing/login.html", {
                            "is_desktop_app": is_desktop,
                            "remembered_username": "",
                            "device_id": device_id,
                            "otp_required": True,
                            "username": username,
                            "password": password,
                        })
                    elif auth_resp.status_code == 403:
                        cloud_err_msg = auth_resp.json().get("message", "Device Limit Exceeded on Cloud Server.")
                        break
                    elif auth_resp.status_code == 401:
                        cloud_err_msg = auth_resp.json().get("message", "Invalid username or password.")
                        break
                except Exception:
                    continue

            if not cloud_success and user is None:
                messages.error(
                    request,
                    cloud_err_msg or "Failed to connect to Railway Cloud server to verify device. Please check your internet connection."
                )
                return render(request, "billing/login.html", {
                    "is_desktop_app": is_desktop,
                    "remembered_username": remembered_username,
                    "is_first_time_desktop": is_first_time_desktop,
                    "device_id": device_id,
                    "otp_required": bool(admin_otp),
                    "username": username,
                    "password": password,
                })
        else:
            # Device already verified or admin user: authenticate locally
            user = authenticate(request, username=username, password=password)
            if user is None and is_desktop:
                # If credentials failed locally, check Railway Cloud in case password was updated
                candidate_urls = []
                cfg_url = pos_cfg.get("server_url", "").strip()
                if cfg_url:
                    candidate_urls.append(cfg_url.rstrip("/"))
                candidate_urls.append("https://billing-software-production-d0f2.up.railway.app")
                for candidate_url in candidate_urls:
                    try:
                        import requests
                        auth_resp = requests.post(
                            f"{candidate_url}/api/sync/auth/",
                            json={"username": username, "password": password, "device_id": device_id, "device_name": device_name},
                            timeout=5.0
                        )
                        if auth_resp.status_code == 200 and auth_resp.json().get("status") == "success":
                            loc_u, _ = User.objects.get_or_create(username=username)
                            loc_u.set_password(password)
                            loc_u.save()
                            user = authenticate(request, username=username, password=password)
                            if user:
                                break
                    except Exception:
                        continue

        if user is None:
            messages.error(request, cloud_err_msg or "Invalid username or password. Please try again.")
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
            admin_limit = profile.device_limit if (profile and profile.device_limit) else (2 if user.username in ("Mathan003", "admin") else 1)
            if user.username == "Mathan003":
                admin_limit = max(2, admin_limit)

            # Evict only oldest active sessions if limit exceeded
            cur_sessions = list(ActiveUserSession.objects.filter(user=user).order_by("last_activity"))
            while len(cur_sessions) >= admin_limit:
                oldest = cur_sessions.pop(0)
                try:
                    from django.contrib.sessions.models import Session
                    Session.objects.filter(session_key=oldest.session_key).delete()
                except Exception:
                    pass
                oldest.delete()

            RegisteredDevice.objects.update_or_create(
                user=user,
                device_id=device_id,
                defaults={
                    "device_name": device_name,
                    "device_type": device_type,
                    "ip_address": ip_addr,
                    "is_active": True,
                    "is_verified": True,
                }
            )
        else:
            # Client: Admin specifies allowed devices (e.g. 5 devices). 6th device is STRICTLY BLOCKED!
            dev_limit = profile.device_limit if (profile and profile.device_limit) else 5

            # Check if this physical device/token is already verified
            existing_device = RegisteredDevice.objects.filter(
                user=user,
                device_id=device_id,
                is_active=True,
                is_verified=True
            ).first()

            if not existing_device:
                current_active_devices = RegisteredDevice.objects.filter(user=user, is_active=True, is_verified=True).count()
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

                pending_device, _ = RegisteredDevice.objects.get_or_create(
                    user=user,
                    device_id=device_id,
                    defaults={
                        "device_name": device_name,
                        "device_type": device_type,
                        "ip_address": ip_addr,
                        "is_active": True,
                        "is_verified": False,
                    }
                )

                if admin_otp:
                    if pending_device.verify_otp(admin_otp):
                        log_activity(
                            request,
                            "DEVICE_VERIFY",
                            f"Admin OTP verified successfully for device '{device_name}' ({device_id[:12]}). Client '{user.username}' authorized.",
                            ip_address=ip_addr
                        )
                    else:
                        messages.error(request, "Invalid or expired 6-Digit Admin Verification OTP. Please check the code in the Website Admin Panel.")
                        return render(request, "billing/login.html", {
                            "is_desktop_app": is_desktop,
                            "device_id": device_id,
                            "otp_required": True,
                            "username": username,
                            "password": password,
                        })
                else:
                    otp_code = pending_device.generate_otp()
                    pending_device.ip_address = ip_addr
                    pending_device.device_name = device_name
                    pending_device.save(update_fields=["ip_address", "device_name"])

                    log_activity(
                        request,
                        "DEVICE_OTP",
                        f"New system activation request for client '{user.username}' on '{device_name}' (IP: {ip_addr}). Admin 6-Digit Verification OTP: {otp_code}",
                        ip_address=ip_addr
                    )
                    messages.info(request, "New system detected. A 6-digit activation code has been generated in the Website Admin Panel. Please ask your administrator for the code.")
                    return render(request, "billing/login.html", {
                        "is_desktop_app": is_desktop,
                        "device_id": device_id,
                        "otp_required": True,
                        "username": username,
                        "password": password,
                    })
            else:
                existing_device.ip_address = ip_addr
                existing_device.device_name = device_name
                existing_device.save(update_fields=["ip_address", "device_name", "last_login"])

        # 4. Save persistent local configuration & Erase previous client data if switching client
        if is_desktop or os.environ.get("USE_SQLITE") == "True":
            is_user_admin = user.is_superuser or (getattr(user, "profile", None) and user.profile.role == "admin")
            last_client = pos_cfg.get("last_logged_in_client", "")
            if not is_user_admin:
                # Multi-tenant safety: Preserve all client data locally without wiping.
                # Each client's records are strictly isolated and queried by client_id.

                save_desktop_pos_config({
                    "device_id": device_id,
                    "remembered_username": user.username,
                    "last_logged_in_client": user.username,
                    "shop_name": getattr(profile, "shop_name", ""),
                    "is_activated": True,
                })
            else:
                # When Admin logs in, preserve client data on terminal
                save_desktop_pos_config({
                    "device_id": device_id,
                    "is_activated": True,
                })

            # Trigger immediate background sync to pull latest data for this client from cloud
            try:
                from sync_manager import trigger_desktop_sync
                trigger_desktop_sync()
            except Exception:
                pass

        # 5. Login and create session
        login(request, user)
        if not request.session.session_key:
            request.session.save()

        session_key = request.session.session_key
        sec_token = uuid.uuid4().hex[:12]
        request.session["sec_token"] = sec_token

        ActiveUserSession.objects.update_or_create(
            session_key=session_key[:40],
            defaults={
                "user": user,
                "device_info": f"{device_name} ({device_id[:12]})",
                "ip_address": ip_addr,
            }
        )

        role_title = f"Administrator ({profile.device_limit if profile else 2} Devices)" if is_admin_user else f"Client Operator (Max {profile.device_limit if profile else 5} Devices)"
        log_activity(
            request,
            "LOGIN",
            f"{role_title} '{user.username}' logged in successfully ({device_name} | IP: {ip_addr})."
        )

        messages.success(request, f"Welcome back, {user.username}!")
        next_url = request.GET.get("next") or request.POST.get("next")
        if next_url and next_url.startswith("/"):
            target_url = f"{next_url}{'&' if '?' in next_url else '?'}sec={sec_token}"
        elif login_admin or (is_admin_user and not is_desktop):
            target_url = f"/admin-panel/?sec={sec_token}"
        else:
            target_url = f"/?sec={sec_token}"
        response = redirect(target_url)
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
        ActiveUserSession.objects.filter(session_key=session_key[:40]).delete()
    logout(request)
    msg = "You have been logged out successfully. Please enter your password to unlock POS." if is_desktop_environment(request) else "You have been logged out successfully. Please enter your username and password to log in."
    messages.info(request, msg)
    return redirect("billing:login")


# ==========================================
# DASHBOARD (Metrics, Add Customer, Quick Bill)
# ==========================================

def dashboard(request):
    today = timezone.localdate()
    today_start, today_end = get_date_bounds(today)
    c_filter = get_client_filter(request)
    b_filter = get_branch_filter(request)

    # 1. Today's Invoices & Total Sales scoped to client
    today_invoices = Invoice.objects.filter(
        c_filter,
        is_deleted=False,
        created_at__gte=today_start,
        created_at__lte=today_end
    ).select_related("customer", "branch").prefetch_related("items").order_by("-created_at")
    today_sales = today_invoices.aggregate(Sum("grand_total"))["grand_total__sum"] or Decimal("0.00")
    today_bills_count = today_invoices.count()

    # 2. Customer Paid TODAY (replaces previous Total Buy!)
    client_invoices_all = Invoice.objects.filter(c_filter, is_deleted=False)
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
    low_stock_count = Product.objects.filter(c_filter, is_deleted=False, stock_quantity__lte=5, is_active=True).count()

    # 4. Pending Customers Breakdown (Customers who currently have pending payments)
    pending_customers_qs = Customer.objects.filter(c_filter, is_deleted=False).annotate(
        calc_pending=Sum("invoices__balance_amount", filter=Q(invoices__is_deleted=False)),
        calc_billed=Sum("invoices__grand_total", filter=Q(invoices__is_deleted=False)),
        calc_paid=Sum("invoices__paid_amount", filter=Q(invoices__is_deleted=False)),
        due_bills_count=Count("invoices", filter=Q(invoices__balance_amount__gt=0, invoices__is_deleted=False))
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
    customers = Customer.objects.filter(c_filter, is_deleted=False).order_by("name")
    products = Product.objects.filter(c_filter, is_deleted=False, is_active=True).order_by("name_tamil", "name")
    categories = get_available_categories(request)
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
        "categories": categories,
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

@never_cache
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
        customer_address = request.POST.get("customer_address", "").strip()
        branch_id = request.POST.get("branch_id", "").strip()
        payment_method = request.POST.get("payment_method", "Cash")
        paid_amount_str = request.POST.get("paid_amount", "").strip()
        notes = request.POST.get("notes", "").strip()

        product_ids = request.POST.getlist("product_id[]") or request.POST.getlist("product_id")
        product_names = request.POST.getlist("product_name[]") or request.POST.getlist("product_name")
        units = request.POST.getlist("unit[]") or request.POST.getlist("unit")
        unit_prices = request.POST.getlist("unit_price[]") or request.POST.getlist("unit_price")
        quantities = request.POST.getlist("quantity[]") or request.POST.getlist("quantity")

        total_row_count = max(len(product_ids), len(product_names))
        if total_row_count == 0:
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
            cust_obj = Customer.objects.filter(c_filter, is_deleted=False, pk=customer_id).first()
            if cust_obj and not customer_name:
                customer_name = cust_obj.name
            if cust_obj and not customer_phone and cust_obj.phone:
                customer_phone = cust_obj.phone
            if cust_obj and customer_address and cust_obj.address != customer_address:
                cust_obj.address = customer_address
                cust_obj.save(update_fields=["address", "updated_at"])
        elif customer_phone:
            cust_obj = Customer.objects.filter(
                phone=customer_phone,
                client=client_user,
                is_deleted=False
            ).first()
            if not cust_obj:
                cust_obj = Customer.objects.create(
                    phone=customer_phone,
                    client=client_user,
                    name=customer_name or "Cash Customer",
                    address=customer_address
                )
            elif customer_address and cust_obj.address != customer_address:
                cust_obj.address = customer_address
                cust_obj.save(update_fields=["address", "updated_at"])
        elif customer_name and customer_name != "Cash Customer" and customer_address:
            cust_obj = Customer.objects.filter(c_filter, is_deleted=False, name__iexact=customer_name).first()
            if not cust_obj:
                cust_obj = Customer.objects.create(
                    name=customer_name,
                    client=client_user,
                    address=customer_address
                )
            elif customer_address and cust_obj.address != customer_address:
                cust_obj.address = customer_address
                cust_obj.save(update_fields=["address", "updated_at"])

        final_customer_name = (cust_obj.name if cust_obj else customer_name) or "Cash Customer"
        final_customer_phone = (cust_obj.phone if cust_obj else customer_phone) or ""
        final_customer_address = customer_address or (cust_obj.address if (cust_obj and cust_obj.address) else "") or ""

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
        has_shop_gst = check_user_has_gst(client_user)

        for i in range(total_row_count):
            pid = str(product_ids[i]).strip() if i < len(product_ids) and product_ids[i] else ""
            item_name = str(product_names[i]).strip() if i < len(product_names) and product_names[i] else ""
            unit = units[i].strip() if i < len(units) and units[i] else "KG"
            qty = parse_decimal(quantities[i] if i < len(quantities) else "1", "1")

            if qty <= Decimal("0.00"):
                continue

            product = None
            if pid and pid != "custom" and pid.isdigit():
                product = Product.objects.filter(c_filter, is_deleted=False, pk=int(pid), is_active=True).first()

            if product:
                price = parse_decimal(unit_prices[i] if i < len(unit_prices) else str(product.price), str(product.price))
                if not unit:
                    unit = product.unit
                tax_pct = product.tax_percent if has_shop_gst else Decimal("0.00")
                item_title = product.display_name
                item_sku = product.sku
            elif item_name:
                price = parse_decimal(unit_prices[i] if i < len(unit_prices) else "0.00", "0.00")
                tax_pct = Decimal("0.00")
                item_title = item_name
                item_sku = "CUSTOM"
            else:
                continue

            item_subtotal = price * qty
            if has_shop_gst and tax_pct > Decimal("0.00"):
                item_tax = (item_subtotal * tax_pct) / Decimal("100.00")
            else:
                item_tax = Decimal("0.00")
            item_total = item_subtotal + item_tax

            subtotal += item_subtotal
            total_tax += item_tax

            line_items.append({
                "product": product,
                "name": item_title,
                "sku": item_sku,
                "unit": unit,
                "unit_price": price,
                "quantity": qty,
                "tax_percent": tax_pct,
                "tax_amount": item_tax,
                "total_price": item_total,
            })

        if not line_items:
            messages.error(request, "Please add at least one valid product with quantity > 0.")
            return redirect("billing:billing_page")

        # Strict Stock Validation & Out-of-Stock Prevention
        # Aggregate quantities across line items by product to prevent exceeding stock across split rows
        requested_by_product = {}
        for item in line_items:
            prod = item["product"]
            if prod:
                requested_by_product[prod.id] = requested_by_product.get(prod.id, Decimal("0.00")) + item["quantity"]

        for prod_id, total_req_qty in requested_by_product.items():
            prod = Product.objects.filter(pk=prod_id).first()
            if prod:
                if prod.stock_quantity <= Decimal("0.00"):
                    messages.error(
                        request,
                        f"Out of Stock: '{prod.display_name}' is currently out of stock (0 {prod.unit} available). Cannot create bill for this item. (பொருள் கையிருப்பில் இல்லை)"
                    )
                    return redirect("billing:billing_page")
                elif total_req_qty > prod.stock_quantity:
                    messages.error(
                        request,
                        f"Insufficient Stock: '{prod.display_name}' has only {prod.stock_quantity} {prod.unit} available, but {total_req_qty} {prod.unit} was requested. Cannot exceed available stock. (கையிருப்பு போதாது)"
                    )
                    return redirect("billing:billing_page")

        discount_amount = parse_decimal(request.POST.get("discount_amount", "0"), "0.00")
        max_discount = subtotal + total_tax
        if discount_amount > max_discount:
            discount_amount = max_discount

        computed_grand_total = max(Decimal("0.00"), subtotal + total_tax - discount_amount)
        # Allow manual editing of Grand Total (increase / decrease / round off)
        manual_grand_total_str = request.POST.get("grand_total", "").strip()
        if manual_grand_total_str != "":
            grand_total = max(Decimal("0.00"), parse_decimal(manual_grand_total_str, str(computed_grand_total)))
        else:
            grand_total = computed_grand_total

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
            customer_address=final_customer_address,
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

            # Decrement stock if standard catalog product
            prod = item["product"]
            if prod:
                old_stock = prod.stock_quantity
                new_stock = max(Decimal("0.00"), old_stock - item["quantity"])
                prod.stock_quantity = new_stock
                prod.save(update_fields=["stock_quantity", "updated_at"])

                # Create StockLog entry for audit ledger
                try:
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
                except Exception:
                    pass

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

        trigger_desktop_sync_safe()

        messages.success(request, f"Invoice {inv_number} created successfully! Grand Total: ₹{grand_total}")
        return redirect(f"/invoices/{invoice.id}/?autoprint=1")

    # GET request
    c_filter = get_client_filter(request)
    b_filter = get_branch_filter(request)
    products = Product.objects.filter(c_filter, is_active=True, is_deleted=False).order_by("name_tamil", "name")
    categories = get_available_categories(request)
    customers = Customer.objects.filter(c_filter, is_deleted=False).order_by("name")
    branches = Branch.objects.filter(b_filter).order_by("-is_default", "name")
    default_branch = Branch.get_default_branch()
    return render(request, "billing/billing_screen.html", {
        "products": products,
        "categories": categories,
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

            product = get_object_or_404(Product.objects.filter(c_filter, is_active=True, is_deleted=False), pk=product_id)
            if product.stock_quantity <= Decimal("0.00"):
                messages.error(
                    request,
                    f"Out of Stock: '{product.display_name}' is currently out of stock (0 {product.unit} available). Cannot create bill for this item. (பொருள் கையிருப்பில் இல்லை)"
                )
                return redirect("billing:dashboard")
            elif qty > product.stock_quantity:
                messages.error(
                    request,
                    f"Insufficient Stock: '{product.display_name}' has only {product.stock_quantity} {product.unit} available, but {qty} {product.unit} was requested. Cannot exceed available stock. (கையிருப்பு போதாது)"
                )
                return redirect("billing:dashboard")

            unit_price = product.price
            subtotal = unit_price * qty
            has_shop_gst = check_user_has_gst(client_user)
            if has_shop_gst:
                tax_amount = (subtotal * product.tax_percent) / Decimal("100.00")
                item_tax_pct = product.tax_percent
            else:
                tax_amount = Decimal("0.00")
                item_tax_pct = Decimal("0.00")

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
                cust_obj = Customer.objects.filter(c_filter, is_deleted=False, pk=customer_id).first()
                if cust_obj and not customer_name:
                    customer_name = cust_obj.name
                if cust_obj and not customer_phone and cust_obj.phone:
                    customer_phone = cust_obj.phone
            elif customer_phone:
                cust_obj = Customer.objects.filter(
                    phone=customer_phone,
                    client=client_user,
                    is_deleted=False
                ).first()
                if not cust_obj:
                    cust_obj = Customer.objects.create(
                        phone=customer_phone,
                        client=client_user,
                        name=customer_name or "Cash Customer"
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
                tax_percent=item_tax_pct,
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

            trigger_desktop_sync_safe()

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
    invoices = Invoice.objects.filter(c_filter, is_deleted=False).select_related("customer", "branch").prefetch_related("items", "payments").order_by("-created_at")
    search = request.GET.get("search", "").strip()
    status_filter = request.GET.get("status", "").strip()
    payment_method = request.GET.get("method", "").strip()
    customer_id = request.GET.get("customer", "").strip()

    selected_customer = None
    customer_stats = None

    if customer_id:
        try:
            selected_customer = Customer.objects.filter(c_filter, is_deleted=False, pk=customer_id).first()
            if selected_customer:
                cust_query = Q(customer=selected_customer)
                if selected_customer.phone:
                    cust_query |= Q(customer_phone=selected_customer.phone)
                invoices = invoices.filter(cust_query)

                all_cust_invs = Invoice.objects.filter(c_filter, is_deleted=False).filter(cust_query)
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

    customers = Customer.objects.filter(c_filter, is_deleted=False).order_by("name")

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
    invoice = get_object_or_404(Invoice.objects.filter(c_filter, is_deleted=False).prefetch_related("items", "payments"), pk=invoice_id)
    company = CompanySettings.get_settings()
    profile = getattr(request.user, "profile", None) if request.user.is_authenticated else None
    if not profile and invoice.client and hasattr(invoice.client, "profile"):
        profile = invoice.client.profile

    paper_size = profile.print_paper_size if profile else "80mm"
    paper_width_mm = profile.effective_paper_width_mm if profile else Decimal("80.0")
    paper_height_mode = profile.print_paper_height_mode if profile else "auto"
    paper_height_mm = profile.effective_paper_height_mm if profile else None
    auto_expand = profile.print_auto_expand_height if profile else True
    font_scaling = profile.print_font_scaling if profile else "auto"
    dimensions_display = profile.paper_dimensions_display if profile else "80mm × Auto-Fit"

    return render(request, "billing/invoice_detail.html", {
        "invoice": invoice,
        "company": company,
        "profile": profile,
        "print_paper_size": paper_size,
        "print_paper_width_mm": paper_width_mm,
        "print_paper_height_mode": paper_height_mode,
        "print_paper_height_mm": paper_height_mm,
        "print_auto_expand_height": auto_expand,
        "print_font_scaling": font_scaling,
        "print_dimensions_display": dimensions_display,
    })


def invoice_delete(request, invoice_id):
    """
    Moves an individual invoice to the Recycle Bin / Deleted Items (3-day recovery window).
    Restores inventory product stock while in the recycle bin.
    """
    if not request.user.is_authenticated:
        return redirect("billing:login")
    if request.method == "POST":
        c_filter = get_client_filter(request)
        invoice = get_object_or_404(Invoice.objects.filter(c_filter, is_deleted=False).prefetch_related("items"), pk=invoice_id)
        inv_number = invoice.invoice_number
        inv_uuid = str(invoice.invoice_uuid)

        # Restore inventory stock while invoice is in recycle bin
        for item in invoice.items.all():
            if item.product:
                Product.objects.filter(pk=item.product.id).update(
                    stock_quantity=F("stock_quantity") + item.quantity
                )

        # Soft-delete: Move to Recycle Bin (3-day recovery window)
        invoice.is_deleted = True
        invoice.deleted_at = timezone.now()
        invoice.save(update_fields=["is_deleted", "deleted_at"])

        from billing.models import DeletedInvoice
        DeletedInvoice.objects.update_or_create(
            invoice_uuid=inv_uuid,
            defaults={
                "invoice_number": inv_number,
                "client": invoice.client,
                "deleted_at": timezone.now()
            }
        )

        log_activity(
            request,
            "RECYCLE_BIN_DELETE",
            f"User '{request.user.username}' moved invoice #{inv_number} (UUID: {inv_uuid}) to Recycle Bin (3-day recovery). Stock restored."
        )
        trigger_desktop_sync_safe()
        messages.success(request, f"Invoice #{inv_number} moved to Recycle Bin (Deleted Items). It will remain recoverable for 3 days before permanent deletion.")
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
    invoice = get_object_or_404(Invoice.objects.filter(c_filter, is_deleted=False).prefetch_related("items"), pk=invoice_id)
    products = Product.objects.filter(c_filter, is_active=True, is_deleted=False).order_by("name_tamil", "name")
    customers = Customer.objects.filter(c_filter, is_deleted=False).order_by("name")

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

        # Validate that updated quantities do not exceed available restored stock
        edit_requested_by_prod = {}
        for i in range(len(product_ids)):
            pid = product_ids[i]
            if pid and pid.isdigit():
                q_val = parse_decimal(quantities[i] if i < len(quantities) else "1", "1")
                if q_val > Decimal("0.00"):
                    p_int = int(pid)
                    edit_requested_by_prod[p_int] = edit_requested_by_prod.get(p_int, Decimal("0.00")) + q_val

        for p_id, total_req in edit_requested_by_prod.items():
            check_p = Product.objects.filter(pk=p_id).first()
            if check_p:
                if check_p.stock_quantity <= Decimal("0.00"):
                    messages.error(
                        request,
                        f"Out of Stock: '{check_p.display_name}' has 0 {check_p.unit} available. Cannot update bill. (பொருள் கையிருப்பில் இல்லை)"
                    )
                    return redirect("billing:invoice_edit", invoice_id=invoice.id)
                elif total_req > check_p.stock_quantity:
                    messages.error(
                        request,
                        f"Insufficient Stock: '{check_p.display_name}' has only {check_p.stock_quantity} {check_p.unit} available, but {total_req} {check_p.unit} requested. (கையிருப்பு போதாது)"
                    )
                    return redirect("billing:invoice_edit", invoice_id=invoice.id)

        # 2. Delete existing items to replace with updated items
        invoice.items.all().delete()

        # 3. Associate Customer
        cust_obj = None
        if customer_id:
            cust_obj = Customer.objects.filter(c_filter, is_deleted=False, pk=customer_id).first()
        elif customer_phone:
            client_user = invoice.client or (request.user if request.user.is_authenticated else None)
            cust_obj = Customer.objects.filter(
                phone=customer_phone,
                client=client_user,
                is_deleted=False
            ).first()
            if not cust_obj:
                cust_obj = Customer.objects.create(
                    phone=customer_phone,
                    client=client_user,
                    name=customer_name
                )

        invoice.customer = cust_obj
        invoice.customer_name = cust_obj.name if cust_obj else customer_name
        invoice.customer_phone = cust_obj.phone if cust_obj else customer_phone
        invoice.payment_method = payment_method
        invoice.notes = notes

        # 4. Process updated line items
        subtotal = Decimal("0.00")
        total_tax = Decimal("0.00")
        target_client = invoice.client or request.user
        has_shop_gst = check_user_has_gst(target_client)

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
            if has_shop_gst:
                line_tax = (line_sub * prod.tax_percent) / Decimal("100.00")
                line_tax_pct = prod.tax_percent
            else:
                line_tax = Decimal("0.00")
                line_tax_pct = Decimal("0.00")
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
                tax_percent=line_tax_pct,
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

        manual_grand = request.POST.get("grand_total", "").strip()
        if manual_grand != "":
            invoice.grand_total = max(Decimal("0.00"), parse_decimal(manual_grand, str(old_total)))
        else:
            invoice.grand_total = max(Decimal("0.00"), subtotal + total_tax - invoice.discount_amount)

        # Allow cashier to adjust paid_amount (e.g. refunded customer on product returns)
        manual_paid = request.POST.get("paid_amount", "").strip()
        if manual_paid != "":
            invoice.paid_amount = max(Decimal("0.00"), parse_decimal(manual_paid, str(invoice.paid_amount)))

        invoice.notes = (invoice.notes or "").replace("[CLOUD_SYNCED]", "").strip()
        invoice.save()  # Auto updates grand_total, balance_amount, and payment_status

        return_msg = ""
        if old_total > invoice.grand_total:
            return_diff = old_total - invoice.grand_total
            return_msg = f" (Returned goods value: ₹{return_diff:.2f} safely returned to stock)"

        log_activity(
            request,
            "INVOICE_EDIT",
            f"Altered/edited bill #{invoice.invoice_number} for customer '{invoice.customer_name}'. Grand Total: ₹{old_total} -> ₹{invoice.grand_total}{return_msg}. Paid: ₹{invoice.paid_amount}, Remaining Balance: ₹{invoice.balance_amount}"
        )

        trigger_desktop_sync_safe()

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
    """Generates and serves downloadable PDF receipt with company branding and configured paper size"""
    c_filter = get_client_filter(request)
    invoice = get_object_or_404(Invoice.objects.filter(c_filter, is_deleted=False).prefetch_related("items"), pk=invoice_id)
    profile = getattr(request.user, "profile", None) if request.user.is_authenticated else None
    if not profile and invoice.client and hasattr(invoice.client, "profile"):
        profile = invoice.client.profile
    pdf_bytes = generate_invoice_pdf(invoice, client_profile=profile)

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
        invoice = get_object_or_404(Invoice.objects.filter(c_filter, is_deleted=False), pk=invoice_id)
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
            invoice.notes = (invoice.notes or "").replace("[CLOUD_SYNCED]", "").strip()
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

            trigger_desktop_sync_safe()

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
        invoice = get_object_or_404(Invoice.objects.filter(c_filter, is_deleted=False), pk=invoice_id)
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

            trigger_desktop_sync_safe()

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

@never_cache
def product_list(request):
    """
    Products page:
    Add, Edit, Update, and Remove products anytime.
    Bilingual (Tamil primary, English optional).
    Units: KG, Pack, Box, Pcs, Ltr.
    """
    c_filter = get_client_filter(request)
    client_u = get_client_user(request)

    # Initial isolated product seeding for new clients with zero products
    if client_u and not is_admin_user(request.user):
        if not Product.objects.filter(client=client_u).exists():
            from billing.models import DeletedProduct
            if not DeletedProduct.objects.filter(client=client_u).exists():
                starter_prods = [
                    ("பொன்னி அரிசி", "Ponni Rice", "SKU-RICE-01", "KG", 55.00, 45.00, 1000, "Grocery"),
                    ("துவரம் பருப்பு", "Toor Dal", "SKU-DAL-01", "KG", 160.00, 140.00, 500, "Grocery"),
                    ("சர்க்கரை", "Sugar", "SKU-SUGAR-01", "KG", 42.00, 36.00, 800, "Grocery"),
                    ("காபி தூள்", "Filter Coffee Powder", "SKU-COFFEE-01", "Pack", 120.00, 95.00, 200, "Beverages"),
                    ("ஆப்பிள் பாக்ஸ்", "Apple Box", "SKU-APPLE-BOX", "Box", 1200.00, 950.00, 50, "Fruits"),
                ]
                for p_tam, p_eng, p_sku, p_unit, p_pr, p_cpr, p_stk, p_cat in starter_prods:
                    Product.objects.get_or_create(
                        sku=p_sku,
                        client=client_u,
                        defaults={
                            "name_tamil": p_tam,
                            "name": p_eng,
                            "unit": p_unit,
                            "price": p_pr,
                            "cost_price": p_cpr,
                            "stock_quantity": p_stk,
                            "category": p_cat,
                            "is_active": True,
                            "is_deleted": False,
                        }
                    )

    products = Product.objects.filter(c_filter, is_active=True, is_deleted=False).order_by("name_tamil", "name")
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

    categories = get_available_categories(request)
    cat_counts_map = dict(
        Product.objects.filter(c_filter, is_active=True, is_deleted=False)
        .values("category")
        .annotate(cnt=Count("id"))
        .values_list("category", "cnt")
    )
    category_list_data = [
        {
            "name": cat_name,
            "product_count": cat_counts_map.get(cat_name, 0),
            "is_default": (cat_name.lower() == "general"),
        }
        for cat_name in categories
    ]

    return render(request, "billing/products.html", {
        "products": products,
        "categories": categories,
        "category_list_data": category_list_data,
        "search": search,
        "selected_category": category,
        "unit_choices": Product.UNIT_CHOICES,
    })


def category_add(request):
    """Add or restore a product category with persistent state"""
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        is_ajax = (
            request.headers.get("x-requested-with") == "XMLHttpRequest"
            or request.content_type == "application/json"
        )
        if not name:
            if is_ajax:
                return JsonResponse({"status": "error", "message": "Please enter a valid category name."}, status=400)
            messages.error(request, "Please enter a valid category name.")
        else:
            client_user = get_client_user(request)
            c_filter = get_client_filter(request)
            existing_cat = ProductCategory.objects.filter(c_filter, name__iexact=name).first()
            if existing_cat:
                if existing_cat.is_deleted:
                    existing_cat.is_deleted = False
                    existing_cat.deleted_at = None
                    existing_cat.save(update_fields=["is_deleted", "deleted_at"])
                    log_activity(
                        request,
                        "PRODUCT_ADD",
                        f"Restored product category '{name}'"
                    )
                    trigger_desktop_sync_safe()
                    msg = f"Category '{name}' restored successfully!"
                    if is_ajax:
                        return JsonResponse({"status": "success", "message": msg, "name": name, "id": existing_cat.id})
                    messages.success(request, msg)
                else:
                    if is_ajax:
                        return JsonResponse({"status": "info", "message": f"Category '{name}' already exists.", "name": name})
                    messages.info(request, f"Category '{name}' already exists.")
            else:
                cat = ProductCategory.objects.create(name=name, client=client_user, is_deleted=False)
                log_activity(
                    request,
                    "PRODUCT_ADD",
                    f"Created new product category '{name}'"
                )
                trigger_desktop_sync_safe()
                msg = f"Category '{name}' added successfully! You can now select it when adding products."
                if is_ajax:
                    return JsonResponse({"status": "success", "message": msg, "name": name, "id": cat.id})
                messages.success(request, msg)

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or ""
    if next_url and next_url.startswith("/"):
        return redirect(next_url)
    return redirect("billing:product_list")


def category_delete(request):
    """Permanently delete / tombstone a product category so it never resurrects, and reassign its products to 'General'"""
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        is_ajax = (
            request.headers.get("x-requested-with") == "XMLHttpRequest"
            or request.content_type == "application/json"
        )
        if not name:
            if is_ajax:
                return JsonResponse({"status": "error", "message": "Please specify a category name to remove."}, status=400)
            messages.error(request, "Please specify a valid category name to remove.")
        elif name.lower() == "general":
            msg = "The default 'General' category cannot be deleted as it is required as a fallback."
            if is_ajax:
                return JsonResponse({"status": "error", "message": msg}, status=400)
            messages.warning(request, msg)
        else:
            c_filter = get_client_filter(request)
            client_user = get_client_user(request)
            clean_name = name.strip()

            # Reassign all affected products under this scope to 'General'
            affected_count = Product.objects.filter(c_filter, category__iexact=clean_name).update(category="General")

            # Persistent tombstone soft-deletion in ProductCategory table
            matching_cats = ProductCategory.objects.filter(c_filter, name__iexact=clean_name)
            if matching_cats.exists():
                matching_cats.update(is_deleted=True, deleted_at=timezone.now())
            if client_user:
                ProductCategory.objects.get_or_create(
                    name=clean_name,
                    client=client_user,
                    defaults={"is_deleted": True, "deleted_at": timezone.now()}
                )
                ProductCategory.objects.filter(client=client_user, name__iexact=clean_name).update(
                    is_deleted=True,
                    deleted_at=timezone.now()
                )

            log_activity(
                request,
                "PRODUCT_DELETE",
                f"Removed category '{clean_name}' (reassigned {affected_count} product(s) to 'General')"
            )
            trigger_desktop_sync_safe()

            success_msg = f"Category '{clean_name}' removed permanently! ({affected_count} product(s) reassigned to 'General')"
            if is_ajax:
                return JsonResponse({
                    "status": "success",
                    "message": success_msg,
                    "name": clean_name,
                    "reassigned_count": affected_count,
                })
            messages.success(request, success_msg)

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or ""
    if next_url and next_url.startswith("/"):
        return redirect(next_url)
    return redirect("billing:product_list")


@never_cache
def product_add(request):
    """Add a new product with Tamil & English names and Unit choices"""
    is_ajax = (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in request.headers.get("accept", "")
        or request.content_type == "application/json"
    )
    if request.method == "POST":
        name_tamil = request.POST.get("name_tamil", "").strip()
        name = request.POST.get("name", "").strip()
        sku = request.POST.get("sku", "").strip()
        category = request.POST.get("category", "General").strip() or "General"
        unit = request.POST.get("unit", "KG").strip() or "KG"
        custom_unit = request.POST.get("custom_unit", "").strip()
        if unit == "Other" and custom_unit:
            unit = custom_unit[:20]
        elif unit == "Other":
            unit = "Unit"

        price = parse_decimal(request.POST.get("price"), "0.00")
        cost_price = parse_decimal(request.POST.get("cost_price"), "0.00")
        tax_percent = parse_decimal(request.POST.get("tax_percent"), "0.00")
        stock = parse_decimal(request.POST.get("stock_quantity"), "0.00")

        if not name_tamil and not name:
            if is_ajax:
                return JsonResponse({
                    "status": "error",
                    "message": "Please enter at least a Tamil or English product name (தமிழ் அல்லது ஆங்கிலப் பெயர் உள்ளிடவும்)."
                }, status=400)
            messages.error(request, "Please enter at least a Tamil or English product name.")
            return redirect("billing:product_list")

        if not sku:
            sku = f"SKU-{timezone.now().strftime('%y%m%d%H%M%S')}"

        c_filter = get_client_filter(request)
        client_u = get_client_user(request)

        # Check if an active product already exists with this SKU
        if Product.objects.filter(c_filter, sku=sku, is_deleted=False).exists():
            if is_ajax:
                return JsonResponse({
                    "status": "error",
                    "message": f"An active product with SKU '{sku}' already exists."
                }, status=400)
            messages.error(request, f"An active product with SKU '{sku}' already exists.")
            return redirect("billing:product_list")

        # If a soft-deleted product with this SKU exists, reactivate and update it
        soft_deleted = Product.objects.filter(c_filter, sku=sku, is_deleted=True).first()
        if soft_deleted:
            prod = soft_deleted
            prod.name_tamil = name_tamil
            prod.name = name
            prod.client = client_u
            prod.category = category
            prod.unit = unit
            prod.price = price
            prod.cost_price = cost_price
            prod.tax_percent = tax_percent
            prod.stock_quantity = stock
            prod.is_active = True
            prod.is_deleted = False
            prod.deleted_at = None
            prod.save()
        else:
            prod = Product.objects.create(
                name_tamil=name_tamil,
                name=name,
                client=client_u,
                sku=sku,
                category=category,
                unit=unit,
                price=price,
                cost_price=cost_price,
                tax_percent=tax_percent,
                stock_quantity=stock,
                is_active=True,
                is_deleted=False,
            )

        # Clear any deletion tombstone for this SKU
        from billing.models import DeletedProduct
        del_prod_q = Q(sku=sku)
        if client_u:
            del_prod_q &= (Q(client=client_u) | Q(client__isnull=True))
        DeletedProduct.objects.filter(del_prod_q).delete()

        log_activity(
            request,
            "PRODUCT_ADD",
            f"Added product '{prod.display_name}' (SKU: {prod.sku}, Unit: {prod.unit}, Price: ₹{prod.price}, Stock: {prod.stock_quantity})"
        )
        trigger_desktop_sync_safe()

        if is_ajax:
            return JsonResponse({
                "status": "success",
                "message": f"Product '{name_tamil or name}' added successfully!",
                "product": {
                    "id": prod.id,
                    "name_tamil": prod.name_tamil or "",
                    "name": prod.name or "",
                    "display_name": prod.display_name,
                    "sku": prod.sku,
                    "category": prod.category,
                    "unit": prod.unit,
                    "price": f"{prod.price:.2f}",
                    "cost_price": f"{prod.cost_price:.2f}",
                    "tax_percent": f"{prod.tax_percent:.2f}",
                    "stock_quantity": f"{prod.stock_quantity:.2f}" if prod.stock_quantity % 1 else str(int(prod.stock_quantity)),
                }
            })

        messages.success(request, f"Product '{name_tamil or name}' added successfully!")

    return redirect("billing:product_list")


@never_cache
def product_edit(request, product_id):
    """Edit / Update product details (Price, Stock, Unit, Names) anytime"""
    is_ajax = (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in request.headers.get("accept", "")
        or request.content_type == "application/json"
    )
    c_filter = get_client_filter(request)
    product = Product.objects.filter(c_filter, pk=product_id, is_deleted=False).first()
    if not product and is_admin_user(request.user):
        product = Product.objects.filter(pk=product_id, is_deleted=False).first()
    elif not product:
        product = Product.objects.filter(Q(client=request.user) | Q(client__isnull=True), pk=product_id, is_deleted=False).first()

    if not product:
        if is_ajax:
            return JsonResponse({"status": "error", "message": "Product not found or has already been removed."}, status=404)
        messages.error(request, "Product not found or has already been removed.")
        return redirect("billing:product_list")

    if request.method == "POST":
        product.name_tamil = request.POST.get("name_tamil", product.name_tamil).strip()
        product.name = request.POST.get("name", "").strip()
        product.category = request.POST.get("category", product.category).strip() or product.category
        
        unit = request.POST.get("unit", product.unit).strip() or product.unit
        custom_unit = request.POST.get("custom_unit", "").strip()
        if unit == "Other" and custom_unit:
            unit = custom_unit[:20]
        elif unit == "Other":
            unit = product.unit or "Unit"
        product.unit = unit
        product.price = parse_decimal(request.POST.get("price"), str(product.price))
        product.cost_price = parse_decimal(request.POST.get("cost_price"), str(product.cost_price))
        product.tax_percent = parse_decimal(request.POST.get("tax_percent"), str(product.tax_percent))
        product.stock_quantity = parse_decimal(request.POST.get("stock_quantity"), str(product.stock_quantity))
        product.is_active = True
        product.is_deleted = False
        product.deleted_at = None
        product.save()

        # Clear any tombstone for this SKU
        from billing.models import DeletedProduct
        del_prod_q = Q(sku=product.sku)
        if product.client:
            del_prod_q &= (Q(client=product.client) | Q(client__isnull=True))
        DeletedProduct.objects.filter(del_prod_q).delete()

        log_activity(
            request,
            "PRODUCT_EDIT",
            f"Updated product '{product.display_name}' (SKU: {product.sku}, Unit: {product.unit}, Price: ₹{product.price}, Stock: {product.stock_quantity})"
        )
        trigger_desktop_sync_safe()

        if is_ajax:
            return JsonResponse({
                "status": "success",
                "message": f"Product '{product.display_name}' updated successfully!",
                "product": {
                    "id": product.id,
                    "name_tamil": product.name_tamil or "",
                    "name": product.name or "",
                    "display_name": product.display_name,
                    "sku": product.sku,
                    "category": product.category,
                    "unit": product.unit,
                    "price": f"{product.price:.2f}",
                    "cost_price": f"{product.cost_price:.2f}",
                    "tax_percent": f"{product.tax_percent:.2f}",
                    "stock_quantity": f"{product.stock_quantity:.2f}" if product.stock_quantity % 1 else str(int(product.stock_quantity)),
                }
            })

        messages.success(request, f"Product '{product.display_name}' updated successfully!")

    return redirect("billing:product_list")


@never_cache
def product_delete(request, product_id):
    """Moves product to Recycle Bin (3-day recovery window) and records tombstone without 404 crashes"""
    if not request.user.is_authenticated:
        return redirect("billing:login")
    is_ajax = (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in request.headers.get("accept", "")
        or request.content_type == "application/json"
    )
    if request.method == "POST":
        c_filter = get_client_filter(request)
        client_u = get_client_user(request)

        # Resilient lookup: check client filter, admin scope, or unassigned global products
        product = Product.objects.filter(c_filter, pk=product_id).first()
        if not product and is_admin_user(request.user):
            product = Product.objects.filter(pk=product_id).first()
        elif not product:
            product = Product.objects.filter(Q(client=request.user) | Q(client__isnull=True), pk=product_id).first()

        if not product:
            if is_ajax:
                return JsonResponse({"status": "info", "message": "Product has already been removed or does not exist.", "product_id": product_id})
            messages.info(request, "Product has already been removed or does not exist.")
            return redirect("billing:product_list")

        if product.is_deleted:
            # Already deleted! Ensure tombstone is recorded and return cleanly
            from billing.models import DeletedProduct
            DeletedProduct.objects.update_or_create(
                sku=product.sku,
                client=product.client or client_u,
                defaults={"name": product.display_name, "deleted_at": timezone.now()}
            )
            if is_ajax:
                return JsonResponse({"status": "info", "message": f"Product '{product.display_name}' is already in the Recycle Bin.", "product_id": product_id})
            messages.info(request, f"Product '{product.display_name}' is already in the Recycle Bin.")
            return redirect("billing:product_list")

        prod_name = product.display_name
        sku = product.sku
        owner = product.client or client_u

        # Soft delete: move to Recycle Bin
        product.is_active = False
        product.is_deleted = True
        product.deleted_at = timezone.now()
        product.save(update_fields=["is_active", "is_deleted", "deleted_at", "updated_at"])

        # Also mark all matching SKU instances under this scope as deleted
        scope_q = Q(client=owner) | Q(client__isnull=True) if owner else Q()
        Product.objects.filter(sku=sku).filter(scope_q).update(
            is_active=False,
            is_deleted=True,
            deleted_at=timezone.now(),
            updated_at=timezone.now()
        )

        # Record persistent tombstone so background sync never revives it
        from billing.models import DeletedProduct
        DeletedProduct.objects.update_or_create(
            sku=sku,
            client=owner,
            defaults={"name": prod_name, "deleted_at": timezone.now()}
        )

        log_activity(
            request,
            "RECYCLE_BIN_DELETE",
            f"User '{request.user.username}' moved product '{prod_name}' (SKU: {sku}) to Recycle Bin (3-day recovery)."
        )
        trigger_desktop_sync_safe()

        if is_ajax:
            return JsonResponse({
                "status": "success",
                "message": f"Product '{prod_name}' moved to Recycle Bin.",
                "product_id": product_id,
                "sku": sku
            })

        messages.success(request, f"Product '{prod_name}' moved to Recycle Bin (Deleted Items). Recoverable for 3 days.")

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
        if phone and Customer.objects.filter(c_filter, is_deleted=False, phone=phone).exists():
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
            trigger_desktop_sync_safe()
            messages.success(request, f"Customer '{name}' added successfully!")

    return redirect(request.META.get("HTTP_REFERER", "billing:dashboard"))


def customer_edit(request, customer_id):
    """Alter / Edit customer details"""
    c_filter = get_client_filter(request)
    customer = get_object_or_404(Customer.objects.filter(c_filter, is_deleted=False), pk=customer_id)

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
        trigger_desktop_sync_safe()
        messages.success(request, f"Customer '{customer.name}' updated successfully!")

    return redirect(request.META.get("HTTP_REFERER", "billing:dashboard"))


def admin_quick_unlock(request):
    """
    Allows Administrator (e.g. Mathan003) to instantly unlock and open the Admin Panel
    from any active POS terminal or client session.
    """
    if request.method != "POST":
        return redirect("billing:admin_panel")

    admin_username = request.POST.get("admin_username", "Mathan003").strip()
    admin_password = request.POST.get("admin_password", "")

    if not admin_password:
        messages.error(request, "Admin password is required to unlock the Admin Panel.")
        return redirect(request.META.get("HTTP_REFERER", "billing:dashboard"))

    user = authenticate(request, username=admin_username, password=admin_password)
    if not user:
        admin_obj = User.objects.filter(username=admin_username).first()
        if admin_obj and admin_obj.check_password(admin_password):
            user = admin_obj

    if not user:
        messages.error(request, "Invalid administrator password or username. Admin panel access denied.")
        return redirect(request.META.get("HTTP_REFERER", "billing:dashboard"))

    is_admin = user.is_superuser or (hasattr(user, "profile") and user.profile.role == "admin")
    if not is_admin:
        messages.error(request, f"User '{admin_username}' does not have Administrator privileges.")
        return redirect(request.META.get("HTTP_REFERER", "billing:dashboard"))

    # Switch session to administrator
    login(request, user)
    if not request.session.session_key:
        request.session.save()

    session_key = request.session.session_key
    sec_token = uuid.uuid4().hex[:12]
    request.session["sec_token"] = sec_token

    device_id = get_hardware_device_id() if is_desktop_environment(request) else (
        request.COOKIES.get("billing_device_id") or f"WEB-{uuid.uuid4().hex[:12].upper()}"
    )

    ActiveUserSession.objects.update_or_create(
        session_key=session_key[:40],
        defaults={
            "user": user,
            "device_info": f"Admin Direct Terminal ({device_id[:12]})",
            "ip_address": request.META.get("REMOTE_ADDR", "127.0.0.1"),
        }
    )

    log_activity(
        request,
        "LOGIN",
        f"Administrator '{user.username}' unlocked Admin Panel from active terminal session."
    )
    messages.success(request, f"Administrator unlocked successfully! Welcome, {user.username}.")
    response = redirect(f"/admin-panel/?sec={sec_token}")
    response.set_cookie("billing_device_id", device_id, max_age=365*24*3600*5)
    return response


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
    deleted_client_names = set(DeletedClient.objects.values_list("username", flat=True))
    users = User.objects.select_related("profile").prefetch_related("active_sessions").order_by("username")
    clients = [
        u for u in users
        if getattr(u, "profile", None)
        and u.profile.role == "client"
        and not getattr(u.profile, "is_deleted", False)
        and u.username not in deleted_client_names
    ]
    for c in clients:
        c.invoice_count = Invoice.objects.filter(client=c, is_deleted=False).count()
        c.customer_count = Customer.objects.filter(client=c, is_deleted=False).count()
        c.product_count = Product.objects.filter(client=c, is_deleted=False).count()
        c.sales_total = Invoice.objects.filter(client=c, is_deleted=False).aggregate(Sum("grand_total"))["grand_total__sum"] or Decimal("0.00")
    admin_users = [u for u in users if u.is_superuser or (getattr(u, "profile", None) and u.profile.role == "admin")]

    active_device_sessions = ActiveUserSession.objects.select_related("user", "user__profile").order_by("-last_activity")
    registered_devices = RegisteredDevice.objects.select_related("user", "user__profile").order_by("-last_login")
    pending_device_otps = RegisteredDevice.objects.filter(is_verified=False).exclude(otp_code="").select_related("user", "user__profile").order_by("-otp_created_at")

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

    all_customers = Customer.objects.filter(is_deleted=False).select_related("client").order_by("-created_at")
    for cust in all_customers:
        cust.bill_count = Invoice.objects.filter(customer=cust, is_deleted=False).count()
        cust.total_spend = Invoice.objects.filter(customer=cust, is_deleted=False).aggregate(Sum("grand_total"))["grand_total__sum"] or Decimal("0.00")

    context = {
        "admin_users": admin_users,
        "clients": clients,
        "branches": branches,
        "all_customers": all_customers,
        "active_device_sessions": active_device_sessions,
        "registered_devices": registered_devices,
        "pending_device_otps": pending_device_otps,
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

        # Remove from DeletedClient blacklist if admin intentionally creates this client
        DeletedClient.objects.filter(username=username).delete()

        is_admin_role = (role == "admin")
        existing_user = User.objects.filter(username=username).first()
        if existing_user:
            existing_prof = getattr(existing_user, "profile", None)
            if existing_prof and existing_prof.is_deleted:
                user = existing_user
                user.email = email
                user.set_password(password)
                user.first_name = first_name
                user.last_name = last_name
                user.is_active = True
                user.is_staff = is_admin_role
                user.save()
                profile = existing_prof
                profile.is_deleted = False
                profile.deleted_at = None
            else:
                messages.error(request, f"Username '{username}' already exists. Please choose a different username.")
                return redirect("billing:admin_panel")
        else:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                is_staff=is_admin_role,
            )
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.is_deleted = False
            profile.deleted_at = None
        profile.phone = phone
        profile.shop_name = shop_name
        profile.shop_address = shop_address
        profile.business_type = business_type
        profile.access_mode = access_mode
        profile.device_limit = max(2, device_limit) if (is_admin_role and username in ("Mathan003", "admin")) else (2 if is_admin_role else device_limit)
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

        device_info_str = f"{profile.device_limit} Devices Allowed" if is_admin_role else f"Up to {profile.device_limit} Concurrent Devices Allowed"
        b_type_display = "Mobile & Computer Shop" if business_type == "mobile_computer" else "Grocery Shop"
        log_activity(
            request,
            "USER_CREATE",
            f"Created new {role.upper()} account '{username}' ({b_type_display}, Shop: {shop_name or 'N/A'}, Phone: {phone}, {device_info_str})"
        )
        trigger_desktop_sync_safe()
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
        profile.device_limit = max(2, device_limit) if (role == "admin" and user.username in ("Mathan003", "admin")) else (2 if role == "admin" else device_limit)
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
        trigger_desktop_sync_safe()
        messages.success(request, f"User account '{user.username}' updated successfully!")

    return redirect("billing:admin_panel")


@admin_required
def admin_client_delete(request, user_id):
    """
    Admin deletes a client account.
    Normal client users cannot delete clients.
    All active device sessions for this client are terminated immediately.
    """
    target_user = User.objects.filter(pk=user_id).first()
    if not target_user:
        messages.info(request, "This client account has already been deleted or does not exist.")
        return redirect("billing:admin_panel")

    if request.method == "POST":
        if target_user.id == request.user.id:
            messages.error(request, "Security violation: You cannot delete your currently logged-in administrator account.")
            return redirect("billing:admin_panel")
        if target_user.is_superuser:
            messages.error(request, "Security violation: Superuser accounts cannot be deleted.")
            return redirect("billing:admin_panel")

        uname = target_user.username
        # 1. Permanently register in DeletedClient blacklist to block sync/auto-seed revival
        DeletedClient.objects.get_or_create(username=uname)

        # 2. Invalidate all active device sessions for this user so they are immediately kicked out
        ActiveUserSession.objects.filter(user=target_user).delete()

        # 3. Deactivate all registered devices
        RegisteredDevice.objects.filter(user=target_user).update(is_active=False, is_verified=False)

        # 4. Deactivate user login and mark profile deleted (preserving business data safe & isolated)
        target_user.is_active = False
        target_user.save(update_fields=["is_active"])
        if hasattr(target_user, "profile"):
            target_user.profile.is_deleted = True
            target_user.profile.deleted_at = timezone.now()
            target_user.profile.save(update_fields=["is_deleted", "deleted_at", "updated_at"])

        log_activity(
            request,
            "USER_DELETE",
            f"Admin '{request.user.username}' deleted client account '{uname}' and terminated all associated device sessions."
        )
        trigger_desktop_sync_safe()
        messages.success(request, f"Client '{uname}' and all active device sessions deleted successfully.")

    return redirect("billing:admin_panel")


@admin_required
def admin_revoke_device_session(request, session_id):
    """
    Admin forcefully disconnects an active device session from the Admin Panel.
    Terminates the live session immediately so the client terminal is logged out.
    """
    if request.method == "POST":
        dev_session = ActiveUserSession.objects.filter(pk=session_id).first()
        if not dev_session:
            messages.warning(request, "Active session already disconnected or expired.")
            return redirect("billing:admin_panel")

        u_name = dev_session.user.username
        d_info = dev_session.device_info
        ip_addr = dev_session.ip_address
        s_key = dev_session.session_key
        target_user = dev_session.user

        # Expire web session
        try:
            from django.contrib.sessions.models import Session
            Session.objects.filter(session_key=s_key).delete()
        except Exception:
            pass

        # Deactivate matching RegisteredDevice so it does not immediately reconnect
        matching_devices = RegisteredDevice.objects.filter(user=target_user)
        for md in matching_devices:
            if (md.device_id[:12] in d_info) or (md.device_id[:12] in s_key) or (md.device_name in d_info):
                md.is_active = False
                md.is_verified = False
                md.save(update_fields=["is_active", "is_verified"])

        dev_session.delete()
        trigger_desktop_sync_safe()

        log_activity(
            request,
            "SESSION_REVOKE",
            f"Admin revoked device session for user '{u_name}' ({d_info[:35]} | IP: {ip_addr})"
        )
        messages.success(request, f"Disconnected active device session for '{u_name}'. That terminal will be logged out immediately.")

    return redirect("billing:admin_panel")


@login_required
def client_logout_device(request, session_id):
    """
    Allows a client to manually terminate/disconnect another active device/system from their account.
    Terminates the live session immediately so the other client terminal or browser is logged out.
    """
    if request.method == "POST":
        target_session = ActiveUserSession.objects.filter(pk=session_id).first()
        if not target_session:
            msg = "Device session already disconnected or expired."
            if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.GET.get("format") == "json":
                return JsonResponse({"status": "not_found", "message": msg})
            messages.warning(request, msg)
            return redirect(request.META.get("HTTP_REFERER", "/"))

        is_admin = request.user.is_superuser or (hasattr(request.user, "profile") and request.user.profile.role == "admin")
        if target_session.user != request.user and not is_admin:
            return HttpResponseForbidden("Permission denied.")

        d_info = target_session.device_info
        s_key = target_session.session_key
        ip_addr = target_session.ip_address
        target_user = target_session.user

        # Expire Django web session
        try:
            from django.contrib.sessions.models import Session
            Session.objects.filter(session_key=s_key).delete()
        except Exception:
            pass

        # Deactivate matching RegisteredDevice so it cannot immediately pull or push
        matching_devices = RegisteredDevice.objects.filter(user=target_user)
        for md in matching_devices:
            if (md.device_id[:12] in d_info) or (md.device_id[:12] in s_key) or (md.device_name in d_info):
                md.is_active = False
                md.save(update_fields=["is_active"])

        target_session.delete()
        trigger_desktop_sync_safe()

        log_activity(
            request,
            "DEVICE_LOGOUT",
            f"User '{request.user.username}' manually disconnected session ({d_info[:35]} | IP: {ip_addr})"
        )

        remaining = ActiveUserSession.objects.filter(user=request.user).count()
        clean_name = d_info.split("(")[0].strip() or "Device"
        msg = f"Device '{clean_name}' has been disconnected and logged out successfully. Slot freed!"

        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.GET.get("format") == "json":
            return JsonResponse({
                "status": "success",
                "message": msg,
                "remaining_active": remaining,
            })

        messages.success(request, msg)

    return redirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def client_logout_all_other_devices(request):
    """
    Disconnects all other active devices/systems for this user except the current one.
    """
    if request.method == "POST":
        sess_obj = getattr(request, "session", None)
        curr_key = str(getattr(sess_obj, "session_key", "") or (sess_obj.get("session_key", "") if hasattr(sess_obj, "get") else ""))[:40]
        curr_device_id = request.COOKIES.get("billing_device_id", "")
        
        all_user_sessions = ActiveUserSession.objects.filter(user=request.user)
        other_sessions = []
        for s in all_user_sessions:
            if curr_key and s.session_key == curr_key:
                continue
            if curr_device_id and (curr_device_id[:12] in s.device_info or curr_device_id[:12] in s.session_key):
                continue
            other_sessions.append(s)

        count = len(other_sessions)
        try:
            from django.contrib.sessions.models import Session
            for s in other_sessions:
                Session.objects.filter(session_key=s.session_key).delete()
                for md in RegisteredDevice.objects.filter(user=request.user):
                    if (md.device_id[:12] in s.device_info) or (md.device_id[:12] in s.session_key):
                        md.is_active = False
                        md.save(update_fields=["is_active"])
                s.delete()
        except Exception:
            pass

        trigger_desktop_sync_safe()

        log_activity(
            request,
            "DEVICE_LOGOUT_ALL",
            f"User '{request.user.username}' logged out {count} other system(s)."
        )

        msg = f"Successfully logged out {count} other active device(s)!" if count > 0 else "No other active devices found."
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.GET.get("format") == "json":
            return JsonResponse({"status": "success", "message": msg, "logged_out_count": count})

        messages.success(request, msg)

    return redirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def api_client_devices(request):
    """
    Returns active devices for the current logged-in client in JSON format.
    """
    sess_obj = getattr(request, "session", None)
    curr_session_key = str(getattr(sess_obj, "session_key", "") or (sess_obj.get("session_key", "") if hasattr(sess_obj, "get") else ""))[:40]
    curr_device_id = request.COOKIES.get("billing_device_id", "")
    devices = []
    for s in ActiveUserSession.objects.filter(user=request.user).order_by("-last_activity"):
        is_curr = False
        if curr_session_key and s.session_key == curr_session_key:
            is_curr = True
        elif curr_device_id and (curr_device_id[:12] in s.device_info or curr_device_id[:12] in s.session_key):
            is_curr = True

        devices.append({
            "id": s.id,
            "device_info": s.device_info,
            "ip_address": s.ip_address,
            "last_activity": s.last_activity.strftime("%d-%m-%Y %H:%M"),
            "is_current": is_curr,
            "is_desktop": "EXE" in s.device_info or "Desktop" in s.device_info or s.session_key.startswith("EXE-"),
        })

    profile = getattr(request.user, "profile", None)
    max_devices = profile.device_limit if (profile and profile.device_limit) else 5
    return JsonResponse({
        "status": "success",
        "devices": devices,
        "active_count": len(devices),
        "max_allowed": max_devices,
    })


@admin_required
def admin_revoke_registered_device(request, device_id):
    """
    Admin revokes an approved device slot to allow a new or replacement device.
    Frees up the device quota slot immediately and disconnects the client device.
    """
    if request.method == "POST":
        device = RegisteredDevice.objects.filter(pk=device_id).first()
        if not device:
            hw_id = request.POST.get("device_hw_id", "").strip()
            if hw_id:
                device = RegisteredDevice.objects.filter(device_id=hw_id).first()

        if not device:
            messages.warning(request, "Device slot already removed or not found.")
            return redirect("billing:admin_panel")

        u_name = device.user.username
        d_name = device.device_name
        d_id = device.device_id
        target_user = device.user

        # Mark device inactive & unverified so it cannot sync or reconnect
        device.is_active = False
        device.is_verified = False
        device.save(update_fields=["is_active", "is_verified"])

        # Disconnect active session only for this specific revoked device
        ActiveUserSession.objects.filter(session_key=f"EXE-{d_id}"[:40]).delete()
        ActiveUserSession.objects.filter(user=target_user, device_info__contains=d_id[:12]).delete()

        # Expire any web sessions matching this user and device
        try:
            from django.contrib.sessions.models import Session
            for s in ActiveUserSession.objects.filter(user=target_user):
                if (d_id[:12] in s.device_info) or (s.device_info == d_name):
                    Session.objects.filter(session_key=s.session_key).delete()
                    s.delete()
        except Exception:
            pass

        trigger_desktop_sync_safe()

        # Delete the device entry to free the slot
        device.delete()

        log_activity(
            request,
            "DEVICE_REVOKE",
            f"Admin revoked approved device '{d_name}' ({d_id[:12]}) for client '{u_name}'."
        )
        messages.success(request, f"Approved device '{d_name}' removed successfully. Slot is now free and client device has been logged out.")

    return redirect("billing:admin_panel")


@admin_required
def admin_approve_device_otp(request, device_id):
    """
    Admin one-click approval / acceptance of a pending client EXE device from the Admin Panel.
    Marks the device as verified, clears the OTP, activates it, and logs the action.
    """
    if request.method == "POST":
        device = RegisteredDevice.objects.filter(pk=device_id).first()
        if not device:
            messages.warning(request, "Device not found.")
            return redirect("billing:admin_panel")

        device.is_verified = True
        device.is_active = True
        otp_used = device.otp_code
        device.otp_code = ""
        device.save(update_fields=["is_verified", "is_active", "otp_code"])

        # Create or update active session in cloud DB so Admin can monitor and disconnect
        ActiveUserSession.objects.update_or_create(
            session_key=f"EXE-{device.device_id}"[:40],
            defaults={
                "user": device.user,
                "device_info": f"Desktop POS (EXE): {device.device_name} ({device.device_id[:12]})",
                "ip_address": device.ip_address,
                "last_activity": timezone.now(),
            }
        )

        log_activity(
            request,
            "DEVICE_VERIFY",
            f"Admin accepted and approved new device '{device.device_name}' ({device.device_id[:12]}) for client '{device.user.username}' (OTP: {otp_used})."
        )
        trigger_desktop_sync_safe()
        messages.success(request, f"Device '{device.device_name}' for client '{device.user.username}' has been accepted and activated!")

    return redirect("billing:admin_panel")


@admin_required
def admin_reject_device_otp(request, device_id):
    """
    Admin rejects and cancels a pending client EXE device activation request from the Admin Panel.
    Removes the device registration request and logs the rejection.
    """
    if request.method == "POST":
        device = RegisteredDevice.objects.filter(pk=device_id).first()
        if not device:
            messages.warning(request, "Device not found.")
            return redirect("billing:admin_panel")

        u_name = device.user.username
        d_name = device.device_name
        d_id = device.device_id
        otp_val = device.otp_code

        # Remove any lingering active session
        ActiveUserSession.objects.filter(session_key=f"EXE-{d_id}"[:40]).delete()
        device.delete()

        log_activity(
            request,
            "DEVICE_REJECT",
            f"Admin rejected device activation request for '{d_name}' ({d_id[:12]}) of client '{u_name}' (Cancelled OTP: {otp_val})."
        )
        trigger_desktop_sync_safe()
        messages.warning(request, f"Device activation request for '{d_name}' (Client: {u_name}) has been rejected.")

    return redirect("billing:admin_panel")


@admin_required
def admin_update_device_limit(request, user_id):
    """
    Admin quickly increases, decreases, or sets the allowed device quota for a client.
    Instantly updates UserProfile.device_limit and synchronizes to client desktop terminals.
    """
    user = User.objects.filter(pk=user_id).first()
    if not user:
        u_name = request.POST.get("username", "").strip()
        if u_name:
            user = User.objects.filter(username=u_name).first()

    if not user:
        messages.warning(request, "User account not found.")
        return redirect("billing:admin_panel")

    profile, _ = UserProfile.objects.get_or_create(user=user)

    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        custom_limit = request.POST.get("device_limit", "").strip()
        current_limit = profile.device_limit or 5

        if action == "increase":
            new_limit = min(50, current_limit + 1)
        elif action == "decrease":
            new_limit = max(1, current_limit - 1)
        elif custom_limit:
            try:
                new_limit = max(1, min(50, int(custom_limit)))
            except Exception:
                new_limit = current_limit
        else:
            new_limit = current_limit

        profile.device_limit = new_limit
        profile.save(update_fields=["device_limit"])

        log_activity(
            request,
            "DEVICE_LIMIT_UPDATE",
            f"Admin '{request.user.username}' updated device limit for '{user.username}' from {current_limit} to {new_limit} devices."
        )
        try:
            from sync_manager import get_sync_manager
            sm = get_sync_manager()
            if sm:
                sm.max_allowed_devices = new_limit
                sm.trigger_sync()
        except Exception:
            pass
        trigger_desktop_sync_safe()

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "status": "success",
                "device_limit": new_limit,
                "message": f"Device limit updated to {new_limit} devices for '{user.username}'."
            })

        messages.success(request, f"Device limit for '{user.username}' updated to {new_limit} devices!")

        messages.success(request, f"Device limit for client '{user.username}' updated to {new_limit} devices!")

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
        trigger_desktop_sync_safe()
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
            now = timezone.now()
            count = 0
            for inv in qs.filter(is_deleted=False).prefetch_related("items"):
                for item in inv.items.all():
                    if item.product:
                        Product.objects.filter(pk=item.product.id).update(
                            stock_quantity=F("stock_quantity") + item.quantity
                        )
                inv.is_deleted = True
                inv.deleted_at = now
                inv.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
                count += 1
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

            s_dt, e_dt = get_date_bounds(target_date)
            invoices = Invoice.objects.filter(c_filter, created_at__gte=s_dt, created_at__lte=e_dt)
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

            s_dt, e_dt = get_date_bounds(start_date, end_date)
            invoices = Invoice.objects.filter(
                c_filter,
                created_at__gte=s_dt,
                created_at__lte=e_dt
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
                customer.is_deleted = True
                customer.deleted_at = timezone.now()
                customer.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
                log_activity(
                    request,
                    "RECYCLE_BIN_DELETE",
                    f"User '{user_name}' moved customer '{cust_name}' and all {inv_count} associated invoice(s) to Recycle Bin (3-day recovery)."
                )
                messages.success(request, f"Customer '{cust_name}' and {inv_count} invoice(s) moved to Recycle Bin (3-day recovery).")
            else:
                log_activity(
                    request,
                    "RECYCLE_BIN_DELETE",
                    f"User '{user_name}' moved {inv_count} invoice(s) for customer '{cust_name}' to Recycle Bin. Pending balance cleared."
                )
                messages.success(request, f"Successfully moved {inv_count} bill(s) for '{cust_name}' to Recycle Bin. Customer balance reset to ₹0.00.")

        elif delete_type == "all_invoices":
            invoices = Invoice.objects.filter(c_filter, is_deleted=False)
            inv_count = _restore_and_delete_invoices(invoices)
            log_activity(
                request,
                "RECYCLE_BIN_DELETE",
                f"User '{user_name}' moved all {inv_count} store invoices to Recycle Bin (3-day recovery). Stock restored."
            )
            messages.success(request, f"Successfully moved {inv_count} store invoices to Recycle Bin (3-day recovery). Dashboard and sales reset to ₹0.00.")

        elif delete_type == "wipe_all":
            invoices = Invoice.objects.filter(c_filter, is_deleted=False)
            inv_count = _restore_and_delete_invoices(invoices)
            customers = Customer.objects.filter(c_filter, is_deleted=False)
            cust_count = customers.count()
            customers.update(is_deleted=True, deleted_at=timezone.now())
            log_activity(
                request,
                "RECYCLE_BIN_DELETE",
                f"User '{user_name}' moved {inv_count} invoices and {cust_count} customers to Recycle Bin (3-day recovery)."
            )
            messages.success(request, f"Moved {inv_count} invoices and {cust_count} customers to Recycle Bin (Recoverable for 3 days).")

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

    start_dt, end_dt = get_date_bounds(start_date, end_date)

    # 1. Opening Balance prior to start_date
    prior_invoices = Invoice.objects.filter(cust_query, is_deleted=False, created_at__lt=start_dt)
    prior_billed = prior_invoices.aggregate(Sum("grand_total"))["grand_total__sum"] or Decimal("0.00")
    prior_paid = prior_invoices.aggregate(Sum("paid_amount"))["paid_amount__sum"] or Decimal("0.00")
    opening_balance = max(Decimal("0.00"), prior_billed - prior_paid)

    # 2. Invoices in date range
    range_invoices = Invoice.objects.filter(
        cust_query,
        is_deleted=False,
        created_at__gte=start_dt,
        created_at__lte=end_dt
    ).prefetch_related("items").order_by("created_at")

    # 3. Payments in date range
    range_payments = PaymentRecord.objects.filter(
        invoice__in=Invoice.objects.filter(cust_query, is_deleted=False),
        created_at__gte=start_dt,
        created_at__lte=end_dt
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
    customer = get_object_or_404(Customer.objects.filter(c_filter, is_deleted=False), pk=customer_id)
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
    customer = get_object_or_404(Customer.objects.filter(c_filter, is_deleted=False), pk=customer_id)
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

    products_qs = Product.objects.filter(c_filter, is_active=True, is_deleted=False).order_by("name_tamil", "name")

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

    all_active = Product.objects.filter(c_filter, is_active=True, is_deleted=False)
    total_sku_count = all_active.count()
    low_stock_count = all_active.filter(stock_quantity__lte=5, stock_quantity__gt=0).count()
    out_of_stock_count = all_active.filter(stock_quantity__lte=0).count()

    total_inventory_value = all_active.aggregate(
        val=Sum(F("cost_price") * F("stock_quantity"))
    )["val"] or Decimal("0.00")

    stock_logs = StockLog.objects.filter(product__in=all_active).select_related("product", "invoice").order_by("-created_at")[:100]
    categories = Product.objects.filter(c_filter, is_active=True, is_deleted=False).values_list("category", flat=True).distinct()

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

            product = get_object_or_404(Product.objects.filter(c_filter, is_deleted=False), pk=product_id)
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
    product = get_object_or_404(Product.objects.filter(c_filter, is_deleted=False), pk=product_id)

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
    products = Product.objects.filter(c_filter, is_active=True, is_deleted=False).order_by("name_tamil", "name")

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

    customers_qs = Customer.objects.filter(c_filter, is_deleted=False).annotate(
        calc_billed=Sum("invoices__grand_total", filter=Q(invoices__is_deleted=False)),
        calc_paid=Sum("invoices__paid_amount", filter=Q(invoices__is_deleted=False)),
        calc_pending=Sum("invoices__balance_amount", filter=Q(invoices__is_deleted=False)),
        invoices_count=Count("invoices", filter=Q(invoices__is_deleted=False))
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

    all_custs = Customer.objects.filter(c_filter, is_deleted=False).annotate(
        calc_pending=Sum("invoices__balance_amount", filter=Q(invoices__is_deleted=False))
    )
    total_customers_count = all_custs.count()
    pending_custs_count = all_custs.filter(calc_pending__gt=0).count()
    total_pending_all = Invoice.objects.filter(c_filter, is_deleted=False).aggregate(Sum("balance_amount"))["balance_amount__sum"] or Decimal("0.00")

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
    Moves customer to Recycle Bin / Deleted Items (3-day recovery window).
    Accessible to authenticated shop clients and administrators.
    """
    if not request.user.is_authenticated:
        return redirect("billing:login")
    if request.method == "POST":
        c_filter = get_client_filter(request)
        customer = get_object_or_404(Customer.objects.filter(c_filter, is_deleted=False), pk=customer_id)
        cust_name = customer.name
        cust_phone = customer.phone or "N/A"

        # Soft delete: move to Recycle Bin without wiping invoice relationships
        customer.is_deleted = True
        customer.deleted_at = timezone.now()
        customer.save(update_fields=["is_deleted", "deleted_at", "updated_at"])

        log_activity(
            request,
            "RECYCLE_BIN_DELETE",
            f"User '{request.user.username}' moved customer '{cust_name}' (Phone: {cust_phone}) to Recycle Bin (3-day recovery)."
        )
        trigger_desktop_sync_safe()
        messages.success(request, f"Customer record '{cust_name}' moved to Recycle Bin (Deleted Items). Recoverable for 3 days.")

    return redirect("billing:customer_list")


def customer_export_csv(request, customer_id):
    """Exports a single customer's transaction ledger / statement as CSV"""
    c_filter = get_client_filter(request)
    customer = get_object_or_404(Customer.objects.filter(c_filter, is_deleted=False), pk=customer_id)
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
    customers_qs = Customer.objects.filter(c_filter, is_deleted=False).annotate(
        calc_billed=Sum("invoices__grand_total", filter=Q(invoices__is_deleted=False)),
        calc_paid=Sum("invoices__paid_amount", filter=Q(invoices__is_deleted=False)),
        calc_pending=Sum("invoices__balance_amount", filter=Q(invoices__is_deleted=False)),
        invoices_count=Count("invoices", filter=Q(invoices__is_deleted=False))
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
    period_start_dt, period_end_dt = get_date_bounds(start_date, end_date)
    bills_qs = Invoice.objects.filter(
        c_filter,
        is_deleted=False,
        created_at__gte=period_start_dt,
        created_at__lte=period_end_dt
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
    t_start, t_end = get_date_bounds(today)
    today_bills = Invoice.objects.filter(c_filter, is_deleted=False, created_at__gte=t_start, created_at__lte=t_end)
    today_footfall = _compute_customer_footfall(today_bills)

    # 2. This Week
    week_start = today - timedelta(days=today.weekday())
    w_start, w_end = get_date_bounds(week_start, today)
    week_bills = Invoice.objects.filter(c_filter, is_deleted=False, created_at__gte=w_start, created_at__lte=w_end)
    week_footfall = _compute_customer_footfall(week_bills)

    # 3. This Month
    month_start = today.replace(day=1)
    m_start, m_end = get_date_bounds(month_start, today)
    month_bills = Invoice.objects.filter(c_filter, is_deleted=False, created_at__gte=m_start, created_at__lte=m_end)
    month_footfall = _compute_customer_footfall(month_bills)

    # 4. This Year
    year_start = today.replace(month=1, day=1)
    y_start, y_end = get_date_bounds(year_start, today)
    year_bills = Invoice.objects.filter(c_filter, is_deleted=False, created_at__gte=y_start, created_at__lte=y_end)
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
        gst_number = request.POST.get("gst_number", "").strip().upper()

        profile.shop_name = shop_name
        profile.shop_address = shop_address
        profile.phone = phone
        profile.bank_name = bank_name
        profile.account_number = account_number
        profile.ifsc_code = ifsc_code
        profile.gst_number = gst_number

        if request.user.is_superuser or profile.role == "admin":
            comp = CompanySettings.get_settings()
            if gst_number:
                comp.gst_number = gst_number
                comp.save(update_fields=["gst_number", "updated_at"])

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

        # Bill Paper Size & Print Configuration
        print_paper_size = request.POST.get("print_paper_size", "").strip()
        if print_paper_size:
            profile.print_paper_size = print_paper_size
            if print_paper_size == "custom":
                profile.print_custom_width_mm = parse_decimal(request.POST.get("print_custom_width_mm", "80.0"), "80.0")
            else:
                preset_widths = {
                    "58mm": Decimal("58.0"),
                    "80mm": Decimal("80.0"),
                    "100mm": Decimal("100.0"),
                    "a5": Decimal("148.0"),
                    "a4": Decimal("210.0"),
                }
                profile.print_custom_width_mm = preset_widths.get(print_paper_size, Decimal("80.0"))

        print_paper_height_mode = request.POST.get("print_paper_height_mode", "").strip()
        if print_paper_height_mode in ["auto", "fixed"]:
            profile.print_paper_height_mode = print_paper_height_mode
            if print_paper_height_mode == "fixed":
                h_str = request.POST.get("print_custom_height_mm", "").strip()
                profile.print_custom_height_mm = parse_decimal(h_str, "210.0") if h_str else Decimal("210.0")
            else:
                profile.print_custom_height_mm = None

        profile.print_auto_expand_height = (request.POST.get("print_auto_expand_height") in ["on", "true", "1"])
        print_font_scaling = request.POST.get("print_font_scaling", "").strip()
        if print_font_scaling:
            profile.print_font_scaling = print_font_scaling

        profile.save()

        log_activity(
            request,
            "PRINT_CONFIG_UPDATE",
            f"User '{request.user.username}' updated business profile and print settings (Paper: {profile.print_paper_size}, {profile.paper_dimensions_display})"
        )
        trigger_desktop_sync_safe()
        messages.success(request, f"Shop Profile & Bill Paper Size ({profile.print_paper_size.upper()} - {profile.paper_dimensions_display}) updated successfully!")
        return redirect("billing:client_profile")

    # Fetch branches associated with this client or default branches
    if request.user.is_superuser or profile.role == "admin":
        branches = Branch.objects.all().order_by("-is_default", "name")
    else:
        branches = Branch.objects.filter(Q(client=request.user) | Q(is_default=True)).order_by("-is_default", "name")

    c_filter = get_client_filter(request)
    recycle_bin_count = (
        Invoice.objects.filter(c_filter, is_deleted=True).count() +
        Customer.objects.filter(c_filter, is_deleted=True).count() +
        Product.objects.filter(c_filter, is_deleted=True).count() +
        SavedReport.objects.filter(c_filter, is_deleted=True).count()
    )

    return render(request, "billing/client_profile.html", {
        "profile": profile,
        "branches": branches,
        "recycle_bin_count": recycle_bin_count,
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
    - If running in desktop app and pending update is ready, schedules seamless detached restart.
    """
    latest = SoftwareUpdate.get_latest_update()
    version = latest.version if latest else "v2.5.0"
    request.session["applied_update_version"] = version

    is_desktop = os.getenv("IS_DESKTOP_APP") == "True" or is_desktop_environment(request)
    restarting = False
    if is_desktop:
        try:
            try:
                from auto_updater import get_auto_updater
            except ImportError:
                from windows_app.auto_updater import get_auto_updater
            updater = get_auto_updater()
            if updater:
                if updater.is_update_ready():
                    restarting = True
                    updater.schedule_restart()
                else:
                    updater.trigger_check()
        except Exception:
            pass

    msg = (
        f"Software updated to {version}! Desktop app is restarting smoothly to finalize upgrade."
        if restarting else
        f"Software updated to {version} successfully."
    )

    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.GET.get("format") == "json":
        return JsonResponse({
            "status": "success",
            "message": msg,
            "version": version,
            "is_desktop": is_desktop,
            "restarting": restarting,
        })

    messages.success(request, msg)
    return redirect(request.META.get("HTTP_REFERER", "/"))


def client_reject_software_update(request):
    """
    Client rejects / postpones software update:
    - Sets rejected_update_version in session and cookie so update banner is dismissed.
    - Does NOT force update or interrupt active billing session.
    - Returns JSON for AJAX calls or redirects back.
    """
    latest = SoftwareUpdate.get_latest_update()
    version = latest.version if latest else "v2.5.0"
    request.session["rejected_update_version"] = version

    msg = f"Update {version} postponed. You can update later from Settings."

    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.GET.get("format") == "json":
        response = JsonResponse({
            "status": "rejected",
            "message": msg,
            "version": version,
        })
        response.set_cookie(f"rejected_update_{version}", "1", max_age=86400 * 7)
        return response

    messages.info(request, msg)
    response = redirect(request.META.get("HTTP_REFERER", "/"))
    response.set_cookie(f"rejected_update_{version}", "1", max_age=86400 * 7)
    return response


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
        now = timezone.now()
        invoices = Invoice.objects.filter(customer=customer, is_deleted=False)
        inv_count = invoices.count()

        # Restore stock for invoice items
        for inv in invoices.prefetch_related("items"):
            for item in inv.items.all():
                if item.product:
                    Product.objects.filter(pk=item.product.id).update(
                        stock_quantity=F("stock_quantity") + item.quantity
                    )
            inv.is_deleted = True
            inv.deleted_at = now
            inv.save(update_fields=["is_deleted", "deleted_at", "updated_at"])

        if action_type == "full":
            customer.is_deleted = True
            customer.deleted_at = now
            customer.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
            log_activity(
                request,
                "RECYCLE_BIN_DELETE",
                f"Admin '{request.user.username}' moved customer '{cust_name}' and {inv_count} bills to Recycle Bin (3-day recovery)."
            )
            messages.success(request, f"Customer '{cust_name}' and all {inv_count} bills moved to Recycle Bin (3-day recovery).")
        else:
            log_activity(
                request,
                "RECYCLE_BIN_DELETE",
                f"Admin '{request.user.username}' moved all {inv_count} bills for customer '{cust_name}' to Recycle Bin (3-day recovery)."
            )
            messages.success(request, f"All {inv_count} bills for customer '{cust_name}' moved to Recycle Bin (Profile retained, recoverable for 3 days).")

        trigger_desktop_sync_safe()
        return redirect("billing:admin_panel")


def sync_status_view(request):
    """Returns real-time sync connectivity status, pending offline bills count, error diagnostics, and dynamic device counts"""
    try:
        from sync_manager import get_sync_manager
        sm = get_sync_manager()
        data = sm.get_status_dict()
    except Exception:
        data = {
            "is_online": True,
            "is_syncing": False,
            "server_url": "https://billing-software-production-d0f2.up.railway.app",
            "last_sync_time": timezone.now().strftime("%H:%M:%S"),
            "last_sync_status": "cloud",
            "last_error": None,
            "last_error_time": None,
            "pending_count": 0,
            "pending_invoices": [],
            "is_desktop": False,
        }

    # If user is authenticated, provide live multi-terminal count & device limit
    if request.user.is_authenticated:
        profile = getattr(request.user, "profile", None)
        is_admin = request.user.is_superuser or (profile and profile.role == "admin")
        data["is_admin"] = is_admin
        if is_admin:
            admin_limit = profile.device_limit if (profile and profile.device_limit) else (2 if request.user.username in ("Mathan003", "admin") else 1)
            if request.user.username == "Mathan003":
                admin_limit = max(2, admin_limit)
            sess_cnt = ActiveUserSession.objects.filter(user=request.user).count()
            data["active_devices_count"] = min(admin_limit, max(sess_cnt, 1))
            data["max_allowed_devices"] = admin_limit
        else:
            local_sess = ActiveUserSession.objects.filter(user=request.user).count()
            local_reg = RegisteredDevice.objects.filter(user=request.user, is_active=True, is_verified=True).count()
            sm_active = data.get("active_devices_count")
            sm_limit = data.get("max_allowed_devices")

            profile_lim = profile.device_limit if (profile and profile.device_limit) else 5
            effective_limit = sm_limit if (sm_limit and sm_limit > 0) else profile_lim
            if profile and sm_limit and profile.device_limit != sm_limit:
                profile.device_limit = sm_limit
                profile.save(update_fields=["device_limit"])

            effective_active = max(local_sess, local_reg, sm_active or 0, 1)
            effective_active = min(effective_active, effective_limit)

            data["active_devices_count"] = effective_active
            data["max_allowed_devices"] = effective_limit

    return JsonResponse(data)


def sync_trigger_view(request):
    """Triggers immediate database sync cycle and returns real-time status and diagnostics"""
    if request.method == "POST":
        try:
            from sync_manager import get_sync_manager
            sm = get_sync_manager()
            sm._sync_cycle()
            res_data = sm.get_status_dict()
            is_ok = res_data.get("last_sync_status") == "success"
            msg = "Database synchronized successfully with cloud server." if is_ok else (res_data.get("last_error") or "Sync finished with notices.")
            return JsonResponse({
                "status": "success" if is_ok else "error",
                "message": msg,
                "sync_data": res_data,
            })
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=500)
    return JsonResponse({"status": "error", "message": "POST method required."}, status=405)


def sync_server_config_view(request):
    """Allows setting or retrieving the target cloud sync server URL"""
    from .device_utils import get_desktop_pos_config, save_desktop_pos_config
    if request.method == "POST":
        new_url = request.POST.get("server_url", "").strip().rstrip("/")
        if new_url:
            save_desktop_pos_config({"server_url": new_url})
            try:
                from sync_manager import get_sync_manager
                sm = get_sync_manager()
                sm.server_url = new_url
                sm.trigger_sync()
            except Exception:
                pass
            messages.success(request, f"Sync Server URL updated to: {new_url}")
        else:
            messages.error(request, "Please enter a valid server URL.")
        return redirect("billing:sync_status")

    cfg = get_desktop_pos_config()
    return JsonResponse({"server_url": cfg.get("server_url", "")})


# ======================================================
# DELETED ITEMS / RECYCLE BIN (3-DAY RECOVERY LIFECYCLE)
# ======================================================

def _format_time_left(deleted_at):
    if not deleted_at:
        return "Expired"
    expires_at = deleted_at + timedelta(days=3)
    remaining = expires_at - timezone.now()
    total_seconds = int(remaining.total_seconds())
    if total_seconds <= 0:
        return "Expired (Pending cleanup)"
    hours = total_seconds // 3600
    days = hours // 24
    rem_hours = hours % 24
    if days > 0:
        return f"{days}d {rem_hours}h remaining"
    mins = (total_seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {mins}m remaining"
    return f"{mins}m remaining"


def recycle_bin_view(request):
    """
    Deleted Items / Recycle Bin:
    Displays customer data, bills, products, and reports soft-deleted within the last 3 days.
    Purges any expired records (>3 days old) automatically on load.
    Multi-tenant isolation: clients see only their own deleted items.
    """
    if not request.user.is_authenticated:
        return redirect("billing:login")

    # 1. Trigger automated 3-day cleanup routine
    purge_expired_deleted_items()

    c_filter = get_client_filter(request)
    active_tab = request.GET.get("tab", "all").strip().lower()
    if active_tab not in ["all", "invoices", "customers", "products", "reports"]:
        active_tab = "all"

    # Query soft-deleted items
    deleted_invoices = list(Invoice.objects.filter(c_filter, is_deleted=True).prefetch_related("items").order_by("-deleted_at"))
    for inv in deleted_invoices:
        inv.time_left_str = _format_time_left(inv.deleted_at)

    deleted_customers = list(Customer.objects.filter(c_filter, is_deleted=True).order_by("-deleted_at"))
    for cust in deleted_customers:
        cust.time_left_str = _format_time_left(cust.deleted_at)

    deleted_products = list(Product.objects.filter(c_filter, is_deleted=True).order_by("-deleted_at"))
    for prod in deleted_products:
        prod.time_left_str = _format_time_left(prod.deleted_at)

    deleted_reports = list(SavedReport.objects.filter(c_filter, is_deleted=True).order_by("-deleted_at"))
    for rep in deleted_reports:
        rep.time_left_str = _format_time_left(rep.deleted_at)

    invoices_count = len(deleted_invoices)
    customers_count = len(deleted_customers)
    products_count = len(deleted_products)
    reports_count = len(deleted_reports)
    total_deleted = invoices_count + customers_count + products_count + reports_count

    return render(request, "billing/recycle_bin.html", {
        "active_tab": active_tab,
        "deleted_invoices": deleted_invoices,
        "deleted_customers": deleted_customers,
        "deleted_products": deleted_products,
        "deleted_reports": deleted_reports,
        "invoices_count": invoices_count,
        "customers_count": customers_count,
        "products_count": products_count,
        "reports_count": reports_count,
        "total_deleted": total_deleted,
    })


def recycle_bin_restore(request, item_type, item_id):
    """
    Restores a soft-deleted item back to active records.
    """
    if not request.user.is_authenticated:
        return redirect("billing:login")

    item_type = str(item_type).lower().strip()
    if request.method == "POST":
        c_filter = get_client_filter(request)

        if item_type == "invoice":
            inv = get_object_or_404(Invoice.objects.filter(c_filter, is_deleted=True).prefetch_related("items"), pk=item_id)
            # Re-deduct catalog stock upon restoration
            for item in inv.items.all():
                if item.product:
                    Product.objects.filter(pk=item.product.id).update(
                        stock_quantity=F("stock_quantity") - item.quantity
                    )
            inv.is_deleted = False
            inv.deleted_at = None
            inv.save(update_fields=["is_deleted", "deleted_at"])

            log_activity(
                request,
                "RECYCLE_BIN_RESTORE",
                f"User '{request.user.username}' restored Invoice #{inv.invoice_number} from Recycle Bin."
            )
            trigger_desktop_sync_safe()
            messages.success(request, f"Invoice #{inv.invoice_number} was restored successfully to active bills.")

        elif item_type == "customer":
            cust = get_object_or_404(Customer.objects.filter(c_filter, is_deleted=True), pk=item_id)
            cust.is_deleted = False
            cust.deleted_at = None
            cust.save(update_fields=["is_deleted", "deleted_at", "updated_at"])

            log_activity(
                request,
                "RECYCLE_BIN_RESTORE",
                f"User '{request.user.username}' restored Customer '{cust.name}' from Recycle Bin."
            )
            trigger_desktop_sync_safe()
            messages.success(request, f"Customer '{cust.name}' was restored successfully to active customers.")

        elif item_type == "product":
            prod = get_object_or_404(Product.objects.filter(c_filter, is_deleted=True), pk=item_id)
            prod.is_deleted = False
            prod.is_active = True
            prod.deleted_at = None
            prod.save(update_fields=["is_deleted", "is_active", "deleted_at", "updated_at"])

            from billing.models import DeletedProduct
            del_prod_q = Q(sku=prod.sku)
            if prod.client:
                del_prod_q &= (Q(client=prod.client) | Q(client__isnull=True))
            DeletedProduct.objects.filter(del_prod_q).delete()

            log_activity(
                request,
                "RECYCLE_BIN_RESTORE",
                f"User '{request.user.username}' restored Product '{prod.display_name}' from Recycle Bin."
            )
            trigger_desktop_sync_safe()
            messages.success(request, f"Product '{prod.display_name}' was restored successfully to inventory.")

        elif item_type == "report":
            rep = get_object_or_404(SavedReport.objects.filter(c_filter, is_deleted=True), pk=item_id)
            rep.is_deleted = False
            rep.deleted_at = None
            rep.save(update_fields=["is_deleted", "deleted_at"])

            log_activity(
                request,
                "RECYCLE_BIN_RESTORE",
                f"User '{request.user.username}' restored Report '{rep.report_name}' from Recycle Bin."
            )
            messages.success(request, f"Report '{rep.report_name}' was restored successfully.")

    tab = "invoices" if item_type == "invoice" else ("customers" if item_type == "customer" else ("products" if item_type == "product" else "reports"))
    return redirect(f"/recycle-bin/?tab={tab}")


def recycle_bin_permanent_delete(request, item_type, item_id):
    """
    Permanently and immediately deletes a single item from the Recycle Bin before the 3-day expiry.
    """
    if not request.user.is_authenticated:
        return redirect("billing:login")

    if request.method == "POST":
        c_filter = get_client_filter(request)
        item_type = str(item_type).lower().strip()

        if item_type == "invoice":
            inv = get_object_or_404(Invoice.objects.filter(c_filter, is_deleted=True), pk=item_id)
            inv_number = inv.invoice_number
            inv.items.all().delete()
            inv.payments.all().delete()
            inv.delete()
            log_activity(
                request,
                "RECYCLE_BIN_PURGE",
                f"User '{request.user.username}' permanently deleted Invoice #{inv_number}."
            )
            messages.success(request, f"Invoice #{inv_number} was permanently deleted.")

        elif item_type == "customer":
            cust = get_object_or_404(Customer.objects.filter(c_filter, is_deleted=True), pk=item_id)
            name = cust.name
            cust.delete()
            log_activity(
                request,
                "RECYCLE_BIN_PURGE",
                f"User '{request.user.username}' permanently deleted Customer record '{name}'."
            )
            messages.success(request, f"Customer record '{name}' was permanently deleted.")

        elif item_type == "product":
            prod = get_object_or_404(Product.objects.filter(c_filter, is_deleted=True), pk=item_id)
            name = prod.display_name
            sku = prod.sku
            client_u = prod.client

            from billing.models import DeletedProduct
            DeletedProduct.objects.update_or_create(
                sku=sku,
                client=client_u,
                defaults={"name": name, "deleted_at": timezone.now()}
            )
            prod.delete()
            log_activity(
                request,
                "RECYCLE_BIN_PURGE",
                f"User '{request.user.username}' permanently deleted Product '{name}'."
            )
            messages.success(request, f"Product '{name}' was permanently deleted.")

        elif item_type == "report":
            rep = get_object_or_404(SavedReport.objects.filter(c_filter, is_deleted=True), pk=item_id)
            name = rep.report_name
            rep.delete()
            log_activity(
                request,
                "RECYCLE_BIN_PURGE",
                f"User '{request.user.username}' permanently deleted Report '{name}'."
            )
            messages.success(request, f"Report '{name}' was permanently deleted.")

        trigger_desktop_sync_safe()

    return redirect("billing:recycle_bin")


def recycle_bin_empty(request):
    """
    Permanently purges ALL soft-deleted items currently in the Recycle Bin for the client.
    """
    if not request.user.is_authenticated:
        return redirect("billing:login")

    if request.method == "POST":
        c_filter = get_client_filter(request)
        invs = Invoice.objects.filter(c_filter, is_deleted=True)
        for inv in invs:
            inv.items.all().delete()
            inv.payments.all().delete()
        invs.delete()

        from billing.models import DeletedProduct
        for p in Product.objects.filter(c_filter, is_deleted=True):
            DeletedProduct.objects.update_or_create(
                sku=p.sku,
                client=p.client,
                defaults={"name": p.display_name, "deleted_at": timezone.now()}
            )

        Customer.objects.filter(c_filter, is_deleted=True).delete()
        Product.objects.filter(c_filter, is_deleted=True).delete()
        SavedReport.objects.filter(c_filter, is_deleted=True).delete()

        log_activity(
            request,
            "RECYCLE_BIN_PURGE",
            f"User '{request.user.username}' emptied the Recycle Bin permanently."
        )
        trigger_desktop_sync_safe()
        messages.success(request, "Recycle Bin was emptied successfully. All deleted records permanently purged.")

    return redirect("billing:recycle_bin")



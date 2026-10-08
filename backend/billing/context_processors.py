import uuid
from .models import ActiveUserSession, SoftwareUpdate, CompanySettings


def session_security_context(request):
    """
    Context processor providing dynamic session security tokens, user session metadata,
    device limits, client shop branding, and software update notifications.
    """
    company = CompanySettings.get_settings()
    latest_update = SoftwareUpdate.get_latest_update()

    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {
            "sec_token": "",
            "sec_param": "",
            "user_profile": None,
            "is_admin_user": False,
            "active_devices_count": 0,
            "max_allowed_devices": 5,
            "available_software_update": None,
            "company": company,
            "active_shop_name": (company.company_name.replace("Supermarket", "").replace("supermarket", "").strip() or "MathanHub") if company else "MathanHub",
            "active_shop_address": company.address,
            "active_shop_phone": company.phone,
            "active_shop_email": company.email,
            "active_shop_logo": company.logo_data_url,
            "active_bank_name": company.bank_name,
            "active_account_number": company.account_number,
            "active_ifsc_code": company.ifsc_code,
            "active_shop_gst": company.gst_number if company else "",
            "business_type": "grocery",
        }

    sec_token = request.session.get("sec_token")
    if not sec_token:
        sec_token = uuid.uuid4().hex[:12]
        request.session["sec_token"] = sec_token

    profile = getattr(request.user, "profile", None)
    is_admin = request.user.is_superuser or (profile and profile.role == "admin")

    # Active devices count and limits
    active_count = ActiveUserSession.objects.filter(user=request.user).count()
    if is_admin:
        max_devices = 1
    else:
        max_devices = profile.device_limit if (profile and profile.device_limit) else 5

    # Check for software updates published by admin
    applied_ver = request.session.get("applied_update_version")
    rejected_ver = request.session.get("rejected_update_version")
    cookie_rejected = False
    if latest_update:
        cookie_rejected = request.COOKIES.get(f"rejected_update_{latest_update.version}") == "1"
    available_software_update = None
    if latest_update and latest_update.version != applied_ver and latest_update.version != rejected_ver and not cookie_rejected:
        available_software_update = latest_update

    # Client-specific shop profile with fallback to company settings
    raw_shop = (profile.shop_name if (profile and profile.shop_name) else (company.company_name if company else "MathanHub"))
    shop_name = (raw_shop or "MathanHub").replace("Supermarket", "").replace("supermarket", "").strip() or "MathanHub"
    shop_address = (profile.shop_address if (profile and profile.shop_address) else company.address)
    shop_phone = (profile.phone if (profile and profile.phone) else company.phone)
    shop_email = (request.user.email if request.user.email else company.email)
    shop_logo = (profile.shop_logo_url if (profile and profile.shop_logo_url) else company.logo_data_url)
    shop_gst = (profile.gst_number.strip() if (profile and profile.gst_number) else (company.gst_number.strip() if (is_admin and company and company.gst_number) else ""))
    bank_name = (profile.bank_name if (profile and profile.bank_name) else company.bank_name)
    account_number = (profile.account_number if (profile and profile.account_number) else company.account_number)
    ifsc_code = (profile.ifsc_code if (profile and profile.ifsc_code) else company.ifsc_code)
    business_type = (profile.business_type if profile else "grocery")

    from django.utils import timezone
    from django.db.models import Q, Count
    from .models import Customer, ProductCategory, Product

    today_date_str = timezone.now().strftime("%Y-%m-%d")
    c_filter = Q(client=request.user) | Q(client__isnull=True) if not is_admin else Q()
    try:
        global_customers = Customer.objects.filter(c_filter).order_by("name")
    except Exception:
        global_customers = []

    global_categories = []
    global_category_list_data = []
    try:
        prod_cats = set(Product.objects.filter(c_filter, is_active=True).exclude(category="").values_list("category", flat=True))
        saved_cats = set(ProductCategory.objects.filter(c_filter).exclude(name="").values_list("name", flat=True))
        all_cats = {"General"} | prod_cats | saved_cats
        global_categories = sorted([c.strip() for c in all_cats if c and c.strip()])
        cat_counts_map = dict(
            Product.objects.filter(c_filter, is_active=True)
            .values("category")
            .annotate(cnt=Count("id"))
            .values_list("category", "cnt")
        )
        global_category_list_data = [
            {
                "name": cat_name,
                "product_count": cat_counts_map.get(cat_name, 0),
                "is_default": (cat_name.lower() == "general"),
            }
            for cat_name in global_categories
        ]
    except Exception:
        pass

    return {
        "sec_token": sec_token,
        "sec_param": f"?sec={sec_token}",
        "user_profile": profile,
        "is_admin_user": is_admin,
        "active_devices_count": active_count,
        "max_allowed_devices": max_devices,
        "available_software_update": available_software_update,
        "company": company,
        "active_shop_name": shop_name,
        "active_shop_address": shop_address,
        "active_shop_phone": shop_phone,
        "active_shop_email": shop_email,
        "active_shop_logo": shop_logo,
        "active_shop_gst": shop_gst,
        "active_bank_name": bank_name,
        "active_account_number": account_number,
        "active_ifsc_code": ifsc_code,
        "business_type": business_type,
        "global_customers": global_customers,
        "global_categories": global_categories,
        "global_category_list_data": global_category_list_data,
        "today_date_str": today_date_str,
    }


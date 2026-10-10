"""
Modular Database Connection and Tenant Scoping Layer for MathanHub POS
Supports Admin, Client, and Customer modules across SQLite, MySQL, and PostgreSQL.
Provides connection health diagnostics, multi-tenant isolation filters, and transaction safety.
"""

import logging
from django.db import connection, connections, transaction
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)


def get_db_engine_name(using="default"):
    """Returns the backend engine name (sqlite, mysql, postgresql, etc.)."""
    try:
        conn = connections[using]
        vendor = conn.vendor
        return vendor
    except Exception as e:
        logger.error(f"Error checking DB vendor: {e}")
        return "unknown"


def is_admin_user(user):
    """Checks if a user is an administrator."""
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or (hasattr(user, "profile") and user.profile.role == "admin")


def get_client_filter(request_or_user):
    """
    Returns a Q filter that guarantees strict multi-tenant isolation.
    - Administrators have global overview access across all records.
    - Client users are strictly constrained to records owned by their account.
    """
    user = getattr(request_or_user, "user", request_or_user)
    if not user or not getattr(user, "is_authenticated", False):
        return Q(pk__in=[])
    if is_admin_user(user):
        return Q()
    return Q(client=user)


def get_admin_db_connection():
    """
    Validates and returns the database connection handle for Admin administrative operations.
    Verifies that system administration models (User, UserProfile, DeviceSession, ActivityLog) are accessible.
    """
    try:
        from django.contrib.auth.models import User
        conn = connections["default"]
        conn.ensure_connection()
        user_count = User.objects.count()
        return {
            "status": "connected",
            "vendor": conn.vendor,
            "database_name": conn.settings_dict.get("NAME", "unknown"),
            "host": conn.settings_dict.get("HOST", "localhost"),
            "user_count": user_count,
            "error": None
        }
    except Exception as e:
        logger.exception("Admin database connection error")
        return {
            "status": "error",
            "vendor": "unknown",
            "database_name": "unknown",
            "host": "unknown",
            "user_count": 0,
            "error": str(e)
        }


def get_client_db_connection(client_user=None):
    """
    Validates and returns client-scoped database connection details.
    Ensures client models (Product, Invoice, StockLog) are operational and isolated.
    """
    try:
        from .models import Product, Invoice
        conn = connections["default"]
        conn.ensure_connection()

        filter_q = Q()
        if client_user and not is_admin_user(client_user):
            filter_q = Q(client=client_user)

        prod_count = Product.objects.filter(filter_q, is_deleted=False).count()
        inv_count = Invoice.objects.filter(filter_q, is_deleted=False).count()

        return {
            "status": "connected",
            "vendor": conn.vendor,
            "products_count": prod_count,
            "invoices_count": inv_count,
            "client_id": client_user.id if client_user else None,
            "error": None
        }
    except Exception as e:
        logger.exception("Client database connection error")
        return {
            "status": "error",
            "vendor": "unknown",
            "products_count": 0,
            "invoices_count": 0,
            "client_id": client_user.id if client_user else None,
            "error": str(e)
        }


def get_customer_db_connection(client_user=None):
    """
    Validates and returns customer module database connection details.
    Verifies Customer and PaymentRecord models are operational and isolated.
    """
    try:
        from .models import Customer, PaymentRecord
        conn = connections["default"]
        conn.ensure_connection()

        filter_q = Q()
        if client_user and not is_admin_user(client_user):
            filter_q = Q(client=client_user)

        cust_count = Customer.objects.filter(filter_q, is_deleted=False).count()
        pay_count = PaymentRecord.objects.filter(filter_q).count()

        return {
            "status": "connected",
            "vendor": conn.vendor,
            "customers_count": cust_count,
            "payments_count": pay_count,
            "client_id": client_user.id if client_user else None,
            "error": None
        }
    except Exception as e:
        logger.exception("Customer database connection error")
        return {
            "status": "error",
            "vendor": "unknown",
            "customers_count": 0,
            "payments_count": 0,
            "client_id": client_user.id if client_user else None,
            "error": str(e)
        }


def check_database_connections():
    """
    Runs diagnostic health checks across Admin, Client, and Customer modules.
    Returns status summary with engine details, connection latency, and table health.
    """
    import time
    t0 = time.time()
    admin_info = get_admin_db_connection()
    client_info = get_client_db_connection()
    customer_info = get_customer_db_connection()
    elapsed_ms = round((time.time() - t0) * 1000, 2)

    all_ok = (
        admin_info["status"] == "connected" and
        client_info["status"] == "connected" and
        customer_info["status"] == "connected"
    )

    return {
        "overall_status": "healthy" if all_ok else "unhealthy",
        "engine": admin_info["vendor"],
        "database": admin_info["database_name"],
        "latency_ms": elapsed_ms,
        "admin_module": admin_info,
        "client_module": client_info,
        "customer_module": customer_info,
        "timestamp": timezone.now().isoformat(),
    }

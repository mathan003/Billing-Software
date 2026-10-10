"""
Modular Client View Layer - MathanHub POS
Handles Client POS Operations, Inventory, Products, and Business Hub.
"""

import logging
from decimal import Decimal
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q, F
from django.utils import timezone
from django.http import JsonResponse

from .db_connections import get_client_filter, is_admin_user, get_client_db_connection
from .models import Product, ProductCategory, Invoice, StockLog, Branch

logger = logging.getLogger(__name__)


@login_required
def client_module_view(request):
    """
    Renders the dedicated Client Module Hub & Operations Overview.
    Displays client-isolated today's sales, bills count, cash collection, and pending dues.
    """
    c_filter = get_client_filter(request)

    # Use timezone-aware bounds for today's queries
    from datetime import datetime as dt_cls, time as dt_time
    today = timezone.localtime().date()
    start_dt = timezone.make_aware(dt_cls.combine(today, dt_time.min))
    end_dt = timezone.make_aware(dt_cls.combine(today, dt_time.max))

    today_invoices = Invoice.objects.filter(
        c_filter,
        is_deleted=False,
        created_at__gte=start_dt,
        created_at__lte=end_dt
    )

    today_sales = today_invoices.aggregate(s=Sum("grand_total"))["s"] or Decimal("0.00")
    today_bills_count = today_invoices.count()
    today_customer_paid = today_invoices.aggregate(s=Sum("paid_amount"))["s"] or Decimal("0.00")

    total_pending_amount = Invoice.objects.filter(
        c_filter,
        is_deleted=False,
        payment_status__in=["Pending", "Partial"]
    ).aggregate(p=Sum(F("grand_total") - F("paid_amount")))["p"] or Decimal("0.00")

    total_products_count = Product.objects.filter(c_filter, is_deleted=False).count()
    low_stock_count = Product.objects.filter(c_filter, is_deleted=False, stock_quantity__lte=5).count()

    context = {
        "today_sales": f"{today_sales:.2f}",
        "today_bills_count": today_bills_count,
        "today_customer_paid": f"{today_customer_paid:.2f}",
        "total_pending_amount": f"{total_pending_amount:.2f}",
        "total_products_count": total_products_count,
        "low_stock_count": low_stock_count,
        "is_admin_user": is_admin_user(request.user),
    }
    return render(request, "billing/client.html", context)


@login_required
def client_quick_status_api(request):
    """Provides client-scoped live metrics for asynchronous frontend updates."""
    c_filter = get_client_filter(request)
    from datetime import datetime as dt_cls, time as dt_time
    today = timezone.localtime().date()
    start_dt = timezone.make_aware(dt_cls.combine(today, dt_time.min))
    end_dt = timezone.make_aware(dt_cls.combine(today, dt_time.max))

    today_invoices = Invoice.objects.filter(
        c_filter,
        is_deleted=False,
        created_at__gte=start_dt,
        created_at__lte=end_dt
    )
    today_sales = today_invoices.aggregate(s=Sum("grand_total"))["s"] or Decimal("0.00")
    today_bills_count = today_invoices.count()
    total_pending = Invoice.objects.filter(
        c_filter,
        is_deleted=False,
        payment_status__in=["Pending", "Partial"]
    ).aggregate(p=Sum(F("grand_total") - F("paid_amount")))["p"] or Decimal("0.00")

    return JsonResponse({
        "status": "online",
        "today_sales": f"{today_sales:.2f}",
        "today_bills_count": today_bills_count,
        "total_pending": f"{total_pending:.2f}",
    })

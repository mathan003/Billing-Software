"""
Modular Customer View Layer - MathanHub POS
Handles Customer Directory, Ledgers, Account Statements & Credit Balances.
"""

import logging
from decimal import Decimal
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q, F
from django.http import JsonResponse

from .db_connections import get_client_filter, is_admin_user, get_customer_db_connection
from .models import Customer, Invoice, PaymentRecord

logger = logging.getLogger(__name__)


@login_required
def customer_module_view(request):
    """
    Renders the dedicated Customer Module Hub & Ledger Overview.
    Displays customer count, accounts with pending balances, and total credit outstanding.
    """
    c_filter = get_client_filter(request)

    customers = Customer.objects.filter(c_filter, is_deleted=False)
    total_customers_count = customers.count()

    # Calculate pending amounts per customer
    pending_custs_count = 0
    total_pending_amount = Decimal("0.00")

    for cust in customers:
        cust_invoices = Invoice.objects.filter(customer=cust, is_deleted=False)
        billed = cust_invoices.aggregate(Sum("grand_total"))["grand_total__sum"] or Decimal("0.00")
        paid = cust_invoices.aggregate(Sum("paid_amount"))["paid_amount__sum"] or Decimal("0.00")
        due = billed - paid
        if due > Decimal("0.00"):
            pending_custs_count += 1
            total_pending_amount += due

    context = {
        "total_customers_count": total_customers_count,
        "pending_custs_count": pending_custs_count,
        "total_pending_amount": f"{total_pending_amount:.2f}",
        "is_admin_user": is_admin_user(request.user),
    }
    return render(request, "billing/customer.html", context)


@login_required
def customer_quick_metrics_api(request):
    """Provides customer credit metrics for async dashboards."""
    c_filter = get_client_filter(request)
    customers = Customer.objects.filter(c_filter, is_deleted=False)
    total_count = customers.count()

    total_pending = Decimal("0.00")
    pending_count = 0
    for cust in customers:
        invs = Invoice.objects.filter(customer=cust, is_deleted=False)
        billed = invs.aggregate(Sum("grand_total"))["grand_total__sum"] or Decimal("0.00")
        paid = invs.aggregate(Sum("paid_amount"))["paid_amount__sum"] or Decimal("0.00")
        due = billed - paid
        if due > Decimal("0.00"):
            pending_count += 1
            total_pending += due

    return JsonResponse({
        "total_customers": total_count,
        "customers_with_due": pending_count,
        "total_pending_amount": f"{total_pending:.2f}",
    })

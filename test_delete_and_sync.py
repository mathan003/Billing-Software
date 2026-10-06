import os
import sys
import django
from decimal import Decimal
import uuid

# Setup Django environment
sys.path.insert(0, os.path.abspath("backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "billing_backend.settings")
django.setup()

from django.utils import timezone
from django.test import RequestFactory
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from billing.models import Product, Customer, Invoice, InvoiceItem, UserProfile
from billing.views import invoice_delete, customer_delete, client_delete_data
from billing.api_views import SyncPullView

def run_tests():
    print("=" * 60)
    print("  TESTING WEB DELETE & DESKTOP APP DELETION SYNC")
    print("=" * 60)

    # 1. Setup client user
    user, _ = User.objects.get_or_create(username="test_client_user")
    user.set_password("pass123")
    user.save()
    profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": "client", "shop_name": "Test Shop"})

    rf = RequestFactory()

    # 2. Setup product & customer
    prod, _ = Product.objects.get_or_create(
        sku="TEST-DEL-SKU-01",
        defaults={
            "name": "Delete Test Rice",
            "name_tamil": "அரிசி டெஸ்ட்",
            "price": Decimal("100.00"),
            "cost_price": Decimal("80.00"),
            "stock_quantity": Decimal("50.00"),
            "is_active": True,
        }
    )
    initial_stock = prod.stock_quantity

    cust, _ = Customer.objects.get_or_create(
        phone="9876599999",
        defaults={"name": "Test Delete Customer"}
    )

    # 3. Create an invoice
    inv = Invoice.objects.create(
        invoice_number=f"INV-TEST-{uuid.uuid4().hex[:6]}",
        invoice_uuid=uuid.uuid4(),
        customer=cust,
        subtotal=Decimal("200.00"),
        grand_total=Decimal("200.00"),
        paid_amount=Decimal("200.00"),
        balance_amount=Decimal("0.00"),
        client=user,
        notes="[CLOUD_SYNCED]"
    )
    InvoiceItem.objects.create(
        invoice=inv,
        product=prod,
        product_name=prod.name,
        quantity=Decimal("2.00"),
        unit_price=Decimal("100.00"),
        total_price=Decimal("200.00")
    )
    # Decrement stock as if sold
    prod.stock_quantity = initial_stock - Decimal("2.00")
    prod.save()
    print(f"[1] Created test invoice #{inv.invoice_number} (UUID: {inv.invoice_uuid})")
    print(f"    Stock after sale: {prod.stock_quantity}")

    # 4. Test invoice_delete
    req = rf.post(f"/invoices/{inv.id}/delete/")
    req.user = user
    setattr(req, "session", {"sec_token": "abc12345"})
    setattr(req, "_messages", FallbackStorage(req))
    
    resp = invoice_delete(req, inv.id)
    assert resp.status_code == 302, f"Expected 302, got {resp.status_code}"
    assert not Invoice.objects.filter(pk=inv.id).exists(), "Invoice was not deleted!"
    
    prod.refresh_from_db()
    assert prod.stock_quantity == initial_stock, f"Expected stock {initial_stock}, got {prod.stock_quantity}"
    print(f"[2] Verified invoice_delete: Invoice #{inv.invoice_number} successfully deleted.")
    print(f"    Stock restored back to: {prod.stock_quantity} [OK]")

    # 5. Test customer_delete (by non-admin client)
    req = rf.post(f"/customers/{cust.id}/delete/")
    req.user = user
    setattr(req, "session", {"sec_token": "abc12345"})
    setattr(req, "_messages", FallbackStorage(req))
    resp = customer_delete(req, cust.id)
    assert resp.status_code == 302, f"Expected 302, got {resp.status_code}"
    assert not Customer.objects.filter(pk=cust.id).exists(), "Customer was not deleted!"
    print(f"[3] Verified customer_delete: Customer '{cust.name}' deleted by client successfully [OK]")

    # 6. Test SyncPullView returns active UUIDs
    pull_view = SyncPullView.as_view()
    req = rf.get("/api/sync/pull/")
    resp = pull_view(req)
    assert resp.status_code == 200
    data = resp.data
    assert "active_invoice_uuids" in data
    assert str(inv.invoice_uuid) not in data["active_invoice_uuids"], "Deleted invoice UUID is still in active UUIDs!"
    print(f"[4] Verified SyncPullView: Deleted invoice UUID is omitted from active_invoice_uuids [OK]")

    # 7. Test client_delete_data with single_date
    today_str = timezone.now().strftime("%Y-%m-%d")
    inv_single = Invoice.objects.create(
        invoice_number=f"INV-TEST-{uuid.uuid4().hex[:6]}",
        invoice_uuid=uuid.uuid4(),
        grand_total=Decimal("350.00"),
        client=user
    )
    req = rf.post("/delete-data/", {"delete_type": "single_date", "single_date": today_str})
    req.user = user
    setattr(req, "session", {"sec_token": "abc12345"})
    setattr(req, "_messages", FallbackStorage(req))
    resp = client_delete_data(req)
    assert resp.status_code == 302
    assert not Invoice.objects.filter(pk=inv_single.id).exists(), "Single date delete failed!"
    print(f"[5] Verified client_delete_data 'single_date': Bill deleted for date {today_str} [OK]")

    # 8. Test client_delete_data with date_range
    inv_range = Invoice.objects.create(
        invoice_number=f"INV-TEST-{uuid.uuid4().hex[:6]}",
        invoice_uuid=uuid.uuid4(),
        grand_total=Decimal("450.00"),
        client=user
    )
    req = rf.post("/delete-data/", {"delete_type": "date_range", "start_date": today_str, "end_date": today_str})
    req.user = user
    setattr(req, "session", {"sec_token": "abc12345"})
    setattr(req, "_messages", FallbackStorage(req))
    resp = client_delete_data(req)
    assert resp.status_code == 302
    assert not Invoice.objects.filter(pk=inv_range.id).exists(), "Date range delete failed!"
    print(f"[6] Verified client_delete_data 'date_range': Bills deleted between dates [OK]")

    # 9. Test client_delete_data with all_invoices
    inv2 = Invoice.objects.create(
        invoice_number=f"INV-TEST-{uuid.uuid4().hex[:6]}",
        invoice_uuid=uuid.uuid4(),
        grand_total=Decimal("500.00"),
        client=user
    )
    req = rf.post("/delete-data/", {"delete_type": "all_invoices"})
    req.user = user
    setattr(req, "session", {"sec_token": "abc12345"})
    setattr(req, "_messages", FallbackStorage(req))
    resp = client_delete_data(req)
    assert resp.status_code == 302
    assert not Invoice.objects.filter(pk=inv2.id).exists(), "All invoices delete failed!"
    print(f"[7] Verified client_delete_data 'all_invoices': Store bills purged cleanly [OK]")

    # 10. Test client_delete_data with wipe_all
    cust_wipe = Customer.objects.create(name="Wipe Me", phone="9988776655", client=user)
    inv_wipe = Invoice.objects.create(
        invoice_number=f"INV-TEST-{uuid.uuid4().hex[:6]}",
        invoice_uuid=uuid.uuid4(),
        customer=cust_wipe,
        grand_total=Decimal("150.00"),
        client=user
    )
    req = rf.post("/delete-data/", {"delete_type": "wipe_all"})
    req.user = user
    setattr(req, "session", {"sec_token": "abc12345"})
    setattr(req, "_messages", FallbackStorage(req))
    resp = client_delete_data(req)
    assert resp.status_code == 302
    assert not Invoice.objects.filter(pk=inv_wipe.id).exists(), "Wipe all invoice failed!"
    assert not Customer.objects.filter(pk=cust_wipe.id).exists(), "Wipe all customer failed!"
    print(f"[8] Verified client_delete_data 'wipe_all': Complete store wipe successful [OK]")

    # Cleanup test user and product
    prod.delete()
    user.delete()
    print("=" * 60)
    print("  ALL 4 DELETE MODES & SYNC PROPAGATION TESTS PASSED! [100% OK]")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()

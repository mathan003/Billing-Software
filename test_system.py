import os
import sys
import uuid
from decimal import Decimal

# Setup Django
sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "billing_backend.settings")
import django
django.setup()

from rest_framework.test import APIClient
from billing.models import Product, Customer, Invoice, InvoiceItem, SyncLog
from windows_app import db_local

def run_integration_tests():
    print("=" * 60)
    print("  SmartBilling Online/Offline Sync Integration Test")
    print("=" * 60)

    # 1. Initialize local SQLite DB
    print("[1] Initializing Windows App local SQLite database...")
    db_local.init_db()
    print("    -> Local SQLite initialized at:", db_local.DB_PATH)

    # 2. Check Django Products in MySQL
    client = APIClient()
    print("\n[2] Testing Django /api/health/ endpoint...")
    health_resp = client.get("/api/health/")
    assert health_resp.status_code == 200, f"Health check failed: {health_resp.status_code}"
    print("    -> Health response:", health_resp.json())

    # 3. Pull catalog from Django into Windows Local SQLite
    print("\n[3] Testing /api/sync/pull/ to fetch catalog into local SQLite...")
    pull_resp = client.get("/api/sync/pull/")
    assert pull_resp.status_code == 200
    pull_data = pull_resp.json()
    print(f"    -> Pulled {pull_data['products_count']} products from Django MySQL.")

    # Upsert into local SQLite
    db_local.upsert_products(pull_data["products"])
    local_prods = db_local.get_all_products()
    print(f"    -> Local SQLite now contains {len(local_prods)} cached products.")
    assert len(local_prods) >= len(pull_data["products"]), "Product cache mismatch!"

    # 4. Simulate Cashier generating an OFFLINE invoice in Windows Desktop App
    print("\n[4] Simulating Cashier creating an OFFLINE invoice in Windows App...")
    test_sku = "SKU-MOU-001"
    prod = db_local.get_product_by_sku(test_sku)
    assert prod is not None, f"Product {test_sku} not found"

    inv_uuid = str(uuid.uuid4())
    test_inv_number = f"WIN-TEST-{inv_uuid[:6]}"

    offline_invoice_data = {
        "invoice_uuid": inv_uuid,
        "invoice_number": test_inv_number,
        "customer_name": "Suresh Raina",
        "customer_phone": "9777123456",
        "customer_email": "suresh@example.com",
        "subtotal": 499.00 * 2,
        "tax_amount": (499.00 * 2 * 0.18),
        "discount_amount": 0.0,
        "grand_total": (499.00 * 2 * 1.18),
        "payment_method": "Cash",
        "payment_status": "Paid",
        "notes": "Created while completely offline without internet",
        "created_at": "2026-09-30T14:30:00",
    }

    offline_items = [
        {
            "product_sku": test_sku,
            "product_name": prod["name"],
            "unit_price": 499.00,
            "quantity": 2.0,
            "tax_percent": 18.0,
            "tax_amount": 499.00 * 2 * 0.18,
            "discount_percent": 0.0,
            "total_price": 499.00 * 2 * 1.18,
        }
    ]

    saved_res = db_local.save_invoice(offline_invoice_data, offline_items)
    print("    -> Saved locally in SQLite:", saved_res)

    # Verify status in local SQLite is PENDING
    pending = db_local.get_pending_invoices()
    matching_pending = [i for i in pending if i["invoice_uuid"] == inv_uuid]
    assert len(matching_pending) == 1, "Invoice not found in pending list!"
    print(f"    -> Local invoice status is 'PENDING'. (Pending queue: {len(pending)} items)")

    # 5. Simulate Internet Restored: Auto-Sync Worker Pushing to Django /api/sync/push/
    print("\n[5] Simulating Internet Reconnect: Auto-Sync pushes to Django MySQL...")
    initial_product = Product.objects.get(sku=test_sku)
    initial_stock = initial_product.stock_quantity
    print(f"    -> Product stock before sync: {initial_stock}")

    push_payload = {
        "device_id": "WIN-TEST-POS-01",
        "invoices": pending,
    }

    push_resp = client.post("/api/sync/push/", push_payload, format="json")
    assert push_resp.status_code == 200, f"Sync push failed: {push_resp.data if hasattr(push_resp, 'data') else push_resp.content}"
    push_result = push_resp.json()
    print("    -> Django API response:", push_result)
    assert inv_uuid in push_result["synced_uuids"], "UUID not returned in synced list"

    # 6. Mark local SQLite invoice as SYNCED
    db_local.mark_invoices_synced(push_result["synced_uuids"])
    remaining_pending = db_local.get_pending_invoices()
    assert not any(i["invoice_uuid"] == inv_uuid for i in remaining_pending), "Invoice should not be pending!"
    print("    -> Local SQLite updated: invoice is now marked 'SYNCED'!")

    # 7. Verify MySQL database in Django
    print("\n[6] Verifying invoice and stock in Django MySQL database...")
    db_invoice = Invoice.objects.filter(invoice_uuid=inv_uuid).first()
    assert db_invoice is not None, "Invoice was not saved in MySQL!"
    assert db_invoice.items.count() == 1, "Invoice item count mismatch!"
    print(f"    -> Invoice found in MySQL: {db_invoice.invoice_number}, Total: Rs.{db_invoice.grand_total}")
    print(f"    -> Customer in MySQL: {db_invoice.customer_name} ({db_invoice.customer_phone})")

    updated_product = Product.objects.get(sku=test_sku)
    print(f"    -> Product stock after sync: {updated_product.stock_quantity} (decremented by 2)")
    assert updated_product.stock_quantity == initial_stock - Decimal("2.00"), "Stock was not decremented correctly!"

    # 8. Test Idempotency (if push is repeated, no duplicate invoice created)
    print("\n[7] Testing Idempotency (re-sending same UUID)...")
    repeat_resp = client.post("/api/sync/push/", push_payload, format="json")
    assert repeat_resp.status_code == 200
    repeat_count = Invoice.objects.filter(invoice_uuid=inv_uuid).count()
    assert repeat_count == 1, "Duplicate invoice was created!"
    print("    -> Idempotency verified: exactly 1 invoice remains in MySQL database.")

    print("\n" + "=" * 60)
    print("  ALL INTEGRATION TESTS PASSED SUCCESSFULLY! [OK]")
    print("=" * 60)

if __name__ == "__main__":
    run_integration_tests()

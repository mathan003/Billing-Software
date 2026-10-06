import os
import sys
import uuid
import django
from decimal import Decimal

# Setup Django backend
sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "billing_backend.settings")
django.setup()

from rest_framework.test import APIClient
from billing.models import Product, Customer, Invoice, InvoiceItem, CompanySettings, SyncLog

def test_full_auto_sync_ecosystem():
    print("=" * 65)
    print("  VERIFYING BI-DIRECTIONAL REAL-TIME AUTO-SYNC ECOSYSTEM")
    print("=" * 65)
    client = APIClient()

    # ---------------------------------------------------------
    # 1. Health check & Live Status endpoint
    # ---------------------------------------------------------
    print("\n[1] Testing /api/health/ and /api/live-status/...")
    health_resp = client.get("/api/health/")
    assert health_resp.status_code == 200, f"Health failed: {health_resp.status_code}"
    print("    -> Cloud Server Health:", health_resp.json()["status"])

    live_resp = client.get("/api/live-status/")
    assert live_resp.status_code == 200
    live_data = live_resp.json()
    print(f"    -> Live Status: Today Sales: Rs.{live_data['today_sales']}, Total Invoices: {live_data['total_invoices']}")

    # ---------------------------------------------------------
    # 2. Admin alters data on Web/Cloud
    # ---------------------------------------------------------
    print("\n[2] Simulating Admin updating data on Web Dashboard...")
    # Admin creates/updates a product on web
    test_sku = "SKU-AUTO-SYNC-01"
    prod, _ = Product.objects.get_or_create(
        sku=test_sku,
        defaults={
            "name": "Organic Honey 500g",
            "name_tamil": "இயற்கை தேன் 500கி",
            "price": Decimal("350.00"),
            "cost_price": Decimal("240.00"),
            "stock_quantity": Decimal("50.00"),
            "unit": "Pack",
        }
    )
    # Admin updates price on website to Rs.375.00
    prod.price = Decimal("375.00")
    prod.save()
    print(f"    -> Admin altered product {test_sku} price to: Rs.{prod.price}")

    # Admin updates Company Settings on website
    cs = CompanySettings.get_settings()
    cs.company_name = "MathanHub Retail"
    cs.phone = "+91 98765 43210"
    cs.save()
    print(f"    -> Admin updated company branding on website: '{cs.company_name}'")

    # ---------------------------------------------------------
    # 3. Client pulls from Cloud (/api/sync/pull/)
    # ---------------------------------------------------------
    print("\n[3] Testing Client App pulling latest Web/Cloud updates...")
    pull_resp = client.get("/api/sync/pull/")
    assert pull_resp.status_code == 200
    pull_data = pull_resp.json()
    assert "company_settings" in pull_data, "Company settings missing from pull"
    assert pull_data["company_settings"]["company_name"] == "MathanHub Retail"
    print("    -> Client pulled company branding:", pull_data["company_settings"]["company_name"])

    pulled_prods = {p["sku"]: p for p in pull_data["products"]}
    assert test_sku in pulled_prods, "New product not found in pull catalog"
    assert Decimal(str(pulled_prods[test_sku]["price"])) == Decimal("375.00"), "Altered price mismatch"
    print(f"    -> Client pulled updated price for {test_sku}: Rs.{pulled_prods[test_sku]['price']} [OK]")

    # ---------------------------------------------------------
    # 4. Cashier uses App Offline to generate bills
    # ---------------------------------------------------------
    print("\n[4] Simulating Cashier billing OFFLINE in Windows Desktop App...")
    inv_uuid = str(uuid.uuid4())
    test_inv_number = f"OFFLINE-BILL-{inv_uuid[:6]}"
    offline_bill = {
        "device_id": "WIN-POS-OFFLINE-01",
        "invoices": [
            {
                "invoice_uuid": inv_uuid,
                "invoice_number": test_inv_number,
                "customer_name": "Murugan Vel",
                "customer_phone": "9840123456",
                "subtotal": "375.00",
                "tax_amount": "18.75",
                "discount_amount": "0.00",
                "grand_total": "393.75",
                "paid_amount": "393.75",
                "payment_method": "Cash",
                "payment_status": "Paid",
                "notes": "Generated offline without internet",
                "created_at": "2026-10-03T12:00:00Z",
                "items": [
                    {
                        "product_sku": test_sku,
                        "product_name": "Organic Honey 500g",
                        "unit_price": "375.00",
                        "quantity": "1",
                        "tax_percent": "5.00",
                        "tax_amount": "18.75",
                        "discount_percent": "0.00",
                        "total_price": "393.75",
                    }
                ]
            }
        ],
        "products": [],  # Client does not overwrite master cloud products
        "customers": [
            {
                "name": "Murugan Vel",
                "phone": "9840123456",
                "email": "murugan@example.local",
            }
        ],
    }
    print(f"    -> Bill #{test_inv_number} created offline with UUID: {inv_uuid}")

    # ---------------------------------------------------------
    # 5. App goes Online: Auto-Push offline bills to Cloud
    # ---------------------------------------------------------
    print("\n[5] Simulating App going ONLINE -> Auto-Pushing to Cloud (/api/sync/push/)...")
    initial_stock = prod.stock_quantity
    push_resp = client.post("/api/sync/push/", data=offline_bill, format="json")
    assert push_resp.status_code == 200, f"Push failed: {push_resp.data}"
    push_result = push_resp.json()
    assert push_result["status"] == "success"
    assert inv_uuid in push_result["synced_uuids"], "Invoice UUID missing from synced list"
    print(f"    -> Server synced offline bill successfully: {push_result['synced_invoices']}")

    # ---------------------------------------------------------
    # 6. Verify Cloud Database State & Stock Deduction
    # ---------------------------------------------------------
    print("\n[6] Verifying Cloud Database state...")
    saved_inv = Invoice.objects.filter(invoice_uuid=inv_uuid).first()
    assert saved_inv is not None, "Invoice was not saved in Cloud DB"
    assert saved_inv.grand_total == Decimal("393.75")
    assert saved_inv.customer_name == "Murugan Vel"
    print(f"    -> Verified Cloud Invoice: #{saved_inv.invoice_number}, Total: Rs.{saved_inv.grand_total}")

    prod.refresh_from_db()
    assert prod.stock_quantity == initial_stock - Decimal("1.00"), "Stock was not decremented"
    print(f"    -> Verified Cloud Stock decremented from {initial_stock} to {prod.stock_quantity} [OK]")

    # ---------------------------------------------------------
    # 7. Verify Client Data Integrity (Non-Destructive)
    # ---------------------------------------------------------
    print("\n[7] Verifying Client Data Integrity (Conflict-free & non-destructive)...")
    # Verify client pull again does NOT alter invoice prices or corrupt past sales
    pull_again = client.get("/api/sync/pull/").json()
    assert len(pull_again["products"]) > 0
    # Invoice remains exactly as created
    saved_inv.refresh_from_db()
    assert saved_inv.grand_total == Decimal("393.75")
    print("    -> Past bills & client data intact without corruption or price alterations [OK]")

    # ---------------------------------------------------------
    # 8. Verify Live Web Status Auto-Refresh
    # ---------------------------------------------------------
    print("\n[8] Verifying Live Web Status reflects new synced bills...")
    live_after = client.get("/api/live-status/").json()
    assert live_after["total_invoices"] > live_data["total_invoices"], "Total invoices did not increment"
    print(f"    -> Total Invoices incremented to: {live_after['total_invoices']}")
    print(f"    -> Web Dashboard live auto-sync payload ready for seamless browser update [OK]")

    print("\n" + "=" * 65)
    print("  ALL BI-DIRECTIONAL AUTO-SYNC REQUIREMENTS PASSED! [100% OK]")
    print("=" * 65)

if __name__ == "__main__":
    test_full_auto_sync_ecosystem()


import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "billing_backend.settings")
import django
django.setup()

from decimal import Decimal
from django.test import Client
from django.contrib.auth.models import User
from billing.models import Product, Customer, Invoice, Purchase, ActiveUserSession

def run_tests():
    print("[*] Starting New Features & Security Test Suite...")
    client = Client()

    # 1. Unauthenticated Security Check
    print("\n[1] Testing Security: Unauthenticated access redirect...")
    resp = client.get("/")
    assert resp.status_code == 302, f"Expected 302 redirect, got {resp.status_code}"
    assert "/login/" in resp.url, f"Expected redirect to /login/, got {resp.url}"
    print("    [+] Blocked unauthenticated access -> Redirected to /login/ [OK]")

    # 2. Login Page Check
    print("\n[2] Testing Login Page...")
    resp = client.get("/login/")
    assert resp.status_code == 200
    assert "Sign In" in resp.content.decode("utf-8")
    assert "register" not in resp.content.decode("utf-8").lower(), "Registration link should not exist!"
    print("    [+] Login page accessible without registration link [OK]")

    # 3. Successful Login
    print("\n[3] Testing User Authentication & Session Creation...")
    login_resp = client.post("/login/", {"username": "admin", "password": "admin123"})
    assert login_resp.status_code == 302, f"Login failed: {login_resp.status_code}"
    session_count = ActiveUserSession.objects.count()
    assert session_count >= 1, "ActiveUserSession was not created"
    print(f"    [+] Logged in successfully. Active session registered [OK] (Active sessions: {session_count})")

    # 4. Dashboard Metrics Test
    print("\n[4] Testing Dashboard Metrics (Today Sales, Total Buy, Customer Paid, Pending Due)...")
    dash_resp = client.get("/")
    assert dash_resp.status_code == 200
    html = dash_resp.content.decode("utf-8")
    assert "TODAY'S SALES" in html
    assert "TOTAL BUY (PURCHASES)" in html
    assert "CUSTOMER PAID" in html
    assert "PENDING AMOUNTS" in html
    assert "Sync Monitor" not in html, "Sync Monitor should NOT be displayed in interface!"
    print("    [+] All 4 metrics rendered. 'Sync Monitor' is completely hidden [OK]")

    # 5. Customer Add & Alter Test
    print("\n[5] Testing Add & Alter Customer...")
    Customer.objects.filter(phone="9840112233").delete()
    add_cust_resp = client.post("/customers/add/", {
        "name": "Karthik Subramanian",
        "phone": "9840112233",
        "email": "karthik@example.com",
        "address": "T Nagar, Chennai",
        "gst_number": "33AAACK1234F1Z1",
    })
    cust = Customer.objects.get(phone="9840112233")
    assert cust.name == "Karthik Subramanian"
    print("    [+] Customer added:", cust.name)

    # Alter Customer
    edit_cust_resp = client.post(f"/customers/{cust.id}/edit/", {
        "name": "Karthik S. (Altered)",
        "phone": "9840112233",
        "email": "karthik_new@example.com",
        "address": "Anna Nagar, Chennai",
        "gst_number": "33AAACK1234F1Z1",
    })
    cust.refresh_from_db()
    assert cust.name == "Karthik S. (Altered)"
    print("    [+] Customer altered/edited successfully:", cust.name)

    # 6. Quick Bill Test (KG, Pack, Box) + Print & Save
    print("\n[6] Testing Quick Bill Creation (Select Product, Unit, Quantity, Print & Save)...")
    prod = Product.objects.filter(is_active=True).first()
    quick_resp = client.post("/quick-bill/", {
        "customer_id": cust.id,
        "product_id": prod.id,
        "unit_type": "KG",
        "quantity": "5",
        "payment_method": "Cash",
        "paid_amount": "300.00",  # Partial payment
    })
    assert quick_resp.status_code == 302, "Quick bill creation failed"
    new_inv = Invoice.objects.filter(customer=cust).latest("created_at")
    print(f"    [+] Quick bill created: {new_inv.invoice_number}")
    print(f"        Grand Total: Rs.{new_inv.grand_total}, Paid: Rs.{new_inv.paid_amount}, Balance Due: Rs.{new_inv.balance_amount}")
    assert new_inv.paid_amount == Decimal("300.00")
    assert new_inv.balance_amount == new_inv.grand_total - Decimal("300.00")

    # 7. PDF Download Test
    print("\n[7] Testing Receipt PDF Download...")
    pdf_resp = client.get(f"/invoices/{new_inv.id}/download/")
    assert pdf_resp.status_code == 200
    assert pdf_resp["Content-Type"] == "application/pdf"
    assert len(pdf_resp.content) > 1000
    print(f"    [+] PDF receipt generated and downloaded successfully! ({len(pdf_resp.content)} bytes)")

    # 8. Update Payment (Cash or Online Payment)
    print("\n[8] Testing Invoice Update Payment (Remaining Balance update)...")
    due_amount = new_inv.balance_amount
    pay_resp = client.post(f"/invoices/{new_inv.id}/pay/", {
        "amount": str(due_amount),
        "payment_method": "Online",
        "notes": "GooglePay UPI Transaction ID #998877",
    })
    new_inv.refresh_from_db()
    print(f"    [+] Payment recorded. New Paid: Rs.{new_inv.paid_amount}, Remaining Balance: Rs.{new_inv.balance_amount}, Status: {new_inv.payment_status}")
    assert new_inv.balance_amount == Decimal("0.00")
    assert new_inv.payment_status == "Paid"

    # 9. Products Add, Edit, Update, Remove Test (Tamil & English)
    print("\n[9] Testing Product Add, Edit, and Remove (Tamil & English)...")
    Product.objects.filter(sku="SKU-TOOR-099").delete()
    add_prod_resp = client.post("/products/add/", {
        "name_tamil": "துவரம் பருப்பு",
        "name": "Toor Dal Premium",
        "sku": "SKU-TOOR-099",
        "category": "Groceries",
        "unit": "KG",
        "price": "160.00",
        "cost_price": "125.00",
        "tax_percent": "5.00",
        "stock_quantity": "80",
    })
    toor_prod = Product.objects.get(sku="SKU-TOOR-099")
    print(f"    [+] Product added: {toor_prod.sku} ({toor_prod.unit}) @ Rs.{toor_prod.price}")

    # Edit Product Price and Stock anytime
    edit_prod_resp = client.post(f"/products/{toor_prod.id}/edit/", {
        "name_tamil": "துவரம் பருப்பு (Updated)",
        "name": "Toor Dal Super",
        "category": "Groceries",
        "unit": "Box",
        "price": "175.00",
        "cost_price": "130.00",
        "tax_percent": "5.00",
        "stock_quantity": "95",
    })
    toor_prod.refresh_from_db()
    assert toor_prod.price == Decimal("175.00")
    assert toor_prod.unit == "Box"
    print(f"    [+] Product updated anytime: {toor_prod.sku} ({toor_prod.unit}) @ Rs.{toor_prod.price}")

    # Remove Product
    del_prod_resp = client.post(f"/products/{toor_prod.id}/delete/")
    toor_prod.refresh_from_db()
    assert toor_prod.is_active == False
    print(f"    [+] Product removed safely from active inventory [OK]")

    # 10. Max 5 Concurrent Devices Limit Test
    print("\n[10] Testing Max 5 Concurrent Devices / Sessions Limit...")
    user = User.objects.get(username="admin")
    ActiveUserSession.objects.filter(user=user).delete()

    for i in range(7):
        dummy_client = Client()
        dummy_client.post("/login/", {"username": "admin", "password": "admin123"})

    active_count = ActiveUserSession.objects.filter(user=user).count()
    print(f"    [+] Simulated 7 logins -> Total active sessions in DB: {active_count}")
    assert active_count <= 5, f"Active sessions exceeded limit of 5! Found {active_count}"
    print("    [+] Maximum 5 active devices strictly enforced! [OK]")

    print("\n" + "=" * 60)
    print("  ALL NEW REQUIREMENTS VERIFIED AND PASSED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()

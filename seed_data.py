import os
import sys
import django
from decimal import Decimal

# Setup Django environment
sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "billing_backend.settings")
django.setup()

from django.contrib.auth.models import User
from billing.models import Product, Customer, Invoice, InvoiceItem

def seed():
    print("[*] Seeding database...")

    # Create Superuser if not exists
    if not User.objects.filter(username="admin").exists():
        User.objects.create_superuser("admin", "admin@example.com", "admin123")
        print("[+] Created Superuser: admin / admin123")
    else:
        print("[+] Superuser 'admin' already exists")

    # Sample Products
    sample_products = [
        {"name": "Wireless Optical Mouse", "sku": "SKU-MOU-001", "category": "Electronics", "price": Decimal("499.00"), "cost_price": Decimal("320.00"), "tax_percent": Decimal("18.00"), "stock_quantity": Decimal("45.00"), "unit": "pcs"},
        {"name": "Mechanical Keyboard RGB", "sku": "SKU-KEY-002", "category": "Electronics", "price": Decimal("1899.00"), "cost_price": Decimal("1250.00"), "tax_percent": Decimal("18.00"), "stock_quantity": Decimal("20.00"), "unit": "pcs"},
        {"name": "USB-C Fast Charging Cable 2M", "sku": "SKU-CAB-003", "category": "Accessories", "price": Decimal("249.00"), "cost_price": Decimal("90.00"), "tax_percent": Decimal("18.00"), "stock_quantity": Decimal("85.00"), "unit": "pcs"},
        {"name": "Premium Roast Coffee Beans 500g", "sku": "SKU-COF-004", "category": "Beverages", "price": Decimal("650.00"), "cost_price": Decimal("420.00"), "tax_percent": Decimal("5.00"), "stock_quantity": Decimal("30.00"), "unit": "pack"},
        {"name": "Green Tea Bags (Box of 50)", "sku": "SKU-TEA-005", "category": "Beverages", "price": Decimal("320.00"), "cost_price": Decimal("210.00"), "tax_percent": Decimal("5.00"), "stock_quantity": Decimal("40.00"), "unit": "box"},
        {"name": "Organic Almonds 1kg", "sku": "SKU-NUT-006", "category": "Groceries", "price": Decimal("850.00"), "cost_price": Decimal("680.00"), "tax_percent": Decimal("5.00"), "stock_quantity": Decimal("25.00"), "unit": "kg"},
        {"name": "Basmati Premium Rice 5kg", "sku": "SKU-RIC-007", "category": "Groceries", "price": Decimal("550.00"), "cost_price": Decimal("430.00"), "tax_percent": Decimal("0.00"), "stock_quantity": Decimal("50.00"), "unit": "bag"},
        {"name": "A4 Copier Paper (500 Sheets)", "sku": "SKU-PAP-008", "category": "Stationery", "price": Decimal("280.00"), "cost_price": Decimal("190.00"), "tax_percent": Decimal("12.00"), "stock_quantity": Decimal("4.00"), "unit": "pack"},
        {"name": "Gel Pen Pack of 10", "sku": "SKU-PEN-009", "category": "Stationery", "price": Decimal("120.00"), "cost_price": Decimal("70.00"), "tax_percent": Decimal("12.00"), "stock_quantity": Decimal("60.00"), "unit": "pack"},
        {"name": "Stainless Steel Water Bottle 1L", "sku": "SKU-BOT-010", "category": "Household", "price": Decimal("399.00"), "cost_price": Decimal("210.00"), "tax_percent": Decimal("12.00"), "stock_quantity": Decimal("18.00"), "unit": "pcs"},
    ]

    for p_data in sample_products:
        p, created = Product.objects.get_or_create(
            sku=p_data["sku"],
            defaults=p_data
        )
        if created:
            print(f"[+] Added product: {p.name}")

    # Sample Customers
    customers = [
        {"name": "Ramesh Kumar", "phone": "9876543210", "email": "ramesh@example.com"},
        {"name": "Priya Sharma", "phone": "9123456789", "email": "priya@example.com"},
        {"name": "Amit Patel", "phone": "9988776655", "email": "amit@example.com"},
    ]

    for c_data in customers:
        c, created = Customer.objects.get_or_create(
            phone=c_data["phone"],
            defaults=c_data
        )
        if created:
            print(f"[+] Added customer: {c.name}")

    print("\n[OK] Database seeding completed successfully!")

if __name__ == "__main__":
    seed()

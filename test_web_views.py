import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "billing_backend.settings")
import django
django.setup()

from django.test import Client
from django.contrib.auth.models import User

def test_web_routes():
    c = Client()
    admin_user = User.objects.filter(is_superuser=True).first()
    if not admin_user:
        admin_user = User.objects.create_superuser("admin", "admin@example.com", "admin123")
    c.force_login(admin_user)

    routes = [
        ("/", 200, "Dashboard"),
        ("/billing/", 200, "Billing Terminal"),
        ("/invoices/", 200, "Invoices List"),
        ("/products/", 200, "Products Catalog"),
        ("/customers/", 200, "Customers Directory"),
        ("/stock/", 200, "Stock Management"),
        ("/reports/", 200, "Reports & Analytics"),
        ("/api/health/", 200, "Health API"),
        ("/api/live-status/", 200, "Live Status Poller API"),
    ]

    print("[*] Testing Web & API views...")
    for path, expected_status, name in routes:
        resp = c.get(path, follow=True)
        assert resp.status_code == expected_status, f"{name} ({path}) failed with {resp.status_code}"
        print(f"  [+] {name} ({path}) -> Status {resp.status_code}")

    print("\n[OK] All Web & API views rendered perfectly!")

if __name__ == "__main__":
    test_web_routes()

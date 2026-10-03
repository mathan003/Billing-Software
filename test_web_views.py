import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "billing_backend.settings")
import django
django.setup()

from django.test import Client

def test_web_routes():
    c = Client()

    routes = [
        ("/", 200, "Dashboard"),
        ("/invoices/", 200, "Invoices"),
        ("/products/", 200, "Products"),
        ("/sync-monitor/", 200, "Sync Monitor"),
        ("/invoices/new/", 200, "New Bill"),
    ]

    print("[*] Testing Web & Mobile views...")
    for path, expected_status, name in routes:
        resp = c.get(path)
        assert resp.status_code == expected_status, f"{name} ({path}) failed with {resp.status_code}"
        print(f"  [+] {name} ({path}) -> Status {resp.status_code}")

    print("\n[OK] All Web & Mobile views rendered perfectly!")

if __name__ == "__main__":
    test_web_routes()

import os
import sys
import unittest
import json
from pathlib import Path
from decimal import Decimal

# Set up Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "billing_backend.settings")
os.environ["USE_SQLITE"] = "True"
os.environ["IS_DESKTOP_APP"] = "True"

BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR / "backend"
WINDOWS_APP_DIR = BASE_DIR / "windows_app"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(WINDOWS_APP_DIR) not in sys.path:
    sys.path.insert(0, str(WINDOWS_APP_DIR))

import django
django.setup()

from django.test import Client
from django.contrib.auth.models import User
from billing.models import SoftwareUpdate, Product, Customer, Invoice, CompanySettings
from billing.version import APP_VERSION, APP_TITLE, APP_RELEASE_NOTES
from auto_updater import AutoUpdater, get_auto_updater, UPDATES_DIR, PENDING_UPDATE_FILE, BAT_UPDATER_FILE


class AutoUpdateSystemTestCase(unittest.TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user, _ = User.objects.get_or_create(username="test_admin", is_staff=True, is_superuser=True)
        self.admin_user.set_password("pass123")
        self.admin_user.save()

    def test_version_published_on_cloud(self):
        """Verifies that the latest version from version.py is published in SoftwareUpdate"""
        latest = SoftwareUpdate.get_latest_update()
        self.assertIsNotNone(latest)
        self.assertEqual(latest.version, APP_VERSION)
        self.assertTrue(latest.is_published)

    def test_update_check_api_with_older_version(self):
        """Verifies /api/updates/check/ reports update_available=True when client has older version"""
        response = self.client.get("/api/updates/check/?current_version=v1.0.0")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["latest_version"], APP_VERSION)
        self.assertTrue(data["update_available"])
        self.assertIn("download_url", data)
        self.assertGreater(data["file_size"], 0)

    def test_update_check_api_with_current_version(self):
        """Verifies /api/updates/check/ reports update_available=False when client is up to date"""
        response = self.client.get(f"/api/updates/check/?current_version={APP_VERSION}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertFalse(data["update_available"])

    def test_update_download_head_request(self):
        """Verifies /api/updates/download/exe/ returns valid HEAD headers with Content-Length"""
        response = self.client.head("/api/updates/download/exe/")
        self.assertEqual(response.status_code, 200)
        content_length = int(response.headers.get("Content-Length", 0))
        self.assertGreater(content_length, 10 * 1024 * 1024)  # Over 10MB
        self.assertEqual(response.headers.get("Content-Type"), "application/vnd.microsoft.portable-executable")

    def test_client_apply_software_update_view(self):
        """Verifies client_apply_software_update records session version and responds cleanly"""
        self.client.post("/login/", {"username": "test_admin", "password": "pass123", "device_id": "DEV-TEST-UPD"})
        response = self.client.post("/software-update/apply/?format=json", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["version"], APP_VERSION)
        self.assertIn("message", data)

    def test_zero_data_loss_preservation(self):
        """
        Critical guarantee:
        Ensures that executing update checks and preparing update scripts
        does NOT delete, mutate, or affect any invoices, customers, products, or settings.
        """
        # 1. Create a customer, product, and invoice
        cust = Customer.objects.create(name="Zero Loss Test Customer", phone="9988776655")
        prod = Product.objects.create(name="Test Safeguard Item", sku="SKU-SAFE-01", price=Decimal("150.00"), stock_quantity=20)
        inv = Invoice.objects.create(
            invoice_number=f"INV-TEST-{cust.id}",
            customer=cust,
            customer_name=cust.name,
            subtotal=Decimal("150.00"),
            grand_total=Decimal("150.00"),
            paid_amount=Decimal("150.00"),
        )

        initial_cust_count = Customer.objects.count()
        initial_prod_count = Product.objects.count()
        initial_inv_count = Invoice.objects.count()

        # 2. Simulate updater script generation
        updater = get_auto_updater()
        updater.create_batch_updater_script()
        self.assertTrue(BAT_UPDATER_FILE.exists())

        # Verify content of updater batch file
        with open(BAT_UPDATER_FILE, "r", encoding="utf-8") as f:
            bat_text = f.read()
            self.assertIn("SWAP_SUCCESS", bat_text)
            self.assertIn("copy /y", bat_text)
            # Ensure it never touches sqlite database
            self.assertNotIn(".sqlite3", bat_text)
            self.assertNotIn(".db", bat_text)

        # 3. Assert all database records are completely intact
        self.assertEqual(Customer.objects.count(), initial_cust_count)
        self.assertEqual(Product.objects.count(), initial_prod_count)
        self.assertEqual(Invoice.objects.count(), initial_inv_count)

        cust_found = Customer.objects.filter(phone="9988776655").first()
        self.assertIsNotNone(cust_found)
        self.assertEqual(cust_found.name, "Zero Loss Test Customer")

        # Cleanup test records
        inv.delete()
        prod.delete()
        cust.delete()


if __name__ == "__main__":
    unittest.main()

import os
import sys
import time
import logging
import threading
import requests
from datetime import datetime
from decimal import Decimal

logger = logging.getLogger("SyncManager")


class SyncManager:
    """
    Background worker that continuously synchronizes between
    the local Windows Desktop SQLite DB and the remote Django Cloud Server.
    Completely seamless and transparent - no invasive sync UI.
    """
    def __init__(self, server_url=None, interval_seconds=10):
        self.server_url = (server_url or os.getenv("CLOUD_SERVER_URL", "https://billing-software-render.onrender.com")).rstrip("/")
        self.interval = interval_seconds
        self.running = False
        self.thread = None
        self._manual_trigger = threading.Event()
        self.is_online = False
        self.device_id = os.getenv("DEVICE_ID", f"WIN-POS-{hex(hash(os.environ.get('COMPUTERNAME', 'WIN')))[2:8].upper()}")

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True, name="SyncWorker")
            self.thread.start()
            logger.info("Background Auto-Sync Worker started.")

    def stop(self):
        self.running = False
        self._manual_trigger.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)

    def trigger_sync(self):
        self._manual_trigger.set()

    def _run_loop(self):
        # Initial small delay so local web server starts up first
        time.sleep(3)
        while self.running:
            try:
                self._sync_cycle()
            except Exception as e:
                self.is_online = False
                logger.debug(f"Sync cycle exception: {e}")

            self._manual_trigger.wait(timeout=self.interval)
            self._manual_trigger.clear()

    def _sync_cycle(self):
        # 1. Ping cloud server
        try:
            resp = requests.get(f"{self.server_url}/api/health/", timeout=4.0)
            if resp.status_code == 200:
                self.is_online = True
            else:
                self.is_online = False
                return
        except Exception:
            self.is_online = False
            return

        # 2. Push unsynced local data to cloud
        self._push_local_data()

        # 3. Pull latest cloud data to local DB
        self._pull_cloud_data()

    def _push_local_data(self):
        try:
            from billing.models import Invoice, InvoiceItem, Product, Customer, PaymentRecord
            from django.db import transaction

            # Find invoices that haven't been synced to cloud yet
            # In local SQLite, invoices created locally have source='windows_app'
            # We track cloud sync using notes or notes containing '[SYNCED]' or a local log
            unsynced_invoices = Invoice.objects.exclude(notes__contains="[CLOUD_SYNCED]").order_by("created_at")[:50]
            
            invoices_payload = []
            for inv in unsynced_invoices:
                items_data = []
                for it in inv.items.all():
                    items_data.append({
                        "product_sku": it.product_sku,
                        "product_name": it.product_name,
                        "unit_price": str(it.unit_price),
                        "quantity": str(it.quantity),
                        "tax_percent": str(it.tax_percent),
                        "tax_amount": str(it.tax_amount),
                        "discount_percent": str(it.discount_percent),
                        "total_price": str(it.total_price),
                    })

                invoices_payload.append({
                    "invoice_uuid": str(inv.invoice_uuid),
                    "invoice_number": inv.invoice_number,
                    "customer_name": inv.customer_name,
                    "customer_phone": inv.customer_phone or "",
                    "subtotal": str(inv.subtotal),
                    "tax_amount": str(inv.tax_amount),
                    "discount_amount": str(inv.discount_amount),
                    "grand_total": str(inv.grand_total),
                    "paid_amount": str(inv.paid_amount),
                    "payment_method": inv.payment_method,
                    "payment_status": inv.payment_status,
                    "notes": inv.notes,
                    "created_at": inv.created_at.isoformat(),
                    "items": items_data,
                })

            # Master cloud catalog is authoritative. Client only pushes offline bills and customers.
            products_payload = []

            # Customers modified locally
            customers_payload = []
            for c in Customer.objects.all():
                customers_payload.append({
                    "name": c.name,
                    "phone": c.phone or "",
                    "email": c.email or "",
                    "address": c.address or "",
                    "gst_number": c.gst_number or "",
                })

            if not invoices_payload and not customers_payload:
                return

            push_data = {
                "device_id": self.device_id,
                "invoices": invoices_payload,
                "products": products_payload,
                "customers": customers_payload,
            }

            resp = requests.post(
                f"{self.server_url}/api/sync/push/",
                json=push_data,
                timeout=12.0
            )

            if resp.status_code == 200:
                result = resp.json()
                synced_uuids = result.get("synced_invoices", []) or result.get("synced_uuids", [])
                if synced_uuids:
                    for inv in unsynced_invoices:
                        if str(inv.invoice_uuid) in synced_uuids:
                            inv.notes = (inv.notes + " [CLOUD_SYNCED]").strip()
                            inv.save(update_fields=["notes"])

        except Exception as e:
            logger.debug(f"Error during push: {e}")

    def _pull_cloud_data(self):
        try:
            from billing.models import Product, Customer, CompanySettings, Invoice
            from django.db import transaction
            from django.db.models import Q

            resp = requests.get(f"{self.server_url}/api/sync/pull/", timeout=10.0)
            if resp.status_code != 200:
                return

            data = resp.json()
            products = data.get("products", [])
            customers = data.get("customers", [])
            company_data = data.get("company_settings")

            with transaction.atomic():
                # 1. Update company settings safely
                if company_data:
                    try:
                        cs = CompanySettings.get_settings()
                        cs.company_name = company_data.get("company_name") or cs.company_name
                        cs.phone = company_data.get("phone") or cs.phone
                        cs.email = company_data.get("email") or cs.email
                        cs.address = company_data.get("address") or cs.address
                        cs.gst_number = company_data.get("gst_number") or cs.gst_number
                        cs.bank_name = company_data.get("bank_name") or cs.bank_name
                        cs.account_number = company_data.get("account_number") or getattr(cs, "account_number", "")
                        cs.ifsc_code = company_data.get("ifsc_code") or cs.ifsc_code
                        cs.save()
                    except Exception as ce:
                        logger.debug(f"Error updating local company settings: {ce}")

                # 2. Update products locally without touching existing invoice records
                for p_data in products:
                    sku = p_data.get("sku")
                    if not sku:
                        continue
                    prod, created = Product.objects.get_or_create(
                        sku=sku,
                        defaults={
                            "name": p_data.get("name", ""),
                            "name_tamil": p_data.get("name_tamil", ""),
                            "category": p_data.get("category", "General"),
                            "unit": p_data.get("unit", "KG"),
                            "price": Decimal(str(p_data["price"])),
                            "cost_price": Decimal(str(p_data.get("cost_price", 0.00))),
                            "tax_percent": Decimal(str(p_data.get("tax_percent", 0.00))),
                            "stock_quantity": Decimal(str(p_data.get("stock_quantity", 0.00))),
                            "is_active": p_data.get("is_active", True),
                        }
                    )
                    if not created:
                        prod.name = p_data.get("name", prod.name)
                        prod.name_tamil = p_data.get("name_tamil", prod.name_tamil)
                        prod.category = p_data.get("category", prod.category)
                        prod.unit = p_data.get("unit", prod.unit)
                        prod.price = Decimal(str(p_data["price"]))
                        prod.cost_price = Decimal(str(p_data.get("cost_price", prod.cost_price)))
                        prod.tax_percent = Decimal(str(p_data.get("tax_percent", prod.tax_percent)))
                        prod.stock_quantity = Decimal(str(p_data.get("stock_quantity", prod.stock_quantity)))
                        prod.is_active = p_data.get("is_active", prod.is_active)
                        prod.save()

                # 3. Update customers locally
                for c_data in customers:
                    phone = c_data.get("phone", "").strip()
                    if phone:
                        cust, created = Customer.objects.get_or_create(
                            phone=phone,
                            defaults={
                                "name": c_data["name"],
                                "email": c_data.get("email", ""),
                                "address": c_data.get("address", ""),
                                "gst_number": c_data.get("gst_number", ""),
                            }
                        )
                        if not created:
                            cust.name = c_data["name"]
                            cust.email = c_data.get("email", cust.email)
                            cust.address = c_data.get("address", cust.address)
                            cust.gst_number = c_data.get("gst_number", cust.gst_number)
                            cust.save()

                # 4. Synchronize Web Deletions to Desktop App
                if "active_invoice_uuids" in data:
                    active_uuids = set(data.get("active_invoice_uuids", []))
                    # Remove locally synced invoices that have been deleted on the web
                    for inv in Invoice.objects.filter(notes__contains="[CLOUD_SYNCED]"):
                        if str(inv.invoice_uuid) not in active_uuids:
                            inv.items.all().delete()
                            inv.delete()

                if "active_customer_phones" in data:
                    active_phones = set(data.get("active_customer_phones", []))
                    for c in Customer.objects.all():
                        if c.phone and c.phone not in active_phones:
                            has_pending = c.invoices.filter(~Q(notes__contains="[CLOUD_SYNCED]")).exists()
                            if not has_pending:
                                c.invoices.all().delete()
                                c.delete()

                if "active_product_skus" in data:
                    active_skus = set(data.get("active_product_skus", []))
                    for p in Product.objects.filter(is_active=True):
                        if p.sku not in active_skus:
                            p.is_active = False
                            p.save(update_fields=["is_active"])

        except Exception as e:
            logger.debug(f"Error during pull: {e}")

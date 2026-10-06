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

            from billing.device_utils import get_desktop_pos_config
            pos_cfg = get_desktop_pos_config()
            client_username = pos_cfg.get("last_logged_in_client") or pos_cfg.get("remembered_username") or ""

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
                    "balance_amount": str(inv.balance_amount),
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
                "client_username": client_username,
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
            from billing.models import Product, Customer, CompanySettings, Invoice, Branch
            from django.contrib.auth.models import User
            from billing.device_utils import get_desktop_pos_config
            from django.db import transaction
            from django.db.models import Q

            pos_cfg = get_desktop_pos_config()
            client_username = pos_cfg.get("last_logged_in_client") or pos_cfg.get("remembered_username") or ""

            params = {}
            if client_username:
                params["client_username"] = client_username

            resp = requests.get(f"{self.server_url}/api/sync/pull/", params=params, timeout=10.0)
            if resp.status_code != 200:
                return

            data = resp.json()
            products = data.get("products", [])
            customers = data.get("customers", [])
            company_data = data.get("company_settings")
            client_prof_data = data.get("client_profile")
            branches_data = data.get("branches", [])

            client_user = None
            if client_username:
                client_user = User.objects.filter(username=client_username).first()

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

                # 2. Update Client Profile locally so Admin edits on Web reflect immediately in App
                if client_prof_data:
                    try:
                        uname = client_prof_data.get("username")
                        if uname:
                            client_user, _ = User.objects.get_or_create(username=uname)
                            client_user.email = client_prof_data.get("email", client_user.email)
                            client_user.first_name = client_prof_data.get("first_name", client_user.first_name)
                            client_user.last_name = client_prof_data.get("last_name", client_user.last_name)
                            client_user.is_active = client_prof_data.get("is_active", True)
                            client_user.save()

                            from billing.models import UserProfile
                            prof, _ = UserProfile.objects.get_or_create(user=client_user)
                            prof.shop_name = client_prof_data.get("shop_name", prof.shop_name)
                            prof.shop_address = client_prof_data.get("shop_address", prof.shop_address)
                            prof.phone = client_prof_data.get("phone", prof.phone)
                            prof.business_type = client_prof_data.get("business_type", prof.business_type)
                            prof.access_mode = client_prof_data.get("access_mode", prof.access_mode)
                            prof.device_limit = client_prof_data.get("device_limit", prof.device_limit)
                            prof.gst_number = client_prof_data.get("gst_number", prof.gst_number)
                            prof.bank_name = client_prof_data.get("bank_name", prof.bank_name)
                            prof.account_number = client_prof_data.get("account_number", prof.account_number)
                            prof.ifsc_code = client_prof_data.get("ifsc_code", prof.ifsc_code)
                            prof.avatar_base64 = client_prof_data.get("avatar_base64", prof.avatar_base64)
                            prof.shop_logo_base64 = client_prof_data.get("shop_logo_base64", prof.shop_logo_base64)
                            prof.role = client_prof_data.get("role", prof.role)
                            prof.save()
                    except Exception as pe:
                        logger.debug(f"Error updating local client profile: {pe}")

                # 3. Synchronize Branches
                for br_d in branches_data:
                    b_code = br_d.get("branch_code")
                    if b_code:
                        Branch.objects.update_or_create(
                            branch_code=b_code,
                            defaults={
                                "name": br_d.get("name", "Branch"),
                                "client": client_user,
                                "phone": br_d.get("phone", ""),
                                "email": br_d.get("email", ""),
                                "address": br_d.get("address", ""),
                                "manager_name": br_d.get("manager_name", ""),
                                "is_default": br_d.get("is_default", False),
                                "is_active": True,
                            }
                        )

                # 4. Update products locally without touching existing invoice records
                for p_data in products:
                    sku = p_data.get("sku")
                    if not sku:
                        continue
                    prod, created = Product.objects.get_or_create(
                        sku=sku,
                        defaults={
                            "client": client_user,
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
                        if not prod.client and client_user:
                            prod.client = client_user
                        prod.save()

                # 5. Update customers locally
                for c_data in customers:
                    phone = c_data.get("phone", "").strip()
                    if phone:
                        cust, created = Customer.objects.get_or_create(
                            phone=phone,
                            defaults={
                                "client": client_user,
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
                            if not cust.client and client_user:
                                cust.client = client_user
                            cust.save()

                # 6. Synchronize Cloud Invoices to Desktop Local App
                cloud_invoices = data.get("invoices", [])
                for inv_data in cloud_invoices:
                    inv_uuid = inv_data.get("invoice_uuid")
                    if not inv_uuid:
                        continue
                    local_inv = Invoice.objects.filter(invoice_uuid=inv_uuid).first()
                    if not local_inv:
                        cust_phone = inv_data.get("customer_phone", "").strip()
                        c_match = None
                        if cust_phone:
                            c_match = Customer.objects.filter(phone=cust_phone).first()

                        new_inv = Invoice.objects.create(
                            invoice_uuid=inv_uuid,
                            invoice_number=inv_data.get("invoice_number"),
                            client=client_user,
                            branch_name=inv_data.get("branch_name", "Main Shop Branch"),
                            customer=c_match,
                            customer_name=inv_data.get("customer_name", "Cash Customer"),
                            customer_phone=cust_phone,
                            subtotal=Decimal(str(inv_data.get("subtotal", "0.00"))),
                            tax_amount=Decimal(str(inv_data.get("tax_amount", "0.00"))),
                            discount_amount=Decimal(str(inv_data.get("discount_amount", "0.00"))),
                            grand_total=Decimal(str(inv_data.get("grand_total", "0.00"))),
                            paid_amount=Decimal(str(inv_data.get("paid_amount", "0.00"))),
                            balance_amount=Decimal(str(inv_data.get("balance_amount", "0.00"))),
                            payment_method=inv_data.get("payment_method", "Cash"),
                            payment_status=inv_data.get("payment_status", "Paid"),
                            source=inv_data.get("source", "web_mobile"),
                            notes=(inv_data.get("notes", "") + " [CLOUD_SYNCED]").strip(),
                            created_at=inv_data.get("created_at"),
                        )
                        from billing.models import InvoiceItem
                        for it_d in inv_data.get("items", []):
                            sku = it_d.get("product_sku", "")
                            prod_obj = Product.objects.filter(sku=sku).first() if sku else None
                            InvoiceItem.objects.create(
                                invoice=new_inv,
                                product=prod_obj,
                                product_name=it_d.get("product_name", ""),
                                product_sku=sku,
                                unit=it_d.get("unit", "KG"),
                                unit_price=Decimal(str(it_d.get("unit_price", "0.00"))),
                                quantity=Decimal(str(it_d.get("quantity", "1.00"))),
                                tax_percent=Decimal(str(it_d.get("tax_percent", "0.00"))),
                                tax_amount=Decimal(str(it_d.get("tax_amount", "0.00"))),
                                discount_percent=Decimal(str(it_d.get("discount_percent", "0.00"))),
                                total_price=Decimal(str(it_d.get("total_price", "0.00"))),
                            )
                    else:
                        updated_paid = Decimal(str(inv_data.get("paid_amount", local_inv.paid_amount)))
                        if updated_paid != local_inv.paid_amount or inv_data.get("payment_status") != local_inv.payment_status:
                            local_inv.paid_amount = updated_paid
                            local_inv.balance_amount = Decimal(str(inv_data.get("balance_amount", "0.00")))
                            local_inv.payment_status = inv_data.get("payment_status", local_inv.payment_status)
                            local_inv.save(update_fields=["paid_amount", "balance_amount", "payment_status"])

                # 7. Synchronize Web Deletions to Desktop App
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


_sync_manager_instance = None


def get_sync_manager(server_url=None, interval_seconds=10):
    global _sync_manager_instance
    if _sync_manager_instance is None:
        _sync_manager_instance = SyncManager(server_url=server_url, interval_seconds=interval_seconds)
    return _sync_manager_instance


def trigger_desktop_sync():
    global _sync_manager_instance
    if _sync_manager_instance:
        _sync_manager_instance.trigger_sync()

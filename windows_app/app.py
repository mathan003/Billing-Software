import os
import sys
import time
import socket
import logging
import threading
import webbrowser
from pathlib import Path

# Safe stdout/stderr redirection for PyInstaller --noconsole windowless execution
class _SafeStream:
    def write(self, s): pass
    def flush(self): pass

if sys.stdout is None:
    sys.stdout = _SafeStream()
if sys.stderr is None:
    sys.stderr = _SafeStream()

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", stream=sys.stderr)
logger = logging.getLogger("SmartBillingDesktop")

# Configure Paths
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
BACKEND_DIR = PROJECT_ROOT / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

# Determine persistent data folder for SQLite
if getattr(sys, "frozen", False):
    # PyInstaller standalone EXE environment
    app_data_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SmartBillingPOS"
else:
    app_data_dir = PROJECT_ROOT

app_data_dir.mkdir(parents=True, exist_ok=True)
db_file = app_data_dir / "billing_local.sqlite3"

# Configure Django Environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "billing_backend.settings")
os.environ["USE_SQLITE"] = "True"
os.environ["IS_DESKTOP_APP"] = "True"
os.environ["LOCAL_DB_PATH"] = str(db_file)

import django
django.setup()

from billing.device_utils import get_hardware_device_id
os.environ["DEVICE_ID"] = get_hardware_device_id()

from django.core.management import call_command
from django.contrib.auth.models import User
from sync_manager import SyncManager, get_sync_manager
from auto_updater import get_auto_updater


def init_database():
    """Ensure local SQLite database is migrated and has default admin credentials"""
    try:
        logger.info(f"Checking local database at {db_file}...")

        # Pre-migration safety snapshot to guarantee zero data loss
        if db_file.exists() and db_file.stat().st_size > 0:
            try:
                backups_dir = app_data_dir / "backups"
                backups_dir.mkdir(parents=True, exist_ok=True)
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                snap_path = backups_dir / f"billing_local_pre_migration_{timestamp}.sqlite3"
                import shutil
                shutil.copy2(db_file, snap_path)
                logger.info(f"Pre-migration database snapshot created: {snap_path}")
                # Retain the most recent 10 migration snapshots
                old_snaps = sorted(backups_dir.glob("billing_local_pre_migration_*.sqlite3"), key=lambda p: p.stat().st_mtime)
                if len(old_snaps) > 10:
                    for old_s in old_snaps[:-10]:
                        try:
                            old_s.unlink()
                        except Exception:
                            pass
            except Exception as snap_err:
                logger.warning(f"Snapshot creation notice: {snap_err}")

        try:
            call_command("migrate", interactive=False, verbosity=0)
        except Exception as e:
            logger.warning(f"Standard migrate notice: {e}")

        # Verify all billing tables exist; if any is missing, run syncdb and schema_editor
        from billing.models import (
            UserProfile, CompanySettings, ActivityLog, ActiveUserSession,
            RegisteredDevice, Product, ProductCategory, Customer, Invoice, InvoiceItem,
            PaymentRecord, Purchase, Branch, StockLog, SoftwareUpdate, purge_old_customer_data
        )

        all_models = [
            UserProfile, CompanySettings, ActivityLog, ActiveUserSession,
            RegisteredDevice, Product, ProductCategory, Customer, Invoice, InvoiceItem,
            PaymentRecord, Purchase, Branch, StockLog, SoftwareUpdate
        ]

        missing_models = []
        for model in all_models:
            try:
                model.objects.first()
            except Exception:
                missing_models.append(model)

        if missing_models:
            logger.info(f"Missing tables detected for {[m.__name__ for m in missing_models]}. Running syncdb...")
            try:
                call_command("migrate", run_syncdb=True, interactive=False, verbosity=0)
            except Exception as e:
                logger.warning(f"run_syncdb notice: {e}")

            # Direct fallback table creation via Django Schema Editor
            from django.db import connection
            with connection.schema_editor() as schema_editor:
                for model in missing_models:
                    try:
                        schema_editor.create_model(model)
                        logger.info(f"Directly created table: {model._meta.db_table}")
                    except Exception:
                        pass

        # Ensure default superuser and admin profile exist (Mathan003)
        if not User.objects.filter(username="Mathan003").exists():
            admin_u = User.objects.create_superuser("Mathan003", "mathan@smartbilling.local", "M@th@n93612003")
            logger.info("Default administrator account created: Mathan003 / M@th@n93612003")
        else:
            admin_u = User.objects.get(username="Mathan003")
            admin_u.set_password("M@th@n93612003")
            admin_u.is_superuser = True
            admin_u.is_staff = True
            admin_u.save()

        prof, _ = UserProfile.objects.get_or_create(user=admin_u)
        prof.role = "admin"
        prof.device_limit = 2
        prof.save()

        # Ensure default shop branch exists
        default_br = Branch.get_default_branch()
        logger.info(f"Default Shop Branch initialized: {default_br.name} ({default_br.branch_code})")

        # Customer records are permanently retained (no automatic deletion based on date)
        # Deletion is strictly performed manually by the admin per system requirements.
        logger.info("Customer data retention: Permanent mode active (Manual deletion only).")

        # Seed initial sample products if empty
        if not Product.objects.exists():
            Product.objects.create(name_tamil="பொன்னி அரிசி", name="Ponni Rice", sku="SKU-RICE-01", unit="KG", price=55.00, cost_price=45.00, stock_quantity=1000)
            Product.objects.create(name_tamil="துவரம் பருப்பு", name="Toor Dal", sku="SKU-DAL-01", unit="KG", price=160.00, cost_price=140.00, stock_quantity=500)
            Product.objects.create(name_tamil="சர்க்கரை", name="Sugar", sku="SKU-SUGAR-01", unit="KG", price=42.00, cost_price=36.00, stock_quantity=800)
            Product.objects.create(name_tamil="காபி தூள்", name="Filter Coffee Powder", sku="SKU-COFFEE-01", unit="Pack", price=120.00, cost_price=95.00, stock_quantity=200)
            Product.objects.create(name_tamil="ஆப்பிள் பாக்ஸ்", name="Apple Box", sku="SKU-APPLE-BOX", unit="Box", price=1200.00, cost_price=950.00, stock_quantity=50)
            logger.info("Seeded initial bilingual product catalog.")

        if not Customer.objects.exists():
            Customer.objects.create(name="Ramesh Kumar", phone="9876543210")
            Customer.objects.create(name="Suresh Store", phone="9841012345")

        # Clear stale active sessions on app launch so terminal requires password unlock
        try:
            from django.contrib.sessions.models import Session
            Session.objects.all().delete()
            ActiveUserSession.objects.all().delete()
            logger.info("Cleared prior active sessions on launch; password unlock armed.")
        except Exception as e:
            logger.debug(f"Session cleanup notice: {e}")

    except Exception as e:
        logger.error(f"Error initializing local database: {e}")


def find_available_port(preferred_port=8765):
    """Finds an available TCP port for the local Waitress server"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", preferred_port))
            return preferred_port
    except OSError:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def start_waitress_server(port):
    """Starts the production-grade Waitress WSGI server in a background thread"""
    from billing_backend.wsgi import application
    from waitress import create_server

    server = create_server(application, host="127.0.0.1", port=port, threads=8)
    server_thread = threading.Thread(target=server.run, daemon=True, name="WaitressServer")
    server_thread.start()
    logger.info(f"Local POS server started on http://127.0.0.1:{port}")
    return server


def main():
    print("=" * 60)
    print("  MathanHub - Windows Offline/Online Edition")
    print("=" * 60)

    # 1. Initialize local SQLite Database
    init_database()

    # 2. Pick free port & launch embedded server
    port = find_available_port(8765)
    server = start_waitress_server(port)
    app_url = f"http://127.0.0.1:{port}/login/"

    # 3. Start Background Auto-Sync & Auto-Updater Workers
    cloud_url = os.getenv("CLOUD_SERVER_URL", "https://billing-software-production-d0f2.up.railway.app")
    sync_worker = get_sync_manager(server_url=cloud_url, interval_seconds=5)
    sync_worker.start()

    updater_worker = get_auto_updater(server_url=cloud_url)
    updater_worker.start()

    # 4. Launch Desktop Window
    try:
        import webview
        logger.info(f"Opening MathanHub Desktop Window -> {app_url}")
        
        # Create native desktop window
        # private_mode=False ensures Edge WebView2 keeps login cookies permanently stored
        window = webview.create_window(
            title="MathanHub - Windows Edition",
            url=app_url,
            width=1280,
            height=820,
            resizable=True,
            confirm_close=False,
            text_select=True,
        )

        # Locate application icon
        icon_path = None
        for p in [
            BACKEND_DIR / "static" / "img" / "logo.ico",
            PROJECT_ROOT / "logo.ico",
        ]:
            if p.exists():
                icon_path = str(p)
                break

        # Try EdgeChromium, fall back to default
        try:
            if icon_path:
                webview.start(gui="edgechromium", private_mode=False, icon=icon_path)
            else:
                webview.start(gui="edgechromium", private_mode=False)
        except Exception:
            if icon_path:
                webview.start(private_mode=False, icon=icon_path)
            else:
                webview.start(private_mode=False)

    except Exception as e:
        logger.warning(f"Native desktop window unavailable ({e}). Opening in default browser...")
        webbrowser.open(app_url)
        print(f"\nMathanHub is running at: {app_url}")
        print("Press Ctrl+C to stop the local POS server.\n")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    # Cleanup
    updater_worker.stop()
    sync_worker.stop()
    server.close()
    logger.info("MathanHub shut down cleanly.")


if __name__ == "__main__":
    main()

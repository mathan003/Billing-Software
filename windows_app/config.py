import sys
from pathlib import Path

# Paths
# When packaged as an EXE via PyInstaller, sys.frozen is True and sys.executable points to the .exe location
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent

DB_PATH = APP_DIR / "billing_local.db"

# Default Server Configuration (can be updated inside GUI Settings)
DEFAULT_SERVER_URL = "http://127.0.0.1:8000"
DEFAULT_DEVICE_ID = "WIN-DESKTOP-POS-01"
DEFAULT_SYNC_INTERVAL = 8  # seconds between auto-sync checks

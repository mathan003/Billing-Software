import os
import sys
import json
import uuid
import socket
import logging
from pathlib import Path

logger = logging.getLogger("DeviceUtils")


def get_hardware_device_id():
    """
    Returns a persistent, unique hardware identifier for this Windows machine.
    Uses Windows MachineGuid from registry if available, with stable fallback
    based on MAC address and Computer Name.
    """
    # 1. Environment override if set
    env_id = os.environ.get("DEVICE_ID")
    if env_id:
        return env_id.strip()

    # 2. Try Windows Registry MachineGuid
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
            guid, _ = winreg.QueryValueEx(key, "MachineGuid")
            winreg.CloseKey(key)
            if guid:
                clean_guid = guid.replace("-", "").upper()[:12]
                return f"WIN-{clean_guid}"
        except Exception:
            pass

    # 3. Stable fallback: Computer name + MAC address node
    try:
        comp_name = os.environ.get("COMPUTERNAME", socket.gethostname() or "POS")
        mac = uuid.getnode()
        mac_hex = hex(mac)[2:].upper()[:8]
        return f"WIN-{comp_name[:8].upper()}-{mac_hex}"
    except Exception:
        return f"WIN-POS-{uuid.uuid4().hex[:8].upper()}"


def _get_config_path():
    """Returns path to pos_config.json in LocalAppData or project root"""
    if getattr(sys, "frozen", False):
        base_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SmartBillingPOS"
    else:
        # Check if running in project root
        base_dir = Path(__file__).resolve().parent.parent.parent
    
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "pos_config.json"


def get_desktop_pos_config():
    """Reads persistent desktop POS activation and remembered username configuration"""
    cfg_file = _get_config_path()
    if cfg_file.exists():
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.debug(f"Error reading pos_config: {e}")
    return {}


def save_desktop_pos_config(data):
    """Saves or updates persistent desktop POS configuration"""
    cfg_file = _get_config_path()
    try:
        existing = get_desktop_pos_config()
        existing.update(data)
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
        return True
    except Exception as e:
        logger.warning(f"Error saving pos_config: {e}")
        return False


def clear_desktop_remembered_user():
    """Clears remembered username when user explicitly requests switching accounts"""
    cfg = get_desktop_pos_config()
    if "remembered_username" in cfg:
        del cfg["remembered_username"]
    cfg_file = _get_config_path()
    try:
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def is_desktop_environment(request=None):
    """
    Identifies whether the request is originating from the Windows Desktop App (EXE)
    or from an external Web Browser.
    """
    if os.environ.get("IS_DESKTOP_APP") == "True":
        return True
    if os.environ.get("USE_SQLITE") == "True":
        return True
    if request:
        if request.COOKIES.get("is_desktop_pos") == "true":
            return True
        ua = request.META.get("HTTP_USER_AGENT", "")
        if "PyWebView" in ua or "SmartBillingDesktop" in ua:
            return True
        if request.headers.get("X-Desktop-App") == "True":
            return True
        try:
            host = request.get_host().split(":")[0].lower()
            if host in ("127.0.0.1", "localhost", "0.0.0.0"):
                return True
        except Exception:
            pass
    return False


def check_internet_connection(server_url=None, timeout=3.5):
    """
    Checks if active internet connection is available to reach cloud server or DNS.
    Mandatory for first-time device registration in desktop app.
    """
    import requests
    # 1. Try pinging cloud server
    url = (server_url or os.getenv("CLOUD_SERVER_URL", "https://billing-software-production-d0f2.up.railway.app")).rstrip("/")
    try:
        resp = requests.get(f"{url}/api/health/", timeout=timeout)
        if resp.status_code == 200:
            return True, "Cloud server reached"
    except Exception:
        pass

    # 2. Try reliable public DNS socket check (Google DNS 8.8.8.8 / Cloudflare 1.1.1.1)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(("8.8.8.8", 53))
        s.close()
        return True, "Internet connected"
    except Exception:
        pass

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(("1.1.1.1", 53))
        s.close()
        return True, "Internet connected"
    except Exception:
        pass

    return False, "No internet connection detected"

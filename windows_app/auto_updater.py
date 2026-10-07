import os
import sys
import time
import json
import logging
import threading
import requests
import subprocess
from pathlib import Path

logger = logging.getLogger("AutoUpdater")

# Resolve application and persistent directories
if getattr(sys, "frozen", False):
    APP_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SmartBillingPOS"
    TARGET_EXE = Path(sys.executable).resolve()
else:
    APP_DATA_DIR = Path(__file__).resolve().parent.parent
    TARGET_EXE = APP_DATA_DIR / "MathanHub.exe"

UPDATES_DIR = APP_DATA_DIR / "updates"
PENDING_UPDATE_FILE = UPDATES_DIR / "pending_update.exe"
TEMP_DOWNLOAD_FILE = UPDATES_DIR / "downloading.tmp"
UPDATE_INFO_FILE = UPDATES_DIR / "update_info.json"
BAT_UPDATER_FILE = UPDATES_DIR / "apply_update.bat"


class AutoUpdater:
    """
    Background worker that checks for new releases on the cloud server,
    downloads the binary non-disruptively in the background, and seamlessly
    replaces the executable upon restart or client confirmation.

    Guarantees:
    - Zero data loss: billing_local.sqlite3 and pos_config.json are completely isolated and preserved.
    - Zero disruption: Downloads occur silently in background without blocking billing operations.
    """
    _instance = None

    def __init__(self, server_url=None, check_interval=60):
        self.server_url = (server_url or os.getenv("CLOUD_SERVER_URL", "https://billing-software-production-d0f2.up.railway.app")).rstrip("/")
        self.check_interval = check_interval
        self.running = False
        self.thread = None
        self._manual_check = threading.Event()
        self.is_downloading = False
        self.download_progress = 0
        self.available_update = None
        UPDATES_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_instance(cls, server_url=None, check_interval=60):
        if cls._instance is None:
            cls._instance = cls(server_url=server_url, check_interval=check_interval)
        return cls._instance

    @property
    def current_version(self):
        try:
            from billing.version import APP_VERSION
            return APP_VERSION
        except Exception:
            return "v2.5.0"

    def start(self):
        """Starts the background updater daemon thread"""
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True, name="AutoUpdaterThread")
            self.thread.start()
            logger.info(f"Auto-updater background worker started (Current Version: {self.current_version}).")

    def stop(self):
        """Stops the background updater worker"""
        self.running = False
        self._manual_check.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)

    def trigger_check(self):
        """Forces an immediate check for updates"""
        self._manual_check.set()

    def _run_loop(self):
        # Initial small delay to let local server spin up
        time.sleep(5)
        while self.running:
            try:
                self.check_for_updates()
            except Exception as e:
                logger.debug(f"Update check loop error: {e}")

            self._manual_check.wait(timeout=self.check_interval)
            self._manual_check.clear()

    def check_for_updates(self):
        """Queries cloud server update API and downloads if new version is available"""
        if self.is_downloading:
            return

        try:
            url = f"{self.server_url}/api/updates/check/?current_version={self.current_version}"
            resp = requests.get(url, timeout=5.0)
            if resp.status_code != 200:
                return

            data = resp.json()
            if not data.get("update_available"):
                return

            latest_version = data.get("latest_version")
            download_url = data.get("download_url")

            if not download_url:
                return

            self.available_update = data
            logger.info(f"New update found: {latest_version} (Current: {self.current_version})")

            # Check if this version is already downloaded and verified
            if self.is_update_ready():
                try:
                    if UPDATE_INFO_FILE.exists():
                        with open(UPDATE_INFO_FILE, "r", encoding="utf-8") as f:
                            info = json.load(f)
                            if info.get("version") == latest_version:
                                logger.info(f"Update {latest_version} is already downloaded and ready to apply.")
                                return
                except Exception:
                    pass

            # Proceed to download in background
            self._download_update_binary(download_url, latest_version, data.get("title", ""))

        except Exception as e:
            logger.debug(f"Failed to check updates: {e}")

    def _download_update_binary(self, download_url, version, title):
        """Streams the binary from the server into pending_update.exe safely"""
        self.is_downloading = True
        self.download_progress = 0
        try:
            logger.info(f"Beginning background download of {version} from {download_url}...")
            # If download_url is relative, prefix with server_url
            if download_url.startswith("/"):
                download_url = f"{self.server_url}{download_url}"

            with requests.get(download_url, stream=True, timeout=120) as r:
                r.raise_for_status()
                total_length = int(r.headers.get("content-length", 0))
                downloaded = 0

                with open(TEMP_DOWNLOAD_FILE, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        if not self.running:
                            return
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total_length > 0:
                                self.download_progress = int((downloaded / total_length) * 100)

            # Verification: Check file size (> 1MB minimum)
            if TEMP_DOWNLOAD_FILE.stat().st_size < 1024 * 1024:
                logger.warning("Downloaded update binary is too small or corrupt. Discarding.")
                if TEMP_DOWNLOAD_FILE.exists():
                    TEMP_DOWNLOAD_FILE.unlink()
                return

            # Replace pending update file atomically
            if PENDING_UPDATE_FILE.exists():
                try:
                    PENDING_UPDATE_FILE.unlink()
                except Exception:
                    pass

            TEMP_DOWNLOAD_FILE.replace(PENDING_UPDATE_FILE)

            # Save metadata
            with open(UPDATE_INFO_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "version": version,
                    "title": title,
                    "downloaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "file_size": PENDING_UPDATE_FILE.stat().st_size,
                }, f, indent=2)

            logger.info(f"Update {version} successfully downloaded and verified. Size: {PENDING_UPDATE_FILE.stat().st_size} bytes.")

        except Exception as e:
            logger.error(f"Error during background update download: {e}")
            if TEMP_DOWNLOAD_FILE.exists():
                try:
                    TEMP_DOWNLOAD_FILE.unlink()
                except Exception:
                    pass
        finally:
            self.is_downloading = False

    def is_update_ready(self):
        """Returns True if a complete pending update executable is downloaded on disk"""
        return PENDING_UPDATE_FILE.exists() and PENDING_UPDATE_FILE.stat().st_size > 1024 * 1024

    def get_pending_version(self):
        """Returns the version of the downloaded pending update, if any"""
        if not self.is_update_ready():
            return None
        if UPDATE_INFO_FILE.exists():
            try:
                with open(UPDATE_INFO_FILE, "r", encoding="utf-8") as f:
                    return json.load(f).get("version")
            except Exception:
                pass
        return None

    def create_batch_updater_script(self):
        """Creates the resilient detached batch script to swap executables on exit"""
        bat_content = r"""@echo off
chcp 65001 > nul
title MathanHub Desktop Auto-Updater
echo ===================================================
echo   MathanHub Automatic Synchronized Software Update
echo ===================================================
echo [1/3] Waiting for active billing processes to close...
timeout /t 2 /nobreak > nul

set TARGET_EXE=%~1
set PENDING_EXE=%~dp0pending_update.exe

if not exist "%PENDING_EXE%" (
    echo [ERROR] Pending update binary not found.
    timeout /t 2 /nobreak > nul
    start "" "%TARGET_EXE%"
    exit
)

echo [2/3] Seamlessly upgrading application binary...
set RETRIES=0
:RETRY_SWAP
copy /y "%PENDING_EXE%" "%TARGET_EXE%" > nul 2>&1
if not errorlevel 1 goto SWAP_SUCCESS

set /a RETRIES+=1
if %RETRIES% GEQ 12 goto SWAP_TIMEOUT
timeout /t 1 /nobreak > nul
goto RETRY_SWAP

:SWAP_SUCCESS
echo [3/3] Application updated successfully!
del "%PENDING_EXE%" > nul 2>&1
del "%~dp0update_info.json" > nul 2>&1
echo Launching updated MathanHub...
start "" "%TARGET_EXE%"
exit

:SWAP_TIMEOUT
echo [WARNING] Could not overwrite target executable (file may be locked).
echo Restarting previous version...
start "" "%TARGET_EXE%"
exit
"""
        with open(BAT_UPDATER_FILE, "w", encoding="utf-8") as f:
            f.write(bat_content)

    def schedule_restart(self):
        """
        Executes the detached updater script and terminates current process
        after a brief delay to allow HTTP responses to complete cleanly.
        """
        if not self.is_update_ready():
            logger.warning("Attempted restart with no pending update.")
            return False

        if not getattr(sys, "frozen", False):
            logger.info("Running in development/source mode: Detached EXE replacement simulated.")
            return True

        self.create_batch_updater_script()
        target_str = str(TARGET_EXE)
        bat_str = str(BAT_UPDATER_FILE)

        # Launch detached batch script in background
        try:
            # CREATE_NO_WINDOW (0x08000000) or DETACHED_PROCESS (0x00000008)
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            subprocess.Popen(
                ["cmd.exe", "/c", bat_str, target_str],
                creationflags=flags,
                close_fds=True,
                shell=False
            )
            logger.info(f"Launched detached updater script: {bat_str}")

            # Exit current process cleanly after 1.5 seconds so web client receives AJAX success
            def _exit_now():
                time.sleep(1.5)
                logger.info("Exiting old process for updater swap...")
                os._exit(0)

            threading.Thread(target=_exit_now, daemon=True).start()
            return True
        except Exception as e:
            logger.error(f"Failed to spawn updater batch process: {e}")
            return False


def get_auto_updater(server_url=None):
    return AutoUpdater.get_instance(server_url=server_url)

import os
import sys
import subprocess
import shutil
from pathlib import Path


def build():
    print("=" * 65)
    print("  Building SmartBilling POS Standalone Windows EXE (Unified UI)")
    print("=" * 65)

    base_dir = Path(__file__).resolve().parent
    app_entry = base_dir / "windows_app" / "app.py"
    dist_dir = base_dir / "dist"
    build_dir = base_dir / "build"
    backend_dir = base_dir / "backend"
    windows_app_dir = base_dir / "windows_app"
    dist_exe = dist_dir / "SmartBillingPOS.exe"
    root_exe = base_dir / "SmartBillingPOS.exe"

    # Pre-collect static assets to ensure staticfiles are 100% complete
    print("\n[*] Collecting static files...")
    os.environ["USE_SQLITE"] = "True"
    subprocess.run([sys.executable, str(backend_dir / "manage.py"), "collectstatic", "--noinput"], cwd=str(base_dir), check=True)

    # Command line arguments for PyInstaller
    cmd = [
        sys.executable,
        "-m", "PyInstaller",
        "--name=SmartBillingPOS",
        "--noconsole",                # Suppress black console window
        "--onefile",                  # Pack into single standalone executable
        "--clean",                    # Clean cache before build
        f"--icon={base_dir / 'logo.ico'}", # Set application icon from MathanHub logo
        # Bundle templates & static assets
        f"--add-data={backend_dir / 'templates'};templates",
        f"--add-data={backend_dir / 'staticfiles'};staticfiles",
        f"--add-data={backend_dir / 'static'};static",
        # Include search paths
        f"--paths={backend_dir}",
        f"--paths={windows_app_dir}",
        # Collect third party packages & local apps
        "--collect-all=billing",
        "--collect-all=waitress",
        "--collect-all=webview",
        "--collect-all=whitenoise",
        "--collect-all=rest_framework",
        "--collect-all=fitz",
        # Explicit billing migrations bundling
        f"--add-data={backend_dir / 'billing' / 'migrations'};billing/migrations",
        "--hidden-import=billing.migrations",
        "--hidden-import=billing.migrations.0001_initial",
        "--hidden-import=billing.migrations.0002_purchase_alter_product_options_and_more",
        "--hidden-import=billing.migrations.0003_activitylog_userprofile",
        "--hidden-import=billing.migrations.0004_companysettings_alter_activitylog_action_type",
        "--hidden-import=billing.migrations.0005_userprofile_avatar_base64_userprofile_avatar_image_and_more",
        "--hidden-import=billing.migrations.0006_branch_customer_notes_invoice_branch_name_and_more",
        "--hidden-import=billing.migrations.0007_softwareupdate_branch_client_and_more",
        "--hidden-import=billing.migrations.0008_customer_client_invoice_client_product_client_and_more",
        "--hidden-import=billing.migrations.0009_userprofile_access_mode_and_more",
        "--hidden-import=billing.migrations.0010_userprofile_gst_number",
        # Hidden imports for Django dynamic loading
        "--hidden-import=billing",
        "--hidden-import=billing.models",
        "--hidden-import=billing.views",
        "--hidden-import=billing.urls",
        "--hidden-import=billing.api_views",
        "--hidden-import=billing.device_utils",
        "--hidden-import=billing.serializers",
        "--hidden-import=billing.middleware",
        "--hidden-import=billing.admin",
        "--hidden-import=billing.pdf_generator",
        "--hidden-import=billing.context_processors",
        "--hidden-import=billing_backend",
        "--hidden-import=billing_backend.settings",
        "--hidden-import=billing_backend.urls",
        "--hidden-import=billing_backend.wsgi",
        "--hidden-import=django.contrib.admin",
        "--hidden-import=django.contrib.auth",
        "--hidden-import=django.contrib.contenttypes",
        "--hidden-import=django.contrib.sessions",
        "--hidden-import=django.contrib.messages",
        "--hidden-import=django.contrib.staticfiles",
        "--hidden-import=django.contrib.sessions.models",
        "--hidden-import=django.contrib.sessions.backends.db",
        "--hidden-import=django.db.backends.sqlite3",
        "--hidden-import=sync_manager",
        "--hidden-import=auto_updater",
        "--hidden-import=billing.version",
        "--hidden-import=requests",
        "--hidden-import=sqlite3",
        f"--distpath={dist_dir}",
        f"--workpath={build_dir}",
        str(app_entry),
    ]

    print(f"\n[*] Running PyInstaller build...")
    proc = subprocess.run(cmd, cwd=str(base_dir))

    if proc.returncode == 0:
        # Terminate any running SmartBillingPOS instances before copying
        try:
            subprocess.run(["taskkill", "/f", "/im", "SmartBillingPOS.exe"], capture_output=True)
            import time
            time.sleep(1)
        except Exception:
            pass

        try:
            if root_exe.exists():
                os.remove(root_exe)
        except Exception:
            pass
        shutil.copy2(dist_exe, root_exe)

        mathanhub_exe = base_dir / "MathanHub.exe"
        try:
            if mathanhub_exe.exists():
                os.remove(mathanhub_exe)
            shutil.copy2(dist_exe, mathanhub_exe)
        except Exception as e:
            print(f"[!] Note: MathanHub.exe is currently open ({e}), dist/SmartBillingPOS.exe is ready.")

        # Create zip package for distribution
        zip_output = base_dir / "SmartBillingPOS_Windows"
        mathan_zip_output = base_dir / "MathanHub_Windows"
        print(f"\n[*] Packaging ZIP archive...")
        shutil.make_archive(str(zip_output), "zip", root_dir=str(base_dir), base_dir="SmartBillingPOS.exe")
        shutil.make_archive(str(mathan_zip_output), "zip", root_dir=str(base_dir), base_dir="MathanHub.exe")
        
        # Copy to artifacts directory if exists
        for conv_id in ["13f8f699-258b-4440-abd3-3e754c3bb6b0", "d52feb94-acb7-4123-b76c-fd6b7e2235c8"]:
            artifacts_dir = Path(r"C:\Users\Netcom\.gemini\antigravity\brain") / conv_id
            if artifacts_dir.exists():
                shutil.copy2(f"{zip_output}.zip", artifacts_dir / "SmartBillingPOS_Windows.zip")
                shutil.copy2(f"{mathan_zip_output}.zip", artifacts_dir / "MathanHub_Windows.zip")
                print(f"[+] Artifact updated at: {artifacts_dir / 'SmartBillingPOS_Windows.zip'}")
                print(f"[+] Artifact updated at: {artifacts_dir / 'MathanHub_Windows.zip'}")

        print("\n" + "=" * 65)
        print("  STANDALONE WINDOWS EXE BUILD SUCCESSFUL!")
        print(f"  EXEs created: {root_exe} & {mathanhub_exe}")
        print(f"  ZIP packages: {zip_output}.zip & {mathan_zip_output}.zip")
        print("=" * 65)
    else:
        print(f"\n[!] Build failed with exit code: {proc.returncode}")
        sys.exit(proc.returncode)


if __name__ == "__main__":
    build()

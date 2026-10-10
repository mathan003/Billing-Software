# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('D:/billing software/logo.ico', '.'), ('D:/billing software/backend/templates', 'templates'), ('D:/billing software/backend/staticfiles', 'staticfiles'), ('D:/billing software/backend/static', 'static'), ('D:/billing software/backend/billing/migrations', 'billing/migrations')]
binaries = []
hiddenimports = ['billing.migrations', 'billing.migrations.0001_initial', 'billing.migrations.0002_purchase_alter_product_options_and_more', 'billing.migrations.0003_activitylog_userprofile', 'billing.migrations.0004_companysettings_alter_activitylog_action_type', 'billing.migrations.0005_userprofile_avatar_base64_userprofile_avatar_image_and_more', 'billing.migrations.0006_branch_customer_notes_invoice_branch_name_and_more', 'billing.migrations.0007_softwareupdate_branch_client_and_more', 'billing.migrations.0008_customer_client_invoice_client_product_client_and_more', 'billing.migrations.0009_userprofile_access_mode_and_more', 'billing.migrations.0010_userprofile_gst_number', 'billing.migrations.0011_alter_companysettings_gst_number_productcategory', 'billing.migrations.0012_registereddevice_is_verified_and_more', 'billing.migrations.0013_invoice_customer_address_alter_product_sku_and_more', 'billing.migrations.0014_customer_deleted_at_customer_is_deleted_and_more', 'billing.migrations.0015_productcategory_deleted_at_and_more', 'billing.migrations.0016_deletedclient_userprofile_deleted_at_userprofile_is_deleted', 'billing.migrations.0017_deletedproduct', 'billing.migrations.0018_deletedinvoice', 'billing', 'billing.models', 'billing.views', 'billing.views_admin', 'billing.views_client', 'billing.views_customer', 'billing.db_connections', 'billing.urls', 'billing.api_views', 'billing.api_admin', 'billing.api_client', 'billing.api_customer', 'billing.device_utils', 'billing.serializers', 'billing.middleware', 'billing.admin', 'billing.pdf_generator', 'billing.context_processors', 'billing_backend', 'billing_backend.settings', 'billing_backend.urls', 'billing_backend.wsgi', 'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'django.contrib.sessions.models', 'django.contrib.sessions.backends.db', 'django.db.backends.sqlite3', 'sync_manager', 'auto_updater', 'billing.version', 'requests', 'sqlite3']
tmp_ret = collect_all('billing')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('waitress')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('whitenoise')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('rest_framework')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['D:/billing software/windows_app/app.py'],
    pathex=['D:/billing software/backend', 'D:/billing software/windows_app'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SmartBillingPOS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['D:/billing software/logo.ico'],
)

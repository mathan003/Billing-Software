# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('D:/billing software/backend/templates', 'templates'), ('D:/billing software/backend/staticfiles', 'staticfiles'), ('D:/billing software/backend/static', 'static'), ('D:/billing software/backend/billing/migrations', 'billing/migrations')]
binaries = []
hiddenimports = ['billing.migrations', 'billing.migrations.0001_initial', 'billing.migrations.0002_purchase_alter_product_options_and_more', 'billing.migrations.0003_activitylog_userprofile', 'billing.migrations.0004_companysettings_alter_activitylog_action_type', 'billing.migrations.0005_userprofile_avatar_base64_userprofile_avatar_image_and_more', 'billing.migrations.0006_branch_customer_notes_invoice_branch_name_and_more', 'billing.migrations.0007_softwareupdate_branch_client_and_more', 'billing.migrations.0008_customer_client_invoice_client_product_client_and_more', 'billing', 'billing.models', 'billing.views', 'billing.urls', 'billing.api_views', 'billing.serializers', 'billing.middleware', 'billing.admin', 'billing.pdf_generator', 'billing.context_processors', 'billing_backend', 'billing_backend.settings', 'billing_backend.urls', 'billing_backend.wsgi', 'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'django.contrib.sessions.models', 'django.contrib.sessions.backends.db', 'django.db.backends.sqlite3', 'sync_manager', 'requests', 'sqlite3']
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
tmp_ret = collect_all('fitz')
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
)

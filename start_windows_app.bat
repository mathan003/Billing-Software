@echo off
title SmartBilling Windows Desktop App
cd /d "%~dp0windows_app"
echo ========================================================
echo   Launching SmartBilling POS Windows Desktop App
echo   Engine: Local SQLite Cache + Background Auto-Sync
echo ========================================================
python app.py
pause

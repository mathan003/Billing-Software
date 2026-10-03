@echo off
title Django SmartBilling Cloud Server
cd /d "%~dp0backend"
echo ========================================================
echo   Starting SmartBilling Django Cloud & Mobile Server
echo   Database: MySQL (billing_db)
echo   Web URL:  http://127.0.0.1:8000
echo   Admin:    http://127.0.0.1:8000/admin/
echo   API Sync: http://127.0.0.1:8000/api/sync/
echo ========================================================
python manage.py runserver 0.0.0.0:8000
pause

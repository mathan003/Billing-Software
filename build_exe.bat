@echo off
title Build SmartBilling POS Windows EXE
cd /d "%~dp0"
echo ========================================================
echo   Compiling SmartBilling Windows POS into Standalone EXE
echo ========================================================
python build_exe.py
pause

@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting GoldBot live signals...
python run_live.py
echo.
echo Bot stopped.
pause

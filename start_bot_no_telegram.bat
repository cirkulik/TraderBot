@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting GoldBot analyzer WITHOUT Telegram (journal only)...
python run_live.py --no-telegram %*
echo.
echo Bot stopped.
pause

@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting GoldBot hourly report...
python run_hourly.py --loop %*
echo.
echo Hourly report stopped.
pause

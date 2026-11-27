@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting GoldBot map (zones/points on MT5 chart)...
python run_map.py --loop %*
echo.
echo Map stopped.
pause

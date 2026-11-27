@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting GoldBot scalper (pending orders on zones/strong points)...
python run_scalper.py --loop %*
echo.
echo Scalper stopped.
pause

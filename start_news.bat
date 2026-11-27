@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Find Python that has MetaTrader5 installed.
set "PY=python"
%PY% -c "import MetaTrader5" >nul 2>&1
if errorlevel 1 set "PY=py -3.13"
%PY% -c "import MetaTrader5" >nul 2>&1
if errorlevel 1 set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"

echo Starting GoldBot news (ForexFactory -> Telegram channels)...
%PY% run_news.py --loop %*
echo.
echo News stopped.
pause

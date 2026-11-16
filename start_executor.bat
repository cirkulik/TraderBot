@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting GoldBot executor (trades on MT5 account)...
python run_executor.py %*
echo.
echo Executor stopped.
pause

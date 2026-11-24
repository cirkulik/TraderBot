@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "config.before-daily.json" (
  echo Backup not found: config.before-daily.json
  pause
  exit /b 1
)
copy /y "config.before-daily.json" "config.json" >nul
echo Restored: config.before-daily.json -^> config.json
echo.
echo This removes the daily report (01:00 MSK). Hourly and interval
echo reports stay exactly as they were.
echo.
echo Restart the report process (Ctrl+C in its window) and run start_hourly.bat.
pause

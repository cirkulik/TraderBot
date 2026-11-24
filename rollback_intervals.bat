@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "config.before-intervals.json" (
  echo Backup not found: config.before-intervals.json
  pause
  exit /b 1
)
copy /y "config.before-intervals.json" "config.json" >nul
echo Restored: config.before-intervals.json -^> config.json
echo.
echo This returns the report schedule to once an hour (intervals off).
echo Channel settings stay in the database but are ignored until intervals
echo are back in config.json.
echo.
echo Restart the report process (Ctrl+C in its window) and run start_hourly.bat.
pause

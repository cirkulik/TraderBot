@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "config.before-zone.json" (
  echo Backup not found: config.before-zone.json
  pause
  exit /b 1
)
copy /y "config.before-zone.json" "config.json" >nul
echo Restored: config.before-zone.json -^> config.json
echo.
echo This removes zone_bounce_buy / zone_bounce_sell and turns dip_late back on.
echo Everything else stays as it was.
echo.
echo Restart the analyzer so it picks up the setups:
echo   stop the analyzer window (Ctrl+C) and run start_bot.bat again
echo The executor does not need a restart.
pause

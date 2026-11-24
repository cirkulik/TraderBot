@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "config.before-noon.json" (
  echo Backup not found: config.before-noon.json
  pause
  exit /b 1
)
copy /y "config.before-noon.json" "config.json" >nul
echo Restored: config.before-noon.json -^> config.json
echo.
echo This turns sell_noon back on and returns the daily limit to 8.
echo Everything else stays as it was.
echo.
echo Restart the analyzer so it picks up the setups:
echo   stop the analyzer window (Ctrl+C) and run start_bot.bat again
echo The executor does not need a restart.
pause

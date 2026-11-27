@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "config.before-scalper.json" (
  copy /y "config.before-scalper.json" "config.json" >nul
  echo Restored: config.before-scalper.json -^> config.json
  echo The scalper block is removed; defaults keep it disabled and in dry-run.
) else (
  echo Backup config.before-scalper.json not found.
  echo Disabling scalper in config.json instead...
  python -c "import json; p='config.json'; d=json.load(open(p, encoding='utf-8-sig')); d.setdefault('scalper', {})['enabled']=False; d['scalper']['dry_run']=True; open(p, 'w', encoding='utf-8').write(json.dumps(d, ensure_ascii=False, indent=2)+'\n')"
  echo Done: scalper.enabled = false, scalper.dry_run = true.
)
echo.
echo Stop the scalper window (Ctrl+C). The running process does not reload config.
echo The analyzer, hourly report and signal executor are not affected.
pause

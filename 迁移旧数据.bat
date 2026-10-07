@echo off
cd /d "%~dp0"
title Migrate QuoteMaster data

python --version >nul 2>&1
if not errorlevel 1 (
    python "app\migrate_old.py"
    goto :end
)
py -3 --version >nul 2>&1
if not errorlevel 1 (
    py -3 "app\migrate_old.py"
    goto :end
)
if exist "python\python.exe" (
    "python\python.exe" "app\migrate_old.py"
    goto :end
)
echo [ERROR] Python not found. Run start.bat once first.
pause
:end

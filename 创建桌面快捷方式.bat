@echo off
cd /d "%~dp0"
powershell -NoProfile -Command "$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\SINLUX CRM.lnk'); $Shortcut.TargetPath = '%~dp0start.bat'; $Shortcut.WorkingDirectory = '%~dp0'; $Shortcut.IconLocation = '%SystemRoot%\System32\shell32.dll,13'; $Shortcut.Description = 'SINLUX Customer CRM'; $Shortcut.Save()"
if errorlevel 1 (
    echo.
    echo [ERROR] Could not create the shortcut. Please screenshot this window and send it back.
    pause
    exit /b 1
)
echo.
echo Done. Look for "SINLUX CRM" icon on your Desktop.
echo Double-click it next time instead of opening this folder.
echo.
pause

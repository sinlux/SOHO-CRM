@echo off
cd /d "%~dp0"
title SINLUX CRM

set "PYEXE="

rem 1) Bundled Python (python\ folder shipped with the full package): always preferred,
rem    because the built-in libraries in app\libs are built for Python 3.12.
if exist "python\python.exe" (
    set "PYEXE=python\python.exe"
    goto :gotpy
)

rem 2) System Python
python --version >nul 2>&1
if not errorlevel 1 (
    set "PYEXE=python"
    goto :gotpy
)

py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PYEXE=py -3"
    goto :gotpy
)

rem 3) Download a portable Python (only when neither of the above exists)
echo ============================================
echo  First run: downloading a portable Python (about 11MB)
echo  This only happens once. No Python was found.
echo ============================================
mkdir python 2>nul

set "PYVER=3.12.10"
set "ZIPFILE=python\pyembed.zip"
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "CURL=%SystemRoot%\System32\curl.exe"
set "URL1=https://registry.npmmirror.com/-/binary/python/%PYVER%/python-%PYVER%-embed-amd64.zip"
set "URL2=https://mirrors.huaweicloud.com/python/%PYVER%/python-%PYVER%-embed-amd64.zip"
set "URL3=https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip"

for %%U in ("%URL1%" "%URL2%" "%URL3%") do (
    if not exist "%ZIPFILE%" (
        echo Trying %%~U ...
        if exist "%CURL%" "%CURL%" -L --fail --connect-timeout 20 --max-time 300 -o "%ZIPFILE%" %%U
        if not exist "%ZIPFILE%" if exist "%PS%" "%PS%" -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; try { Invoke-WebRequest -Uri '%%~U' -OutFile '%ZIPFILE%' -TimeoutSec 300 } catch { exit 1 }"
        if exist "%ZIPFILE%" for %%S in ("%ZIPFILE%") do if %%~zS LSS 5000000 del "%ZIPFILE%"
    )
)
if exist "%ZIPFILE%" goto :unzip

echo.
echo [ERROR] Could not download Python automatically.
echo   Easiest fix: install Python 3.12 from https://www.python.org/downloads/
echo   (tick "Add python.exe to PATH"), then run this file again.
echo   Or use the full package that already contains the "python" folder.
pause
exit /b 1

:unzip
echo Extracting...
if exist "%PS%" (
    "%PS%" -NoProfile -Command "Expand-Archive -Path '%ZIPFILE%' -DestinationPath 'python' -Force"
) else (
    tar -xf "%ZIPFILE%" -C python
)
del "%ZIPFILE%"
> "python\python312._pth" echo python312.zip
>> "python\python312._pth" echo .
>> "python\python312._pth" echo ..\app
>> "python\python312._pth" echo ..\app\libs
if not exist "python\python.exe" (
    echo [ERROR] Extraction failed. Delete the "python" folder and run this file again.
    pause
    exit /b 1
)
set "PYEXE=python\python.exe"
echo Portable Python is ready.
echo.

:gotpy
echo Starting SINLUX CRM ... your browser will open http://127.0.0.1:8123
echo Keep this window open while using the program. Closing it stops the program.
echo.
%PYEXE% "app\main.py"
if errorlevel 1 (
    echo.
    echo [Program exited with an error] Please screenshot the message above and send it back.
    pause
)

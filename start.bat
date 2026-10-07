@echo off
cd /d "%~dp0"
title SINLUX CRM

set "PYEXE="

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

if exist "python\python.exe" (
    set "PYEXE=python\python.exe"
    goto :gotpy
)

echo ============================================
echo  First run: downloading a portable Python (about 11MB)
echo  This only happens once. No system Python was found.
echo ============================================
mkdir python 2>nul

set "PYVER=3.12.10"
set "ZIPFILE=python\pyembed.zip"

echo Trying mirror 1 (npmmirror)...
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; try { Invoke-WebRequest -Uri 'https://registry.npmmirror.com/-/binary/python/%PYVER%/python-%PYVER%-embed-amd64.zip' -OutFile '%ZIPFILE%' -TimeoutSec 120 } catch { exit 1 }"
if exist "%ZIPFILE%" goto :unzip

echo Trying mirror 2 (huaweicloud)...
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; try { Invoke-WebRequest -Uri 'https://mirrors.huaweicloud.com/python/%PYVER%/python-%PYVER%-embed-amd64.zip' -OutFile '%ZIPFILE%' -TimeoutSec 120 } catch { exit 1 }"
if exist "%ZIPFILE%" goto :unzip

echo Trying mirror 3 (python.org)...
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; try { Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip' -OutFile '%ZIPFILE%' -TimeoutSec 180 } catch { exit 1 }"
if exist "%ZIPFILE%" goto :unzip

echo.
echo [ERROR] All three download sources failed. Please check your network connection,
echo or manually download python-%PYVER%-embed-amd64.zip and extract it into the "python" folder.
pause
exit /b 1

:unzip
echo Extracting...
powershell -NoProfile -Command "Expand-Archive -Path '%ZIPFILE%' -DestinationPath 'python' -Force"
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

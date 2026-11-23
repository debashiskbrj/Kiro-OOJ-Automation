@echo off
REM ==================================================================
REM  OOJ Process Automation  -  one-click start
REM  Owner & author: KABIDEBA (SWA India - Automation)
REM  Help / problems / corrections: kabideba@amazon.com
REM ==================================================================
REM Double-click this file. It finds Python, runs the weekly health check
REM when due, starts the local page (127.0.0.1:8000) and opens it.
REM If the page is already running it just opens it again.
title OOJ Process Automation - by KABIDEBA
cd /d "%~dp0"

set "PYCMD="
py -3.13 -c "import sys" >nul 2>&1 && set "PYCMD=py -3.13"
if not defined PYCMD if exist "C:\Windows\py.exe" C:\Windows\py.exe -3.13 -c "import sys" >nul 2>&1 && set "PYCMD=C:\Windows\py.exe -3.13"
if not defined PYCMD py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD goto :nopython

%PYCMD% "tools\launch.py" %*
echo.
pause
exit /b

:nopython
echo ==================================================================
echo   OOJ Process Automation  -  by KABIDEBA
echo ==================================================================
echo   Python 3.13 is not installed on this laptop.
echo   Install it from Software Center, or from https://www.python.org
echo   (tick "Add python.exe to PATH"), then double-click this file again.
echo.
choice /C YN /M "Try to install Python 3.13 automatically now (winget)"
if errorlevel 2 goto :end
winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
echo.
echo   When the install finishes, CLOSE this window and double-click
echo   "Start OOJ Web.bat" again.
:end
echo.
echo   Need help? Contact the owner KABIDEBA: kabideba@amazon.com
pause

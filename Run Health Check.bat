@echo off
REM ==================================================================
REM  OOJ Process Automation  -  weekly health check (bug-fix run)
REM  Owner & author: KABIDEBA (SWA India - Automation)
REM ==================================================================
REM Runs automatically once a week from "Start OOJ Web.bat".
REM Double-click this any time something looks wrong. It fixes what it
REM safely can and writes a report you can send to KABIDEBA.
title OOJ Health Check - by KABIDEBA
cd /d "%~dp0"

set "PYCMD="
py -3.13 -c "import sys" >nul 2>&1 && set "PYCMD=py -3.13"
if not defined PYCMD if exist "C:\Windows\py.exe" C:\Windows\py.exe -3.13 -c "import sys" >nul 2>&1 && set "PYCMD=C:\Windows\py.exe -3.13"
if not defined PYCMD py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD (
  echo Python 3.13 is not installed. Double-click "Start OOJ Web.bat" for install help.
  echo Need help? Contact the owner KABIDEBA: kabideba@amazon.com
  pause
  exit /b 1
)
%PYCMD% "tools\launch.py" --check
echo.
pause

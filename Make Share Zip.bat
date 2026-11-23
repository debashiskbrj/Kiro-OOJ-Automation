@echo off
REM Owner-only (KABIDEBA): builds the zip to share with colleagues on the Desktop.
REM Never packs run data, reports, git, Kiro/NeuroTrace files or tokens.
title Make OOJ Share Zip - KABIDEBA
cd /d "%~dp0"
set "PYCMD=py -3.13"
py -3.13 -c "import sys" >nul 2>&1 || set "PYCMD=C:\Windows\py.exe -3.13"
%PYCMD% "tools\build_share_zip.py"
echo.
pause

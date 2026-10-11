@echo off
REM One-click launcher for the local OOJ web runner.
REM Starts the stdlib web server (127.0.0.1) and opens the page in your browser.
REM On the first start the server auto-installs any MISSING Python packages and shows a
REM read-only checklist of the few manual steps only you can do (Midway login, Edge
REM sign-in). A new associate just needs to run this / restart the server.
cd /d "%~dp0"
py -3.13 "tools\web\server.py" --port 8000
pause

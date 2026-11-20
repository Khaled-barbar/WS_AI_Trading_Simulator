@echo off
REM Opens the dashboard in the browser (starts it if not running)
cd /d "%~dp0"
set PYTHONUTF8=1
py -3 -m lab dashboard

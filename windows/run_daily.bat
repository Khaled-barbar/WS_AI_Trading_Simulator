@echo off
REM Daily run (14:00): Gemini once a month + inbox + prices + resolve + report
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist logs mkdir logs
echo ==== %date% %time% ==== >> logs\run.log
py -3 -m lab monthly >> logs\run.log 2>&1

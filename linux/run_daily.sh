#!/usr/bin/env bash
# Daily run (14:00): Gemini once a month + inbox + prices + resolve + report
cd "$(dirname "$(readlink -f "$0")")" || exit 1
mkdir -p logs
echo "==== $(date '+%F %T') ====" >> logs/run.log
PYTHONUTF8=1 python3 -m lab monthly >> logs/run.log 2>&1

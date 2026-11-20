#!/data/data/com.termux/files/usr/bin/bash
# Daily run: inbox + prices + resolve + report. Runs at most once per day, from 14:00 on
# (cron calls it hourly 14:05-23:05; opening the dashboard also calls it to catch up).
cd "$(dirname "$(readlink -f "$0")")" || exit 1
mkdir -p logs
TODAY="$(date +%F)"
[ "$(date +%H)" -ge 14 ] || exit 0
[ "$(cat logs/.daily_done 2>/dev/null)" = "$TODAY" ] && exit 0
mkdir logs/.daily_lock 2>/dev/null || exit 0          # another run is in progress
trap 'rmdir logs/.daily_lock' EXIT
command -v termux-wake-lock >/dev/null && termux-wake-lock
echo "==== $(date '+%F %T') ====" >> logs/run.log
if PYTHONUTF8=1 python -m lab monthly >> logs/run.log 2>&1; then
  echo "$TODAY" > logs/.daily_done
fi

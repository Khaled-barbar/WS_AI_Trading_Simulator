#!/data/data/com.termux/files/usr/bin/bash
# Starts the dashboard in the background (if it is not running) and opens it in the browser.
#   --no-open   start only (used at boot)
cd "$(dirname "$(readlink -f "$0")")" || exit 1
mkdir -p logs
pgrep -x crond >/dev/null || crond 2>/dev/null || true
if ! python - <<'PY' 2>/dev/null
import socket, sys
s = socket.socket(); s.settimeout(0.5)
sys.exit(0 if s.connect_ex(("127.0.0.1", 8765)) == 0 else 1)
PY
then
  PYTHONUTF8=1 nohup python -m lab dashboard --no-browser >> logs/dashboard.log 2>&1 &
  for _ in $(seq 1 40); do
    python -c 'import socket,sys; s=socket.socket(); s.settimeout(0.3); sys.exit(s.connect_ex(("127.0.0.1",8765)))' 2>/dev/null && break
    sleep 0.25
  done
fi
# Missed today's 14:00 run (phone asleep or off)? run it now in the background
nohup ./run_daily.sh >/dev/null 2>&1 &
[ "${1:-}" = "--no-open" ] || termux-open-url "http://127.0.0.1:8765/"

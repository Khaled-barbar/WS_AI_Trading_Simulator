#!/usr/bin/env bash
# Agents Lab - Linux setup (systemd user units). Run once: ./install.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
echo "Agents Lab folder: $ROOT"
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
chmod +x "$ROOT"/*.sh

if command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
  U="$HOME/.config/systemd/user"; mkdir -p "$U"
  cat > "$U/agents-lab-daily.service" <<UNIT
[Unit]
Description=Agents Lab daily run
[Service]
Type=oneshot
ExecStart=$ROOT/run_daily.sh
UNIT
  # Persistent=true: if the PC was off at 14:00, it runs at next boot/login
  cat > "$U/agents-lab-daily.timer" <<UNIT
[Unit]
Description=Agents Lab daily at 14:00
[Timer]
OnCalendar=*-*-* 14:00:00
Persistent=true
[Install]
WantedBy=timers.target
UNIT
  cat > "$U/agents-lab-dashboard.service" <<UNIT
[Unit]
Description=Agents Lab dashboard (http://127.0.0.1:8765)
[Service]
WorkingDirectory=$ROOT
Environment=PYTHONUTF8=1
ExecStart=$(command -v python3) -m lab dashboard --no-browser
Restart=on-failure
[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now agents-lab-daily.timer agents-lab-dashboard.service
  echo "systemd: daily timer 14:00 + dashboard service enabled."
  echo "  (to keep them running when logged out: sudo loginctl enable-linger $USER)"
elif command -v crontab >/dev/null; then
  LINE="0 14 * * * $ROOT/run_daily.sh"
  ( crontab -l 2>/dev/null | grep -v 'run_daily.sh' || true ; echo "$LINE" ) | crontab -
  echo "systemd user session not available: added cron job: $LINE"
  echo "Note: cron does not catch up missed runs, and the dashboard will not auto-start;"
  echo "      open it with ./open_dashboard.sh"
else
  echo "WARNING: neither systemd user session nor crontab found."
  echo "         Schedule $ROOT/run_daily.sh daily at 14:00 with your system's scheduler."
fi

# Application-menu launcher
A="$HOME/.local/share/applications"; mkdir -p "$A"
cat > "$A/agents-lab.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Agents Lab
Comment=Investment agents experiment dashboard
Exec=$ROOT/open_dashboard.sh
Terminal=false
Categories=Office;Finance;
DESK
echo "Menu launcher 'Agents Lab' created."

cd "$ROOT"
mkdir -p prompts
for ag in gemini chatgpt claude; do
  python3 -m lab print-prompt "$ag" --out "prompts/${ag}_scheduled_prompt.txt" >/dev/null
done
# Remember where Agents Lab is installed, so the updater finds it
mkdir -p "$HOME/.config/agents-lab"
printf '%s\n' "$ROOT" > "$HOME/.config/agents-lab/install_dir"
echo; echo "Done. Dashboard: http://127.0.0.1:8765  (or ./open_dashboard.sh)"

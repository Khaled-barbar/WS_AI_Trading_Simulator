#!/data/data/com.termux/files/usr/bin/bash
# Agents Lab — Android (Termux) setup. Run once: bash install.sh   (safe to run again)
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
echo "Agents Lab folder: $ROOT"
case "${PREFIX:-}" in
  *com.termux*) ;;
  *) echo "This is the Android version: run it inside the Termux app."; exit 1 ;;
esac
command -v python >/dev/null || pkg install -y python
command -v crond >/dev/null || pkg install -y cronie
python -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required (pkg upgrade python)"'
chmod +x "$ROOT"/*.sh
mkdir -p "$ROOT/inbox" "$ROOT/logs" "$ROOT/prompts"

# Daily run: every hour from 14:05 to 23:05; run_daily.sh runs only once per day
# (so a phone that was asleep at 14:00 still gets its run later the same day)
LINE="5 14-23 * * * $ROOT/run_daily.sh"
( crontab -l 2>/dev/null | grep -v 'agents-lab\|run_daily.sh' || true ; echo "$LINE # agents-lab" ) | crontab -
pgrep -x crond >/dev/null || crond
echo "Daily run scheduled (cron, 14:00, with catch-up until 23:00)."

# Termux:Boot (optional app): start cron + dashboard when the phone starts
mkdir -p "$HOME/.termux/boot"
cat > "$HOME/.termux/boot/agents-lab" <<BOOT
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
pgrep -x crond >/dev/null || crond
"$ROOT/open_dashboard.sh" --no-open
BOOT
chmod +x "$HOME/.termux/boot/agents-lab"

# Termux:Widget (optional app): home-screen shortcut "Agents Lab"
mkdir -p "$HOME/.shortcuts"
cat > "$HOME/.shortcuts/Agents Lab" <<SC
#!/data/data/com.termux/files/usr/bin/bash
exec "$ROOT/open_dashboard.sh"
SC
chmod +x "$HOME/.shortcuts/Agents Lab"

# Shortcut command inside Termux: type  agents-lab
cat > "$PREFIX/bin/agents-lab" <<CMD
#!/data/data/com.termux/files/usr/bin/bash
cd "$ROOT" || exit 1
if [ \$# -eq 0 ]; then exec "$ROOT/open_dashboard.sh"; fi
PYTHONUTF8=1 exec python -m lab "\$@"
CMD
chmod +x "$PREFIX/bin/agents-lab"

cd "$ROOT"
for ag in gemini chatgpt claude; do
  python -m lab print-prompt "$ag" --out "prompts/${ag}_scheduled_prompt.txt" >/dev/null
  python -m lab print-prompt "$ag" --track stocks --out "prompts/${ag}_stocks_scheduled_prompt.txt" >/dev/null
done
# Remember where Agents Lab is installed, so the updater finds it
mkdir -p "$HOME/.config/agents-lab"
printf '%s\n' "$ROOT" > "$HOME/.config/agents-lab/install_dir"

echo
echo "Done. Opening the dashboard: http://127.0.0.1:8765"
echo "  - In Chrome: menu ⋮ → 'Add to Home screen' for an app icon."
echo "  - Type  agents-lab  in Termux to open it again."
echo "  - First prices download: press 'Run now' in the dashboard (about 2 minutes)."
"$ROOT/open_dashboard.sh"

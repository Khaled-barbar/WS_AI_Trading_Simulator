#!/data/data/com.termux/files/usr/bin/bash
# Removes the cron job, boot script, shortcuts and the agents-lab command (keeps your data)
( crontab -l 2>/dev/null | grep -v 'agents-lab\|run_daily.sh' ) | crontab - 2>/dev/null || true
pkill -f '[-]m lab dashboard' 2>/dev/null || true
rm -f "$HOME/.termux/boot/agents-lab" "$HOME/.shortcuts/Agents Lab" "$PREFIX/bin/agents-lab"
echo "Removed. Your data is still in $(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

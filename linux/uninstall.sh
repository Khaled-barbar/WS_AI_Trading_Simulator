#!/usr/bin/env bash
# Removes timers, services, cron line and launcher (keeps your data)
systemctl --user disable --now agents-lab-daily.timer agents-lab-dashboard.service 2>/dev/null || true
rm -f ~/.config/systemd/user/agents-lab-{daily.service,daily.timer,dashboard.service}
systemctl --user daemon-reload 2>/dev/null || true
( crontab -l 2>/dev/null | grep -v 'run_daily.sh' ) | crontab - 2>/dev/null || true
rm -f ~/.local/share/applications/agents-lab.desktop
echo "Removed."

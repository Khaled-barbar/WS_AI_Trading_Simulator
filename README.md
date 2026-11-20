# Agents Lab — WS AI Trading Simulator

A **paper-trading** experiment: every month (on the 20th), Gemini, ChatGPT (Codex) and Claude answer
the same investment question for a Wealthsimple TFSA. Their probability forecasts are scored with the
Brier score, and their allocations are compared with a simple benchmark (XEQT.TO). No real money is
moved by this app.

Runs locally on **Windows** and **Linux** (Python 3.11+, standard library only). The dashboard is
at http://127.0.0.1:8765, in Arabic or English.

## Download

| What | File |
|---|---|
| Fresh install — Windows | [`releases/AgentsLab-windows.zip`](releases/AgentsLab-windows.zip) |
| Fresh install — Linux | [`releases/AgentsLab-linux.zip`](releases/AgentsLab-linux.zip) |
| Update an existing install (Windows **and** Linux) | [`releases/AgentsLab-update.pyz`](releases/AgentsLab-update.pyz) |

Current version: see [`VERSION`](VERSION).

### Fresh install
- **Windows:** unzip, then double-click `install.bat`.
- **Linux:** unzip, then run `./install.sh`.

Full instructions (Arabic) are in the `README.md` inside each zip.

### Updating
- **From the dashboard (1.2.0 and later):** click **Check for updates** in the header and then
  **Install**. The dashboard compares its version with [`VERSION`](VERSION) on `main`, downloads
  `main`, and runs that version's updater.
- **Manually (any version, including 1.0 and 1.1, which have no button):** download
  `releases/AgentsLab-update.pyz`, then double-click it on Windows or run
  `python3 AgentsLab-update.pyz` on Linux.

Either way the updater:
1. backs up `lab.db`, `config.toml` and every file it replaces into `backups/`;
2. installs every file of the latest version;
3. runs **every** `config.toml` migration between the installed version and the latest one, in
   order, so any old version goes straight to the latest;
4. installs packages listed in `requirements.txt`;
5. tests the result and rolls everything back automatically if anything fails.

Your data (`lab.db`, `inbox/`, `logs/`, `report.md`) is never overwritten.

## Repository layout

```
VERSION            the version on main; the dashboard's update check reads this file
common/            files for both platforms (lab/ package, schema.sql, default config.toml,
                   requirements.txt)
windows/           Windows scripts + README
linux/             Linux scripts + README
updater/updater.py the updater (bundled into the .pyz, and run from source by the dashboard button)
build.py           builds releases/
releases/          built files, committed so they can be downloaded directly
```

## Releasing a new version
1. Change files in `common/`, `windows/` or `linux/`.
2. If `config.toml` must change for existing installs, add a migration to `MIGRATIONS` in
   `updater/updater.py`. Never edit or remove old migrations: they are what upgrades old installs.
3. Raise the number in `VERSION`, for example `1.2.0` → `1.3.0`.
4. Run `python build.py`, then commit and push to `main`.

Installed dashboards see the new version the next time someone clicks **Check for updates**.

#!/usr/bin/env bash
# Opens the dashboard in the browser (starts it if not running)
cd "$(dirname "$(readlink -f "$0")")" || exit 1
PYTHONUTF8=1 exec python3 -m lab dashboard

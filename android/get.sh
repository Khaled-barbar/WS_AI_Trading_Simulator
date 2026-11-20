#!/data/data/com.termux/files/usr/bin/bash
# Agents Lab for Android — one-command install inside Termux:
#   curl -fsSL https://raw.githubusercontent.com/Khaled-barbar/WS_AI_Trading_Simulator/main/android/get.sh | bash
# Installs to ~/agents-lab (or $AGENTSLAB_DIR). An existing install is updated, never erased.
set -euo pipefail
REPO="Khaled-barbar/WS_AI_Trading_Simulator"
DIR="${AGENTSLAB_DIR:-$HOME/agents-lab}"

case "${PREFIX:-}" in
  *com.termux*) ;;
  *) echo "Run this inside the Termux app on Android."; exit 1 ;;
esac

echo "== Installing packages (python, curl, unzip, cronie) =="
pkg update -y
pkg install -y python curl unzip cronie

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
if [ -f "$DIR/lab/cli.py" ] && [ -f "$DIR/config.toml" ]; then
  echo "== Agents Lab is already installed in $DIR: updating it (your data is kept) =="
  curl -fsSL -o "$TMP/AgentsLab-update.pyz" "https://github.com/$REPO/raw/main/releases/AgentsLab-update.pyz"
  python "$TMP/AgentsLab-update.pyz" --target "$DIR" --yes
  exec bash "$DIR/install.sh"
fi

echo "== Downloading Agents Lab =="
curl -fsSL -o "$TMP/AgentsLab-android.zip" "https://github.com/$REPO/raw/main/releases/AgentsLab-android.zip"
mkdir -p "$DIR"
unzip -q -o "$TMP/AgentsLab-android.zip" -d "$DIR"
exec bash "$DIR/install.sh"

#!/usr/bin/env bash
# PocketSlice one-shot installer for Debian 12 / Ubuntu 22.04+ (bare metal, VM or Proxmox LXC).
#
#   curl -fsSL https://raw.githubusercontent.com/westfrost/Testilento/main/scripts/install.sh | sudo bash
#
# What it does:  installs Docker, clones PocketSlice to /opt/pocketslice, writes .env,
#                builds the image (downloads OrcaSlicer) and starts the app on port 8080.
# Re-running it updates an existing installation.
#
# Non-interactive:  MOONRAKER_URL=http://192.168.1.50:7125 PORT=8080 ./install.sh
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/westfrost/Testilento.git}"
BRANCH="${BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/opt/pocketslice}"
PORT="${PORT:-8080}"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "Run as root:  sudo bash install.sh"
[ "$(uname -m)" = "x86_64" ] || [ "$(uname -m)" = "aarch64" ] || die "Unsupported CPU $(uname -m)"
command -v apt-get >/dev/null || die "This installer expects Debian/Ubuntu (apt-get)"

say "Installing base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl git >/dev/null

if ! command -v docker >/dev/null; then
  say "Installing Docker (official get.docker.com script)"
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null 2>&1 || true
docker compose version >/dev/null 2>&1 || die "docker compose plugin missing – install docker-compose-plugin"

if [ -d "$INSTALL_DIR/.git" ]; then
  say "Updating existing installation in $INSTALL_DIR"
  git -C "$INSTALL_DIR" fetch -q origin "$BRANCH"
  git -C "$INSTALL_DIR" reset -q --hard "origin/$BRANCH"
else
  say "Cloning PocketSlice to $INSTALL_DIR"
  git clone -q --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

if [ ! -f .env ]; then
  say "Creating .env"
  if [ -z "${MOONRAKER_URL:-}" ] && [ -t 0 ]; then
    read -r -p "Moonraker URL of your printer (e.g. http://192.168.1.50:7125) [http://voron.local:7125]: " MOONRAKER_URL
  fi
  MOONRAKER_URL="${MOONRAKER_URL:-http://voron.local:7125}"
  cat > .env <<EOF
MOONRAKER_URL=$MOONRAKER_URL
MOONRAKER_API_KEY=
PRINTER_NAME=${PRINTER_NAME:-Voron}
APP_PASSWORD=
PORT=$PORT
TS_AUTHKEY=
EOF
fi
mkdir -p profiles

say "Building the image – this downloads OrcaSlicer (~150 MB) and takes a few minutes"
docker compose up -d --build

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
say "PocketSlice is running"
cat <<EOF

   Open on your phone or PC:   http://${IP:-<server-ip>}:${PORT}
   The setup wizard starts automatically the first time.

   Update later:   sudo bash $INSTALL_DIR/scripts/install.sh
   Logs:           docker logs -f pocketslice
   Restart:        cd $INSTALL_DIR && docker compose restart

EOF

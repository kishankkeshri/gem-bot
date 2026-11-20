#!/usr/bin/env bash
# Install / update Gem-bot on the Pi. Safe to run again after every `git pull`.
#
#   cd ~/gem-bot && sudo bash deploy/install-pi.sh
#
# Touches only gembot-* units and ~/gem-bot. Reach's services are left alone.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo bash deploy/install-pi.sh" >&2; exit 1; }

APP=/home/pi/gem-bot
say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

for f in .env config/entities.yaml; do
  [[ -f "$APP/$f" ]] || { echo "Missing $APP/$f - copy it from the Mac first (deploy/push-to-pi.sh does this)." >&2; exit 1; }
done

say "System packages (pdftotext, OCR)"
apt-get update -qq
apt-get install -y -qq --no-upgrade poppler-utils tesseract-ocr tesseract-ocr-eng python3-venv >/dev/null

say "Python environment"
[[ -d "$APP/.venv" ]] || sudo -u pi python3 -m venv "$APP/.venv"
sudo -u pi "$APP/.venv/bin/pip" install -q --upgrade pip
sudo -u pi "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

say "Permissions"
mkdir -p "$APP/data" && chown -R pi:pi "$APP/data"
chmod 600 "$APP/.env" "$APP/config/entities.yaml"

say "Services"
install -m 644 "$APP"/deploy/gembot-daily.service "$APP"/deploy/gembot-daily.timer "$APP"/deploy/gembot-bot.service /etc/systemd/system/
# The Pi has no clock battery: make time-sync.target wait for a real NTP sync before runs.
systemctl enable systemd-time-wait-sync.service 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now gembot-daily.timer
systemctl enable gembot-bot.service
systemctl restart gembot-bot.service

say "Status"
systemctl --no-pager --lines=0 status gembot-bot.service | head -3
systemctl --no-pager list-timers gembot-daily.timer
echo
echo "Logs:     journalctl -u gembot-daily -n 100 --no-pager"
echo "          journalctl -u gembot-bot -f"
echo "Run now:  sudo systemctl start gembot-daily   (or wait for 07:30)"

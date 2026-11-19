#!/usr/bin/env bash
# From the Mac: put Gem-bot on the Pi (code from GitHub, secrets + state from this Mac), then install.
#
#   bash deploy/push-to-pi.sh                # Pi at pi@reach.local
#   PI=pi@100.x.y.z bash deploy/push-to-pi.sh   # e.g. its Tailscale address when away from home
set -euo pipefail
PI="${PI:-pi@reach.local}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO=https://github.com/kishankkeshri/gem-bot.git

echo "==> Code (public repo, no key needed)"
ssh "$PI" "test -d ~/gem-bot/.git || git clone -q $REPO ~/gem-bot; cd ~/gem-bot && git pull -q --ff-only && git log --oneline -1"

echo "==> Secrets and firm details (never in git)"
ssh "$PI" 'mkdir -p ~/gem-bot/config ~/gem-bot/data'
scp -q "$ROOT/.env" "$PI:gem-bot/.env"
scp -q "$ROOT/config/entities.yaml" "$PI:gem-bot/config/entities.yaml"
ssh "$PI" 'chmod 600 ~/gem-bot/.env ~/gem-bot/config/entities.yaml'

echo "==> State: seen bids, card buttons, cached PDFs + AI answers (so the Pi doesn't resend everything)"
rsync -a --exclude 'last_run.log' --exclude 'run.lock' --exclude '*.tmp' "$ROOT/data/" "$PI:gem-bot/data/"

echo "==> Install (asks for the Pi's sudo password if it has one)"
ssh -t "$PI" 'cd ~/gem-bot && sudo bash deploy/install-pi.sh'

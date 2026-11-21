#!/usr/bin/env bash
# From the Mac: put Gem-bot on the Pi (code from GitHub, secrets + state from this Mac), then install.
#
#   bash deploy/push-to-pi.sh                # Pi at pi@reach.local
#   PI=pi@100.x.y.z bash deploy/push-to-pi.sh   # e.g. its Tailscale address when away from home
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO=https://github.com/kishankkeshri/gem-bot.git
PI_IP="${PI_IP:-192.168.29.19}"   # the Pi's home-LAN address (reach.local stopped resolving on the Mac, 21 Nov 2026)
SSH_OPTS=(-o ConnectTimeout=8)

# The Pi runs whatever is on GitHub, so deploying local edits that aren't pushed would silently not ship them.
cd "$ROOT"
if [[ -n "$(git status --porcelain --untracked-files=no)" ]] || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
  echo "Uncommitted changes - commit and push first, so the Pi gets exactly what is in git:" >&2
  git status --short >&2; exit 1
fi
git fetch -q origin
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then
  echo "Local main and origin/main differ - push (or pull) first:  git push origin main" >&2; exit 1
fi

if [[ -z "${PI:-}" ]]; then
  if ssh "${SSH_OPTS[@]}" -o BatchMode=yes pi@reach.local true 2>/dev/null; then
    PI=pi@reach.local
  else  # same machine, same host keys: verify against the keys saved for reach.local
    PI="pi@$PI_IP"; SSH_OPTS+=(-o HostKeyAlias=reach.local)
    echo "==> reach.local not reachable - using $PI"
  fi
fi
ssh() { command ssh "${SSH_OPTS[@]}" "$@"; }
scp() { command scp "${SSH_OPTS[@]}" "$@"; }

echo "==> Code (public repo, no key needed)"
ssh "$PI" "test -d ~/gem-bot/.git || git clone -q $REPO ~/gem-bot; cd ~/gem-bot && git pull -q --ff-only && git log --oneline -1"

echo "==> Secrets and firm details (never in git)"
ssh "$PI" 'mkdir -p ~/gem-bot/config ~/gem-bot/data'
scp -q "$ROOT/.env" "$PI:gem-bot/.env"
scp -q "$ROOT/config/entities.yaml" "$PI:gem-bot/config/entities.yaml"
ssh "$PI" 'chmod 600 ~/gem-bot/.env ~/gem-bot/config/entities.yaml'

if ssh "$PI" 'test -f ~/gem-bot/data/seen.json'; then
  echo "==> State: the Pi already has its own (it is the live copy) - not overwriting"
else
  echo "==> State (first install): seen bids, card buttons, cached PDFs + AI answers"
  rsync -a -e "ssh ${SSH_OPTS[*]}" --exclude 'last_run.log' --exclude 'run.lock' --exclude '*.tmp' "$ROOT/data/" "$PI:gem-bot/data/"
fi

echo "==> Install (asks for the Pi's sudo password if it has one)"
ssh -t "$PI" 'cd ~/gem-bot && sudo bash deploy/install-pi.sh'

echo "==> Pi is now on: $(ssh "$PI" 'cd ~/gem-bot && git log --oneline -1')   (Mac: $(git log --oneline -1))"

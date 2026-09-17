#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
for unit in deploy/systemd/*.service; do
  sed "s|__ROOT__|${root}|g" "$unit" > "${tmp}/$(basename "$unit")"
done
sudo install -m 0644 "$tmp"/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable tbot-sensors tbot-base tbot-guard tbot-navigation tbot-gateway tbot-dashboard
echo 'Services installed. Enter measured dimensions in Calibration, export TBOT_WHEEL_SEPARATION_M for tbot-base, then run scripts/tbot-pi-launch.sh.'

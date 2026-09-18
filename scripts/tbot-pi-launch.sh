#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
if ! command -v tailscale >/dev/null; then
  echo 'Tailscale is not installed. Install it once, then run tailscale up.' >&2
  exit 1
fi
if [[ "$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState",""))')" != Running ]]; then
  echo 'Run sudo tailscale up once and sign in before launching T-bot.' >&2
  exit 1
fi
sudo systemctl start tbot-sensors tbot-base tbot-guard tbot-navigation tbot-gateway tbot-dashboard
name="$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
sudo tailscale serve --bg --https=443 --set-path=/ http://127.0.0.1:8787
# Serve removes the /api mount prefix before forwarding to the gateway.
sudo tailscale serve --bg --https=443 --set-path=/api http://127.0.0.1:8001
link="https://${name}"
# Open this HTTPS origin before pairing: the gateway uses the request Origin
# to create the tablet URL. No global systemd environment change is needed.
echo "T-bot cockpit: ${link}/mission-control"
echo "Tablet controller: create a QR code inside ${link}/mission-control"
echo "Project: ${root}"

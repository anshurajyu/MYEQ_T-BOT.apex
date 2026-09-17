#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd "$(dirname "$0")/.." && pwd)"
tool_root="${project_root}/.tbot-tools/tailscale"
archive="${project_root}/.tbot-tools/tailscale_1.90.6_amd64.tgz"
mkdir -p "${project_root}/.tbot-tools"
if [[ ! -x "${tool_root}/tailscale" ]]; then
  curl -fsSL "https://pkgs.tailscale.com/stable/tailscale_1.90.6_amd64.tgz" -o "${archive}"
  tar -xzf "${archive}" -C "${project_root}/.tbot-tools"
  extracted="$(find "${project_root}/.tbot-tools" -maxdepth 1 -type d -name 'tailscale_*_amd64' | head -1)"
  mv "${extracted}" "${tool_root}"
fi
socket="${project_root}/.tbot-tools/tailscaled.sock"
state="${project_root}/.tbot-tools/tailscaled.state"
state_dir="${project_root}/.tbot-tools/tailscaled-data"
mkdir -p "${state_dir}"
if ! "${tool_root}/tailscale" --socket="${socket}" status >/dev/null 2>&1; then
  # A daemon crash can leave its Unix socket behind.  Treat a dead socket as
  # dead, rather than pretending the HTTPS bridge is still available.
  rm -f "${socket}"
  nohup "${tool_root}/tailscaled" --tun=userspace-networking --state="${state}" --statedir="${state_dir}" --socket="${socket}" >"${project_root}/.tbot-tools/tailscaled.log" 2>&1 < /dev/null &
  sleep 1
fi
echo "If this prints a login URL, open it and sign into the same Tailscale account on this laptop and tablet:"
"${tool_root}/tailscale" --socket="${socket}" up
# Vite proxies /api and WebSockets to the local gateway, so one HTTPS origin
# safely carries both the dashboard and tablet commands.
"${tool_root}/tailscale" --socket="${socket}" serve --bg --https=443 http://127.0.0.1:5173
name="$("${tool_root}/tailscale" --socket="${socket}" status --json | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
echo "Secure tablet URL: https://${name}/mission-control"

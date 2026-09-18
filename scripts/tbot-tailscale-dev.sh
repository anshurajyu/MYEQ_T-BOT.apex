#!/usr/bin/env bash
set -euo pipefail
# Inspect sign-in, online status and existing mounts before sharing this app.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${script_dir}/tbot-classroom.sh" secure

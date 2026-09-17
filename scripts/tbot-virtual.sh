#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export TBOT_MODE=virtual
exec .venv-tbot/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8001

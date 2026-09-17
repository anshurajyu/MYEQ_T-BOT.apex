#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
set +u
source /opt/ros/jazzy/setup.bash
set -u
exec .venv-tbot/bin/python scripts/tbot-sensors.py

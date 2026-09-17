#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
set +u
source /opt/ros/jazzy/setup.bash
set -u
exec .venv-tbot/bin/python -m backend.base_node --ros-args \
  -p command_timeout_s:=0.30

#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
set +u
source /opt/ros/jazzy/setup.bash
set -u
export TBOT_ROS=1 TBOT_MODE=hardware TBOT_USE_SIM_TIME=false
exec .venv-tbot/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8001

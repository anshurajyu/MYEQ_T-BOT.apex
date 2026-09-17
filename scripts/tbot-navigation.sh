#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
set +u
source /opt/ros/jazzy/setup.bash
set -u
exec ros2 launch backend/navigation.launch.py

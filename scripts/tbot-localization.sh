#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 ]]; then echo 'Usage: scripts/tbot-localization.sh /absolute/path/to/map.yaml'; exit 1; fi
set +u
source /opt/ros/jazzy/setup.bash
set -u
exec ros2 launch nav2_bringup localization_launch.py use_sim_time:=false map:="$1" use_composition:=false

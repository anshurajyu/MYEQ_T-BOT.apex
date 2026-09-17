#!/usr/bin/env bash
set -euo pipefail
set +u
source /opt/ros/jazzy/setup.bash
set -u
exec ros2 launch slam_toolbox online_async_launch.py use_sim_time:=false

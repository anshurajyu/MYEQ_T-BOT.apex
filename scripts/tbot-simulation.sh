#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
set +u
source /opt/ros/jazzy/setup.bash
set -u
export TURTLEBOT3_MODEL=burger
exec ros2 launch turtlebot3_gazebo turtlebot3_world.launch.py

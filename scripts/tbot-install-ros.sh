#!/usr/bin/env bash
set -euo pipefail
# Run manually in your terminal; sudo may ask for your password.
sudo apt-get update
sudo apt-get install ros-jazzy-navigation2 ros-jazzy-nav2-bringup ros-jazzy-slam-toolbox ros-jazzy-robot-localization ros-jazzy-xacro ros-jazzy-diagnostic-msgs python3-libgpiod
.venv-tbot/bin/pip install -r backend/requirements.txt feetech-servo-sdk==1.0.0

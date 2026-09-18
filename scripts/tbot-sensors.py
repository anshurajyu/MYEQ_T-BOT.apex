#!/usr/bin/env python3
"""Launch sensor and TF nodes using the dashboard's measured hardware profile."""
import os
import sys
from pathlib import Path

# The launcher runs this file directly, so Python otherwise searches scripts/
# rather than the repository root for the backend package.
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from backend.domain import HardwareProfile
from backend.storage import Store

data=Path(os.environ.get('TBOT_DATA',root/'.tbot-data'))
profile=HardwareProfile.model_validate(Store(data).get('settings','hardware',HardwareProfile().model_dump()))
missing=[name for name in ('wheel_separation_m','lidar_x_m','lidar_y_m','imu_x_m','imu_y_m') if getattr(profile,name) is None]
if missing:raise SystemExit('Measure these values in Calibration before starting sensors: '+', '.join(missing))
args=['ros2','launch',str(root/'backend'/'hardware.launch.py'),
      f'wheel_separation:={profile.wheel_separation_m}',f'lidar_x:={profile.lidar_x_m}',f'lidar_y:={profile.lidar_y_m}',
      f'imu_x:={profile.imu_x_m}',f'imu_y:={profile.imu_y_m}',f'lidar_port:={os.environ.get("TBOT_LIDAR_DEVICE","/dev/ttyUSB0")}',
      f'imu_port:={os.environ.get("TBOT_IMU_DEVICE","/dev/ttyUSB1")}']
os.chdir(root);os.execvp(args[0],args)

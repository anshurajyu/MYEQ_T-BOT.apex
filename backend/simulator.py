"""Deterministic local T-bot simulator used when robot hardware is unavailable."""
from __future__ import annotations

import heapq
import math
import threading
import time
from .planner import plan_segment


class VirtualAdapter:
    """Small differential-drive world implementing the same adapter contract as ROS."""

    width = 120
    height = 80
    resolution = 0.05
    origin = {"x": -3.0, "y": -2.0, "yaw": 0.0}

    def __init__(self):
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        with getattr(self, "lock", threading.RLock()):
            self.pose = {"x": -2.25, "y": 1.25, "yaw": 0.0, "frame": "map", "transform_age": 0.0}
            self.linear = 0.0
            self.angular = 0.0
            self.command_at = 0.0
            self.last_update = time.monotonic()
            self.nav_state = "idle"
            self.goal = None
            self.path: list[tuple[int, int]] = []
            self.battery = 96.0
            self.distance = 0.0
            self.revealed = [False] * (self.width * self.height)
            self.world = [0] * (self.width * self.height)
            self._build_world()
            self._sense()

    def _build_world(self):
        for x in range(self.width):
            self.world[x] = 100
            self.world[(self.height - 1) * self.width + x] = 100
        for y in range(self.height):
            self.world[y * self.width] = 100
            self.world[y * self.width + self.width - 1] = 100
        # Workbench, centre table, storage rack, and two narrow divider walls.
        for x0, y0, x1, y1 in [(16, 9, 41, 21), (48, 42, 68, 61), (88, 9, 105, 31), (33, 29, 38, 50), (79, 36, 84, 70)]:
            for y in range(y0, y1):
                for x in range(x0, x1):
                    self.world[y * self.width + x] = 100

    def _cell(self, x: float, y: float):
        return int((x - self.origin["x"]) / self.resolution), int((y - self.origin["y"]) / self.resolution)

    def _world_point(self, cell: tuple[int, int]):
        return (
            self.origin["x"] + (cell[0] + 0.5) * self.resolution,
            self.origin["y"] + (cell[1] + 0.5) * self.resolution,
        )

    def _free(self, x: float, y: float, radius=0.13):
        cx, cy = self._cell(x, y)
        r = max(1, math.ceil(radius / self.resolution))
        if cx < r or cy < r or cx >= self.width - r or cy >= self.height - r:
            return False
        return all(self.world[j * self.width + i] == 0 for j in range(cy - r, cy + r + 1) for i in range(cx - r, cx + r + 1))

    def _sense(self):
        points = []
        px, py = self.pose["x"], self.pose["y"]
        for ray in range(144):
            angle = self.pose["yaw"] + ray * 2 * math.pi / 144
            hit = None
            for step in range(1, 61):
                distance = step * self.resolution
                x, y = px + math.cos(angle) * distance, py + math.sin(angle) * distance
                cx, cy = self._cell(x, y)
                if not (0 <= cx < self.width and 0 <= cy < self.height):
                    break
                self.revealed[cy * self.width + cx] = True
                if self.world[cy * self.width + cx] > 50:
                    hit = [x, y]
                    break
            if hit:
                points.append(hit)
        cx, cy = self._cell(px, py)
        for y in range(max(0, cy - 4), min(self.height, cy + 5)):
            for x in range(max(0, cx - 4), min(self.width, cx + 5)):
                self.revealed[y * self.width + x] = True
        now = time.monotonic()
        self.scan = {"points": points, "frame": "map", "received": now, "error": None}
        self.grid = {
            "width": self.width,
            "height": self.height,
            "resolution": self.resolution,
            "origin": self.origin,
            "data": [self.world[i] if visible else -1 for i, visible in enumerate(self.revealed)],
            "frame": "map",
            "stamp": round(now, 2),
            "received": now,
        }

    def _plan(self, target):
        result,_=plan_segment(self.grid,self.pose,target,.18)
        return [self._cell(p['x'],p['y']) for p in result['poses'][1:]] if result else []

    def _update(self):
        now = time.monotonic()
        dt = min(0.1, max(0.0, now - self.last_update))
        self.last_update = now
        if self.goal and self.nav_state == "active":
            if not self.path:
                self.pose["yaw"] = self.goal.get("yaw", self.pose["yaw"])
                self.nav_state = "succeeded"
                self.goal = None
                self.linear = self.angular = 0.0
            else:
                wx, wy = self._world_point(self.path[0])
                dx, dy = wx - self.pose["x"], wy - self.pose["y"]
                distance = math.hypot(dx, dy)
                if distance < 0.055:
                    self.path.pop(0)
                else:
                    desired = math.atan2(dy, dx)
                    error = math.atan2(math.sin(desired - self.pose["yaw"]), math.cos(desired - self.pose["yaw"]))
                    self.angular = max(-0.8, min(0.8, error * 3))
                    self.linear = 0.20 if abs(error) < 0.5 else 0.04
        elif now - self.command_at > 0.3:
            self.linear = self.angular = 0.0
        old_x, old_y = self.pose["x"], self.pose["y"]
        yaw = self.pose["yaw"] + self.angular * dt
        nx = old_x + math.cos(yaw) * self.linear * dt
        ny = old_y + math.sin(yaw) * self.linear * dt
        if self._free(nx, ny):
            self.pose.update(x=nx, y=ny, yaw=math.atan2(math.sin(yaw), math.cos(yaw)))
            moved = math.hypot(nx - old_x, ny - old_y)
            self.distance += moved
            self.battery = max(5.0, self.battery - moved * 0.045)
        else:
            self.linear = 0.0
            if self.goal:
                self.nav_state = "failed"
                self.goal = None
                self.path = []
        self._sense()

    def drive(self, linear, angular):
        with self.lock:
            self.goal = None
            self.path = []
            self.nav_state = "idle"
            self.linear, self.angular = float(linear), float(angular)
            self.command_at = time.monotonic()

    def stop(self):
        with self.lock:
            self.linear = self.angular = 0.0
            self.command_at = 0.0
            self.goal = None
            self.path = []
            self.nav_state = "idle"

    def navigate(self, target):
        with self.lock:
            path = self._plan(target)
            if not path:
                self.nav_state = "failed"
                return
            self.goal = target
            self.path = path
            self.nav_state = "active"

    def load_map(self, _path):
        raise ValueError("Virtual Lab uses its own deterministic test map")

    def snapshot(self):
        with self.lock:
            self._update()
            now = time.monotonic()
            pose = dict(self.pose)
            pose["transform_age"] = 0.0
            return {
                "mode": "virtual-lab",
                "scenario": "Workshop Alpha",
                "pose": pose,
                "odom": {"x": pose["x"], "y": pose["y"], "yaw": pose["yaw"], "linear": self.linear, "angular": self.angular, "received": now},
                "scan": self.scan,
                "map": self.grid,
                "battery": {"voltage": 11.8, "percent": self.battery},
                "topics": {
                    "scan": {"name": "/virtual/scan", "rate": "10 Hz"},
                    "map": {"name": "/virtual/map", "rate": "5 Hz"},
                    "odom": {"name": "/virtual/odom", "rate": "20 Hz"},
                    "velocity": {"name": "/virtual/cmd_vel", "rate": "guarded"},
                },
                "nav": self.nav_state,
                "scan_age": 0.0,
                "odom_age": 0.0,
                "guard_ready": True,
                "navigation_ready": True,
                "physical_ready": True,
                "distance_total": self.distance,
                "coverage": round(sum(self.revealed) / len(self.revealed) * 100, 1),
                "diagnostics": {
                    "lidar": {"state":"ready","model":"Virtual YDLIDAR X2","rate_hz":10,"points":len(self.scan["points"])},
                    "imu": {"state":"ready","model":"Virtual BNO055","calibration":3},
                    "motor_bus": {"state":"ready","device":"virtual","baud":1000000},
                    "right_motor": {"id":1,"temperature_c":31.2,"load_percent":8,"voltage":11.8},
                    "left_motor": {"id":2,"temperature_c":30.8,"load_percent":7,"voltage":11.8},
                    "tf": {"state":"ready","age":0},
                    "localization": {"state":"ready"},
                    "nav2": {"state":"ready"}
                },
            }

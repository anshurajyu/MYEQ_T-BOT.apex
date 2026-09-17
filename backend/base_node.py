#!/usr/bin/env python3
"""ROS 2 base driver for the custom T-bot ST3215-HS differential drive."""
import json
import math
import threading
import time
import os
from pathlib import Path

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from geometry_msgs.msg import Quaternion, TransformStamped, Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from tf2_ros import TransformBroadcaster
from .motor_math import raw_speed, unwrap_delta, wheel_speeds
from .domain import HardwareProfile
from .storage import Store


def quaternion(yaw: float) -> Quaternion:
    return Quaternion(z=math.sin(yaw / 2), w=math.cos(yaw / 2))


class ServoBus:
    """Thin adapter around Waveshare's SCServo SDK, kept isolated for testing."""
    def __init__(self, device: str, baud: int):
        from scservo_sdk import PortHandler, PacketHandler, SCS_TOSCS, SCS_TOHOST, COMM_SUCCESS
        self.port = PortHandler(device)
        if not self.port.openPort() or not self.port.setBaudRate(baud):
            raise OSError(f"Cannot open ST3215 bus {device} at {baud} baud")
        self.packet = PacketHandler(0); self.encode = SCS_TOSCS; self.decode = SCS_TOHOST; self.success = COMM_SUCCESS

    def checked(self, result, error, operation):
        if result != self.success or error != 0:
            raise OSError(f"{operation} failed: {result}/{error}")

    def wheel_mode(self, servo_id: int):
        # ST3215 operation mode register: 1 = constant-speed wheel mode.
        result, error = self.packet.write1ByteTxRx(self.port, servo_id, 33, 1)
        self.checked(result, error, f"WheelMode motor {servo_id}")

    def speed(self, servo_id: int, raw: int):
        result, error = self.packet.write1ByteTxRx(self.port, servo_id, 41, 20)
        self.checked(result, error, f"Acceleration write motor {servo_id}")
        result, error = self.packet.write2ByteTxRx(self.port, servo_id, 46, self.encode(int(raw), 15))
        self.checked(result, error, f"Speed write motor {servo_id}")

    def read(self, servo_id: int) -> dict:
        position, result, error = self.packet.read2ByteTxRx(self.port, servo_id, 56)
        self.checked(result, error, f"Position read motor {servo_id}")
        speed, result, error = self.packet.read2ByteTxRx(self.port, servo_id, 58)
        self.checked(result, error, f"Speed read motor {servo_id}")
        def scalar(address, size=1):
            value, comm, fault = (self.packet.read1ByteTxRx if size == 1 else self.packet.read2ByteTxRx)(self.port, servo_id, address)
            return value if comm == self.success and fault == 0 else None
        load=scalar(60,2); voltage=scalar(62); temperature=scalar(63)
        return {
            "position": position,
            "speed": self.decode(speed,15),
            "load": self.decode(load,10) / 10 if load is not None else None,
            "voltage": voltage / 10 if voltage is not None else None,
            "temperature": temperature,
        }

    def close(self):
        self.port.closePort()


class TBotBase(Node):
    def __init__(self, bus_factory=ServoBus):
        super().__init__("tbot_st3215_base")
        data_root = Path(os.environ.get("TBOT_DATA", str(Path(__file__).resolve().parents[1] / ".tbot-data")))
        saved = HardwareProfile.model_validate(Store(data_root).get("settings", "hardware", HardwareProfile().model_dump()))
        p = self.declare_parameter
        self.circ = float(p("wheel_circumference_m", saved.wheel_circumference_m).value)
        self.separation = float(p("wheel_separation_m", saved.wheel_separation_m or 0.0).value)
        self.left_id, self.right_id = int(p("left_motor_id", saved.left_motor_id).value), int(p("right_motor_id", saved.right_motor_id).value)
        self.left_sign, self.right_sign = int(p("left_sign", saved.left_sign).value), int(p("right_sign", saved.right_sign).value)
        self.raw_limit = int(p("raw_speed_limit", saved.raw_speed_limit).value)
        self.max_linear = float(p("max_linear_mps", saved.max_linear_mps).value)
        self.encoder_scale = float(p("encoder_scale", saved.encoder_scale or 1.0).value)
        self.timeout = float(p("command_timeout_s", .30).value)
        device, baud = str(p("serial_device", saved.serial_device).value), int(p("baud", 1_000_000).value)
        if self.separation <= 0:
            raise ValueError("wheel_separation_m must be measured before the base can start")
        self.bus = bus_factory(device, baud)
        self.bus.wheel_mode(self.left_id); self.bus.wheel_mode(self.right_id)
        self.lock, self.command_at = threading.RLock(), 0.0
        self.command = (0.0, 0.0); self.last_positions = None
        self.x = self.y = self.heading = 0.0; self.last_read = time.monotonic(); self.fault = ""
        self.cmd_sub = self.create_subscription(Twist, "/cmd_vel", self.on_cmd, 20)
        self.odom_pub = self.create_publisher(Odometry, "/wheel/odom", 20)
        self.joint_pub = self.create_publisher(JointState, "/joint_states", 20)
        self.diag_pub = self.create_publisher(DiagnosticArray, "/diagnostics", 10)
        self.motor_pub = self.create_publisher(String, "/tbot/motor_status", 10)
        self.tf = TransformBroadcaster(self)
        self.create_timer(.05, self.tick)

    def on_cmd(self, msg: Twist):
        with self.lock:
            self.command = (float(msg.linear.x), float(msg.angular.z)); self.command_at = time.monotonic()

    def raw(self, mps: float, sign: int) -> int:
        return raw_speed(mps, sign, self.max_linear, self.raw_limit)

    def stop(self):
        try:
            self.bus.speed(self.left_id, 0); self.bus.speed(self.right_id, 0)
        except Exception as exc:
            self.fault = str(exc)

    def tick(self):
        now = time.monotonic()
        with self.lock:
            linear, angular = self.command if now - self.command_at <= self.timeout else (0.0, 0.0)
        left, right = wheel_speeds(linear, angular, self.separation)
        try:
            self.bus.speed(self.left_id, self.raw(left, self.left_sign))
            self.bus.speed(self.right_id, self.raw(right, self.right_sign))
            lt, rt = self.bus.read(self.left_id), self.bus.read(self.right_id)
            self.fault = ""; self.publish_state(lt, rt, now)
        except Exception as exc:
            self.fault = str(exc); self.stop(); self.publish_diagnostics(None, None)

    def publish_state(self, left: dict, right: dict, now: float):
        positions = (int(left["position"]), int(right["position"]))
        linear = angular = 0.0
        if self.last_positions is not None:
            dl = unwrap_delta(positions[0], self.last_positions[0]) / 4096 * self.circ * self.encoder_scale * self.left_sign
            dr = unwrap_delta(positions[1], self.last_positions[1]) / 4096 * self.circ * self.encoder_scale * self.right_sign
            dt = max(.001, now - self.last_read); ds = (dl + dr) / 2; dtheta = (dr - dl) / self.separation
            self.heading += dtheta; self.x += ds * math.cos(self.heading - dtheta / 2); self.y += ds * math.sin(self.heading - dtheta / 2)
            linear, angular = ds / dt, dtheta / dt
        self.last_positions, self.last_read = positions, now
        stamp = self.get_clock().now().to_msg(); q = quaternion(self.heading)
        odom = Odometry(); odom.header.stamp = stamp; odom.header.frame_id = "odom"; odom.child_frame_id = "base_footprint"
        odom.pose.pose.position.x, odom.pose.pose.position.y, odom.pose.pose.orientation = self.x, self.y, q
        odom.twist.twist.linear.x, odom.twist.twist.angular.z = linear, angular; self.odom_pub.publish(odom)
        tf = TransformStamped(); tf.header.stamp = stamp; tf.header.frame_id = "odom"; tf.child_frame_id = "base_footprint"
        tf.transform.translation.x, tf.transform.translation.y, tf.transform.rotation = self.x, self.y, q; self.tf.sendTransform(tf)
        joint = JointState(); joint.header.stamp = stamp; joint.name = ["left_wheel_joint", "right_wheel_joint"]
        joint.position = [positions[0] / 4096 * 2 * math.pi * self.left_sign, positions[1] / 4096 * 2 * math.pi * self.right_sign]
        joint.velocity = [left["speed"] * self.left_sign, right["speed"] * self.right_sign]; self.joint_pub.publish(joint)
        self.publish_diagnostics(left, right)

    def publish_diagnostics(self, left, right):
        stamp = self.get_clock().now().to_msg(); array = DiagnosticArray(); array.header.stamp = stamp
        status = DiagnosticStatus(name="T-Bot/ST3215 bus", hardware_id="waveshare-servo-adapter", level=DiagnosticStatus.ERROR if self.fault else DiagnosticStatus.OK, message=self.fault or "Motor bus healthy")
        data = {"left": left, "right": right, "fault": self.fault, "command_age": time.monotonic() - self.command_at}
        status.values = [KeyValue(key=k, value=json.dumps(v)) for k, v in data.items()]; array.status = [status]; self.diag_pub.publish(array)
        msg = String(); msg.data = json.dumps(data); self.motor_pub.publish(msg)

    def destroy_node(self):
        self.stop(); self.bus.close(); return super().destroy_node()


def main():
    rclpy.init(); node = TBotBase()
    try: rclpy.spin(node)
    finally: node.destroy_node(); rclpy.shutdown()


if __name__ == "__main__": main()

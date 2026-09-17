"""Independent final velocity publisher. Run separately from gateway.
Nav2 must publish /tbot/nav_velocity, NEVER the robot /cmd_vel directly.
"""
import math
import time
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from geometry_msgs.msg import Twist, TwistStamped
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String
from .domain import safe_velocity

class Guard(Node):
    def __init__(self):
        super().__init__('tbot_velocity_guard')
        self.declare_parameter('stamped_output',False)
        self.declare_parameter('stamped_nav',False)
        self.declare_parameter('output_topic','/cmd_vel')
        self.declare_parameter('scan_topic','/scan')
        self.status_pub=self.create_publisher(String,'/tbot/guard_status',10)
        self.mode='stop';self.heartbeat=0.;self.scan_at=0.;self.scan=None;self.commands={};self.active=False
        self.stamped=self.get_parameter('stamped_output').value
        self.pub=self.create_publisher(TwistStamped if self.stamped else Twist,self.get_parameter('output_topic').value,10)
        self.create_subscription(String,'/tbot/authority',self.authority,10)
        self.create_subscription(Twist,'/tbot/manual_velocity',lambda m:self.command('manual',m),10)
        nav_stamped=self.get_parameter('stamped_nav').value
        self.create_subscription(TwistStamped if nav_stamped else Twist,'/tbot/nav_velocity',lambda m:self.command('navigation',m.twist if nav_stamped else m),10)
        self.create_subscription(LaserScan,self.get_parameter('scan_topic').value,self.scan_cb,qos_profile_sensor_data)
        self.create_timer(.05,self.tick)
    def authority(self,m):
        if m.data in ('manual','navigation','stop'):self.mode=m.data;self.heartbeat=time.monotonic()
    def command(self,source,m):self.commands[source]=(m,time.monotonic())
    def scan_cb(self,m):self.scan=m;self.scan_at=time.monotonic()
    def tick(self):
        status=String();status.data='ready';self.status_pub.publish(status)
        now=time.monotonic();out=Twist();entry=self.commands.get(self.mode)
        if entry and self.scan:
            m=entry[0]
            out.linear.x,out.angular.z=safe_velocity(m.linear.x,m.angular.z,self.scan.ranges,self.scan.angle_min,self.scan.angle_increment,self.scan.range_min,self.scan.range_max,heartbeat_age=now-self.heartbeat,scan_age=now-self.scan_at,command_age=now-entry[1])
        if self.stamped:
            msg=TwistStamped();msg.header.stamp=self.get_clock().now().to_msg();msg.twist=out;self.pub.publish(msg)
        else:self.pub.publish(out)
def main():
    rclpy.init();node=Guard()
    try:rclpy.spin(node)
    finally:
        node.mode='stop';node.tick();node.destroy_node();rclpy.shutdown()
if __name__=='__main__':main()

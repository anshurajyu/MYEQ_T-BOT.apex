"""YDLIDAR X2, BNO055, and measured T-bot TF tree."""
from pathlib import Path
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import Command, LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    args={name:LaunchConfiguration(name) for name in ('wheel_separation','lidar_x','lidar_y','imu_x','imu_y','lidar_port','imu_port')}
    description=Command(['xacro ',str(Path(__file__).parent/'urdf'/'tbot.urdf.xacro'),' wheel_separation:=',args['wheel_separation'],' lidar_x:=',args['lidar_x'],' lidar_y:=',args['lidar_y'],' imu_x:=',args['imu_x'],' imu_y:=',args['imu_y']])
    declarations=[
        DeclareLaunchArgument('wheel_separation'),DeclareLaunchArgument('lidar_x'),DeclareLaunchArgument('lidar_y'),
        DeclareLaunchArgument('imu_x'),DeclareLaunchArgument('imu_y'),DeclareLaunchArgument('lidar_port',default_value='/dev/ttyUSB0'),
        DeclareLaunchArgument('imu_port',default_value='/dev/ttyUSB1')]
    nodes=[
        Node(package='robot_state_publisher',executable='robot_state_publisher',parameters=[{'robot_description':description,'use_sim_time':False}]),
        Node(package='ydlidar_ros2_driver',executable='ydlidar_ros2_driver_node',name='ydlidar_x2',parameters=[{
            'port':args['lidar_port'],'baudrate':115200,'frame_id':'laser','lidar_type':1,'device_type':0,
            'sample_rate':3,'frequency':7.0,'angle_min':-180.0,'angle_max':180.0,'range_min':0.10,'range_max':16.0,
            'fixed_resolution':True,'reversion':False,'inverted':False,'auto_reconnect':True,'isSingleChannel':True}]),
        Node(package='bno055',executable='bno055',name='bno055',parameters=[{'uart_port':args['imu_port'],'frame_id':'imu_link','use_sim_time':False}]),
    ]
    return LaunchDescription([*declarations,*nodes])

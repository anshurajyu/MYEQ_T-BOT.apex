"""Physical T-bot Nav2 stack with explicit velocity routing through the guard."""
from pathlib import Path
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    config=LaunchConfiguration('params_file')
    servers=[('nav2_controller','controller_server'),('nav2_planner','planner_server'),('nav2_smoother','smoother_server'),('nav2_behaviors','behavior_server'),('nav2_bt_navigator','bt_navigator'),('nav2_waypoint_follower','waypoint_follower'),('nav2_velocity_smoother','velocity_smoother')]
    nodes=[]
    for package,name in servers:
        remaps=[]
        if name in ('controller_server','behavior_server'):remaps=[('cmd_vel','/tbot/nav_raw')]
        if name=='velocity_smoother':remaps=[('cmd_vel','/tbot/nav_raw'),('cmd_vel_smoothed','/tbot/nav_velocity')]
        nodes.append(Node(package=package,executable=name,name=name,output='screen',parameters=[config,{'use_sim_time':False,'enable_stamped_cmd_vel':False}],remappings=remaps))
    nodes.append(Node(package='robot_localization',executable='ekf_node',name='ekf_filter_node',output='screen',parameters=[str(Path(__file__).parent/'config'/'ekf.yaml'),{'use_sim_time':False}],remappings=[('odometry/filtered','/odometry/filtered')]))
    nodes.append(Node(package='nav2_lifecycle_manager',executable='lifecycle_manager',name='tbot_navigation_lifecycle',parameters=[{'use_sim_time':False,'autostart':True,'node_names':[name for _,name in servers]}]))
    return LaunchDescription([DeclareLaunchArgument('params_file',default_value=str(Path(__file__).parent/'config'/'tbot_nav2.yaml')),*nodes])

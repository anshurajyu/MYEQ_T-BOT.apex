"""ROS topic choice can be tested without importing or launching ROS."""
from backend.ros_adapter import select_sensor_topic


def test_standard_base_and_ekf_odometry_are_not_ambiguous():
    assert select_sensor_topic('odom', ['/wheel/odom', '/odometry/filtered']) == '/odometry/filtered'
    assert select_sensor_topic('odom', ['/wheel/odom']) == '/wheel/odom'
    assert select_sensor_topic('odom', ['/custom/odom', '/odom']) == '/odom'


def test_configured_topic_never_silently_falls_back():
    assert select_sensor_topic('odom', ['/wheel/odom', '/odometry/filtered'], '/wheel/odom') == '/wheel/odom'
    assert select_sensor_topic('odom', ['/wheel/odom'], '/missing') is None
    assert select_sensor_topic('scan', ['/front/scan', '/back/scan']) is None

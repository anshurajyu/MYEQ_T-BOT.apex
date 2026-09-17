import math
from backend.domain import HardwareProfile, map_version
from backend.motor_math import raw_speed, unwrap_delta, wheel_speeds
from backend.planner import plan_route, world_to_cell, cell_to_world


def grid():
    width=40; height=30; data=[0]*(width*height)
    for y in range(4,26): data[y*width+20]=100
    data[15*width+20]=0
    return {'width':width,'height':height,'resolution':.1,'origin':{'x':-2,'y':-1.5,'yaw':0},'data':data}


def test_motor_polarity_conversion_and_wraparound():
    left,right=wheel_speeds(.1,.5,.2)
    assert math.isclose(left,.05) and math.isclose(right,.15)
    assert raw_speed(.06,-1,.06,500)==-500
    assert raw_speed(9,1,.06,500)==500
    assert unwrap_delta(3,4093)==6
    assert unwrap_delta(4093,3)==-6


def test_hardware_profile_requires_measured_values():
    profile=HardwareProfile()
    assert profile.wheel_circumference_m==.21038
    assert profile.right_motor_id==1 and profile.left_motor_id==2
    assert profile.right_sign==1 and profile.left_sign==-1
    assert 'wheel_separation_m' in profile.missing_calibration()


def test_world_cell_transform_roundtrip():
    value=grid(); value['origin']['yaw']=.4
    point={'x':-.7,'y':.2}
    cell=world_to_cell(value,point)
    restored=cell_to_world(value,cell)
    assert math.dist((point['x'],point['y']),(restored['x'],restored['y']))<value['resolution']


def test_inflated_planner_snaps_and_rejects_blocked_route():
    value=grid(); start={'x':-1.5,'y':0,'yaw':0}
    plan=plan_route(value,start,[{'x':1.5,'y':0,'yaw':0}],.08,.06)
    assert plan['valid'] and plan['segments'][0]['poses']
    assert plan['distance_m']>3
    assert plan['map_version']==map_version(value)
    blocked=plan_route(value,start,[{'x':1.5,'y':0,'yaw':0}],.3,.06)
    assert not blocked['valid']


def test_unknown_cells_are_blocked():
    value=grid(); value['data']=[-1]*len(value['data'])
    result=plan_route(value,{'x':-1,'y':0,'yaw':0},[{'x':1,'y':0,'yaw':0}],.1,.06)
    assert not result['valid']

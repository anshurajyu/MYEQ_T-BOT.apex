"""Validated protocol and deterministic input interpretation. No hardware imports."""
import math
import re
import hashlib
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

class Point(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra='forbid')
    x: float = Field(ge=-1000, le=1000)
    y: float = Field(ge=-1000, le=1000)
    yaw: float = Field(default=0, ge=-math.pi, le=math.pi)
    dwell: float = Field(default=0, ge=0, le=60)
    name: str = Field(default='', max_length=80)

class Mission(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)
    map_id: str = Field(min_length=1, max_length=80)
    points: list[Point] = Field(min_length=1, max_length=100)
    return_home: bool = True
    map_version: str = ''

class HardwareProfile(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra='forbid')
    wheel_circumference_m: float = Field(default=.21038, gt=.05, lt=1)
    wheel_separation_m: float | None = Field(default=None, gt=.05, lt=1)
    footprint_length_m: float | None = Field(default=None, gt=.1, lt=2)
    footprint_width_m: float | None = Field(default=None, gt=.1, lt=2)
    lidar_x_m: float | None = Field(default=None, ge=-1, le=1)
    lidar_y_m: float | None = Field(default=None, ge=-1, le=1)
    imu_x_m: float | None = Field(default=None, ge=-1, le=1)
    imu_y_m: float | None = Field(default=None, ge=-1, le=1)
    encoder_scale: float | None = Field(default=None, gt=0, lt=10)
    serial_device: str = Field(default='/dev/ttyACM0', pattern=r'^/dev/[A-Za-z0-9._/-]+$')
    right_motor_id: int = Field(default=1, ge=1, le=253)
    left_motor_id: int = Field(default=2, ge=1, le=253)
    right_sign: Literal[-1,1] = 1
    left_sign: Literal[-1,1] = -1
    raw_speed_limit: int = Field(default=500, ge=50, le=3073)
    max_linear_mps: float = Field(default=.06, gt=0, le=.22)
    max_angular_rps: float = Field(default=.4, gt=0, le=2)
    clearance_m: float = Field(default=.08, ge=.03, le=.5)

    def missing_calibration(self):
        fields=('wheel_separation_m','footprint_length_m','footprint_width_m','lidar_x_m','lidar_y_m','imu_x_m','imu_y_m','encoder_scale')
        return [name for name in fields if getattr(self,name) is None]

class PlanRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    map_id: str = Field(min_length=1,max_length=80)
    points: list[Point] = Field(min_length=1,max_length=100)
    start: Point | None = None

class ModeRequest(BaseModel):
    mode: Literal['simulation','hardware']

class RunnerRequest(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    args: str = Field(default='', max_length=300)

class RunnerRootRequest(BaseModel):
    root: str = Field(min_length=1, max_length=300)

def map_version(grid: dict | None) -> str:
    if not grid:return ''
    raw=f"{grid.get('width')}:{grid.get('height')}:{grid.get('resolution')}:{grid.get('origin')}:{grid.get('data')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

class Command(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra='forbid')
    id: str = Field(min_length=1, max_length=80)
    action: Literal['stop','claim','release','drive','timed','home_set','home_go','mission','pause','resume','retry','skip','cancel','distance','angle','explore','map_save','map_load']
    source: Literal['keyboard','gesture','voice','mission','gamepad'] = 'keyboard'
    linear: float = Field(default=0, ge=-0.15, le=0.15)
    angular: float = Field(default=0, ge=-0.8, le=0.8)
    value: float = Field(default=0, ge=-360, le=360)
    mission: Mission | None = None
    name: str = Field(default='', max_length=80)

NUMBERS = {'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10,'fifteen':15,'twenty':20,'thirty':30,'forty':40,'fifty':50,'sixty':60,'ninety':90,'hundred':100}
def parse_voice(text: str) -> dict:
    text = re.sub(r'[^a-z0-9 .-]', '', text.lower()).strip()
    if text in ('stop','emergency stop','robot stop','cancel','cancel mission'):
        return {'action':'stop','source':'voice'}
    timed={
        'forward':(.06,0.),'go forward':(.06,0.),'move forward':(.06,0.),
        'backward':(-.045,0.),'go backward':(-.045,0.),'move backward':(-.045,0.),
        'left':(0.,.35),'turn left':(0.,.35),'right':(0.,-.35),'turn right':(0.,-.35),
    }
    if text in timed:
        linear,angular=timed[text];return {'action':'timed','value':5,'linear':linear,'angular':angular,'source':'voice'}
    simple={'go home':'home_go','return home':'home_go','set home':'home_set','pause':'pause','pause mission':'pause','resume mission':'resume','start exploration':'explore'}
    if text in simple:return {'action':simple[text],'source':'voice'}
    for word,number in NUMBERS.items():text=re.sub(r'\b'+word+r'\b',str(number),text)
    m=re.fullmatch(r'(?:move |go )?(forward|backward) (\d+(?:\.\d+)?) (?:centimeters|centimetres|centimeter|cm)',text)
    if m:
        n=float(m[2]);
        if 1<=n<=100:return {'action':'distance','value':n*(1 if m[1]=='forward' else -1),'source':'voice'}
    m=re.fullmatch(r'turn (left|right) (\d+(?:\.\d+)?) degrees',text)
    if m and 1<=float(m[2])<=180:return {'action':'angle','value':float(m[2])*(1 if m[1]=='left' else -1),'source':'voice'}
    if text.startswith('start mission ') and len(text)>14:return {'action':'named_mission','name':text[14:],'source':'voice'}
    if text.startswith('go to ') and len(text)>6:return {'action':'destination','name':text[6:],'source':'voice'}
    raise ValueError('Command not recognized. Say an exact supported command; nothing will move.')

def frontier_goal(grid: dict, pose: dict, blocked: list[dict]) -> dict | None:
    """Nearest reachable frontier, with footprint clearance and unknown adjacency."""
    from collections import deque
    w,h=grid['width'],grid['height'];data=grid['data'];r=grid['resolution'];o=grid['origin']
    yaw=o.get('yaw',0);dx=pose['x']-o['x'];dy=pose['y']-o['y']
    sx=int((math.cos(yaw)*dx+math.sin(yaw)*dy)/r);sy=int((-math.sin(yaw)*dx+math.cos(yaw)*dy)/r)
    radius=max(1,math.ceil(.18/r))
    def clear(x,y):
        if not (radius<=x<w-radius and radius<=y<h-radius):return False
        return all(data[j*w+i]==0 for j in range(y-radius,y+radius+1) for i in range(x-radius,x+radius+1))
    if not (0<=sx<w and 0<=sy<h):return None
    q=deque([(sx,sy)]);seen={(sx,sy)}
    while q:
        x,y=q.popleft()
        lx=(x+.5)*r;ly=(y+.5)*r
        p={'x':o['x']+math.cos(yaw)*lx-math.sin(yaw)*ly,'y':o['y']+math.sin(yaw)*lx+math.cos(yaw)*ly,'yaw':0.,'dwell':0.}
        # Detect unknown beyond the clearance buffer, rather than inside footprint.
        frontier=any(0<=x+dx<w and 0<=y+dy<h and data[(y+dy)*w+x+dx]==-1 for dx,dy in [(radius+1,0),(-radius-1,0),(0,radius+1),(0,-radius-1)])
        if clear(x,y) and frontier and math.hypot(p['x']-pose['x'],p['y']-pose['y'])>.4 and all(math.hypot(p['x']-b['x'],p['y']-b['y'])>.5 for b in blocked):return p
        for nx,ny in [(x+1,y),(x-1,y),(x,y+1),(x,y-1)]:
            if (nx,ny) not in seen and clear(nx,ny):seen.add((nx,ny));q.append((nx,ny))
    return None

def safe_velocity(v,w,ranges,angle_min,angle_increment,range_min,range_max,*,heartbeat_age,scan_age,command_age):
    if heartbeat_age>=.5 or scan_age>=.5 or command_age>=.25 or not math.isfinite(v) or not math.isfinite(w):return 0.,0.
    v=max(-.15,min(.15,v));w=max(-.8,min(.8,w))
    for i,r in enumerate(ranges):
        if not math.isfinite(r) or not range_min<=r<=range_max:continue
        a=angle_min+i*angle_increment;along=math.cos(a)*(1 if v>=0 else -1)
        if (abs(w)>.01 and r<.2) or (abs(v)>.001 and along>.6 and r<.28):return 0.,0.
    return v,w

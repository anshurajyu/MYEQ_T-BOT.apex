import time
import pytest
from backend.controller import Controller
from backend.domain import Command, parse_voice, frontier_goal
from backend.storage import Store

class Robot:
    def __init__(self):
        self.stops=0;self.moves=[];self.goals=[]
        self.data={'scan_age':0.,'odom_age':0.,'pose':{'x':0.,'y':0.,'yaw':0.,'frame':'map','transform_age':0},'odom':{'x':0.,'y':0.,'yaw':0.},'nav':'idle','mode':'test'}
    def snapshot(self):return self.data
    def stop(self):self.stops+=1;self.data['nav']='idle'
    def drive(self,v,w):self.moves.append((v,w))
    def navigate(self,p):self.goals.append(p);self.data['nav']='active'
@pytest.fixture
def setup(tmp_path):
    r=Robot();s=Store(tmp_path);c=Controller(r,s)
    execute=c.execute;sequences={}
    def fresh_command(command,session):
        proof=c.issue_permit(session);proof.pop('expires_in_ms')
        sequences[session]=sequences.get(session,0)+1
        return execute(command.model_copy(update={**proof,'seq':sequences[session]}),session)
    c.execute=fresh_command
    c.execute(Command(id='claim',action='claim'), 'one');return c,r,s
def cmd(action,**kwargs):
    if action in ('drive','timed'):kwargs={'linear':0.,'angular':0.,**kwargs}
    return Command(id=action,action=action,**kwargs)
def test_lease_expiry_cancels(setup):
    c,r,s=setup;c.execute(cmd('drive',linear=.1),'one');c.last_heartbeat=time.monotonic()-2;c.tick();assert c.state=='idle' and c.owner is None and r.stops>=2
def test_sources_cannot_compete(setup):
    c,r,s=setup
    with pytest.raises(ValueError):c.execute(cmd('drive',source='gesture',linear=.1),'one')
    with pytest.raises(ValueError):c.execute(cmd('claim'),'two')
def test_stale_scan_blocks_motion(setup):
    c,r,s=setup;r.data['scan_age']=2
    with pytest.raises(ValueError):c.execute(cmd('drive',linear=.1),'one')
    assert not r.moves
def test_home_mission_sequence_and_persistence(setup):
    c,r,s=setup;c.execute(cmd('home_set'),'one')
    mission={'name':'route','map_id':'unsaved-map','points':[{'x':1,'y':0}],'return_home':True}
    c.execute(cmd('mission',mission=mission),'one');assert r.goals[0]['x']==1
    r.data['nav']='succeeded';c.tick();assert r.goals[-1]['x']==0
    r.data['nav']='succeeded';c.tick();assert c.state=='idle' and s.list('runs')[0]['status']=='Mission complete'
    assert Store(s.root).get('homes','unsaved-map')['x']==0
def test_distance_uses_odometry(setup):
    c,r,s=setup;c.execute(cmd('distance',value=20),'one');c.tick();assert r.moves[-1]==(.06,0.)
    r.data['odom']={'x':.2,'y':0.,'yaw':0.};c.tick();assert c.state=='idle' and 'complete' in c.status
@pytest.mark.parametrize('text',['do not move forward','forward or backward','move forward 900 cm','turn around maybe','stop and go forward'])
def test_ambiguous_voice_rejected(text):
    with pytest.raises(ValueError):parse_voice(text)
def test_voice_exact():
    assert parse_voice('move forward twenty centimeters')['value']==20
    assert parse_voice('stop')['action']=='stop'
    assert parse_voice('turn right ninety degrees')['value']==-90
def test_failed_waypoint_pauses(setup):
    c,r,s=setup;c.execute(cmd('mission',mission={'name':'route','map_id':'unsaved-map','points':[{'x':1,'y':0}],'return_home':False}),'one')
    for attempt in range(3):
        r.data['nav']='failed';c.tick()
        if attempt<2:
            assert c.state=='replanning';c.replan_deadline=0;c.tick()
    assert c.state=='blocked' and 'Retry' in c.status
    c.execute(cmd('stop'),'one');assert c.queue==[]
def test_velocity_input_limits():
    with pytest.raises(ValueError):cmd('drive',linear=100)
    with pytest.raises(ValueError):cmd('drive',linear=float('nan'))
def test_unreachable_frontier():
    grid={'width':12,'height':12,'resolution':.1,'origin':{'x':0,'y':0},'data':[100]*144}
    assert frontier_goal(grid,{'x':.5,'y':.5},[]) is None

def test_guard_stale_nan_limits_and_obstacles():
    from backend.domain import safe_velocity
    def guard(v=.1,w=0.,ranges=(2.,),heartbeat_age=0.,scan_age=0.,command_age=0.,angle_min=0.):
        return safe_velocity(v,w,ranges,angle_min,0.,.05,10.,heartbeat_age=heartbeat_age,scan_age=scan_age,command_age=command_age)
    assert guard(heartbeat_age=.6)==(0.,0.)
    assert guard(scan_age=.6)==(0.,0.)
    assert guard(command_age=.3)==(0.,0.)
    assert guard(v=float('nan'))==(0.,0.)
    assert guard(v=1,w=2)==(.15,.8)
    assert guard(ranges=(.15,))==(0.,0.)
    assert guard(v=-.1,ranges=(.15,),angle_min=3.141592653589793)==(0.,0.)
    assert guard(w=.4,ranges=(.15,),angle_min=1.57)==(0.,0.)

def test_map_export_orientation(tmp_path):
    import json
    s=Store(tmp_path);path=s.export_map('Lab',{'width':2,'height':2,'resolution':.05,'origin':{'x':1,'y':2,'yaw':.4},'data':[0,100,-1,0]})
    from pathlib import Path
    assert json.loads(Path(path).read_text())['origin']==[1,2,.4]
    assert (Path(path).parent/'map.pgm').read_bytes().endswith(bytes([205,254,254,0]))

import math
import time
import uuid
from .domain import frontier_goal, map_version

class Controller:
    def __init__(self,adapter,store):
        self.adapter=adapter;self.store=store;self.physical_readiness=None;self.owner=None;self.source=None;self.last_heartbeat=0.;self.last_drive=0.;self.state='idle';self.status='Robot disconnected';self.map_id=store.get('settings','map',{}).get('name','unsaved-map');self.queue=[];self.index=0;self.dwell_until=0.;self.test=None;self.timed_until=0.;self.timed_motion=(0.,0.);self.blacklist=[];self.exploring=False;self.run_id=None;self.run_started=0.;self.pending_map=None;self.replans=0;self.replan_deadline=0.
    def log(self,kind,message):self.store.log({'kind':kind,'message':message})
    def stop(self,reason='Stopped'):
        self.adapter.stop();self.state='idle';self.status=reason;self.queue=[];self.test=None;self.timed_until=0.;self.timed_motion=(0.,0.);self.exploring=False;self.source=None;self.replans=0;self.replan_deadline=0.
        if self.run_id:
            self.store.put('runs',self.run_id,{'status':reason,'map_id':self.map_id,'started':self.run_started,'finished':time.time(),'completed_checkpoints':self.index});self.run_id=None
        self.log('stop',reason)
    def heartbeat(self,session):
        if self.owner==session:self.last_heartbeat=time.monotonic()
    def require_sensors(self,navigation=False):
        s=self.adapter.snapshot()
        if s.get('guard_ready') is False:raise ValueError('Velocity guard is not running')
        if s.get('scan_age') is None or s['scan_age']>.75:raise ValueError('Fresh LiDAR required')
        if s.get('odom_age') is None or s['odom_age']>.5:raise ValueError('Fresh odometry required')
        if navigation and (not s.get('pose') or s['pose']['frame']!='map' or abs(s['pose'].get('transform_age',99))>1):raise ValueError('Fresh map localization required')
        return s
    def execute(self,c,session):
        if c.action=='stop':self.stop();return
        if c.action=='claim':
            if self.owner not in (None,session) and time.monotonic()-self.last_heartbeat<1:raise ValueError('Another operator owns control')
            self.stop('Control selected');self.owner=session;self.source=c.source;self.last_heartbeat=time.monotonic();return
        if c.action=='release':
            if self.owner==session:self.stop('Control released');self.owner=None
            return
        if self.owner!=session:raise ValueError('Take control first')
        if c.action=='map_load':
            if self.state!='idle':raise ValueError('Stop before loading a map')
            value=self.store.get('maps',c.name)
            if not value:raise ValueError('Saved map not found')
            self.adapter.stop();self.adapter.load_map(self.store.export_map(c.name,value['grid']));self.pending_map=c.name;self.state='loading';self.status='Loading saved map — relocalize before driving';return
        s=self.require_sensors(c.action in ('mission','home_set','home_go','explore','resume','map_save'))
        if c.action=='drive':
            if self.state not in ('idle','manual'):raise ValueError('Stop the active task before taking over')
            if self.source!=c.source:raise ValueError('Select this input source first')
            self.adapter.drive(c.linear,c.angular);self.last_drive=time.monotonic();self.state='manual';self.status='Manual driving';return
        if c.action in ('home_set','map_save') and self.state!='idle':raise ValueError('Stop before changing Home or saving a map')
        if c.action=='home_set':
            self.store.put('homes',self.map_id,{'map_id':self.map_id,**s['pose']});self.status='Home saved';return
        if c.action=='map_save':
            if not c.name.strip() or not s.get('map'):raise ValueError('Map and name required')
            self.map_id=c.name.strip();self.store.export_map(self.map_id,s['map']);self.store.put('maps',self.map_id,{'name':self.map_id,'grid':s['map']});self.store.put('settings','map',{'name':self.map_id});self.status='Map saved locally';return
        if c.action=='pause':
            if self.state not in ('navigating','dwell'):raise ValueError('No mission to pause')
            self.adapter.stop();self.state='paused';self.status='Mission paused';return
        if c.action=='resume':
            if self.state not in ('paused','blocked'):raise ValueError('No paused mission')
            self.replans=0;self.next_goal();return
        if c.action=='retry':
            if self.state!='blocked':raise ValueError('No blocked checkpoint')
            self.replans=0;self.next_goal();return
        if c.action=='skip':
            if self.state!='blocked':raise ValueError('No blocked checkpoint')
            self.index+=1;self.replans=0;self.next_goal();return
        if c.action=='cancel':self.stop('Mission cancelled');return
        if self.state not in ('idle','manual'):raise ValueError('Stop the active task before starting another')
        if c.action in ('distance','angle'):
            if c.value==0 or (c.action=='distance' and abs(c.value)>100) or (c.action=='angle' and abs(c.value)>180):raise ValueError('Distance limit 100 cm; angle limit 180 degrees')
            self.adapter.stop();self.test={'kind':c.action,'target':abs(c.value)/100 if c.action=='distance' else math.radians(abs(c.value)),'sign':1 if c.value>0 else -1,'previous':s['odom'],'progress':0.,'started':time.monotonic()};self.state='test';self.status='Odometry test';return
        if c.action=='timed':
            if c.source!='voice' or not .5<=c.value<=10 or (not c.linear and not c.angular):raise ValueError('Timed voice movement must be 0.5–10 seconds')
            if self.source!=c.source:raise ValueError('Select voice control first')
            self.adapter.stop();self.timed_until=time.monotonic()+c.value;self.timed_motion=(c.linear,c.angular);self.state='timed';self.status=f'Voice motion · {c.value:g} seconds';return
        if s.get('mode')=='ros-hardware' and (not self.physical_readiness or not self.physical_readiness().get('ready')):
            raise ValueError('Physical autonomy is locked until hardware readiness and calibration pass')
        if c.action=='mission':
            if not c.mission:raise ValueError('Mission required')
            if c.mission.map_id!=self.map_id:raise ValueError('Mission belongs to a different map')
            current_version=map_version(s.get('map'))
            if c.mission.map_version and c.mission.map_version!=current_version:raise ValueError('Mission map version is stale; validate the route again')
            self.queue=[p.model_dump() for p in c.mission.points]
            if c.mission.return_home:
                home=self.store.get('homes',self.map_id)
                if not home:raise ValueError('Set Home on this map first')
                self.queue.append(home)
        elif c.action=='home_go':
            home=self.store.get('homes',self.map_id)
            if not home:raise ValueError('Set Home on this map first')
            self.queue=[home]
        elif c.action=='explore':self.queue=[];self.exploring=True;self.blacklist=[]
        else:raise ValueError('Unsupported action')
        self.adapter.stop();self.index=0;self.replans=0;self.run_id=str(uuid.uuid4());self.run_started=time.time();self.source='mission'
        self.next_goal()
    def next_goal(self):
        if self.exploring:
            s=self.require_sensors(True)
            if not s.get('map'):raise ValueError('Map unavailable')
            p=frontier_goal(s['map'],s['pose'],self.blacklist)
            if not p:self.stop('Exploration complete — no reachable frontiers');return
            self.queue=[p];self.index=0
        if self.index>=len(self.queue):self.stop('Mission complete');return
        self.adapter.navigate(self.queue[self.index]);self.state='navigating';self.status=f'Navigating to checkpoint {self.index+1}'
    def tick(self):
        if self.state=='idle':return
        if time.monotonic()-self.last_heartbeat>1:self.stop('Operator connection expired');self.owner=None;return
        if self.state=='loading':
            result=self.adapter.snapshot().get('map_load')
            if result=='succeeded':self.map_id=self.pending_map;self.store.put('settings','map',{'name':self.map_id});self.state='idle';self.status='Map loaded — set initial pose in RViz before navigation'
            elif result=='failed':self.stop('Map load failed')
            return
        try:s=self.require_sensors(self.state in ('navigating','dwell'))
        except ValueError as e:self.stop(str(e));return
        if self.state=='manual' and time.monotonic()-self.last_drive>.25:self.adapter.drive(0.,0.);self.state='idle';self.status='Ready';return
        if self.state=='timed':
            if time.monotonic()>=self.timed_until:self.stop('Voice movement complete');return
            self.adapter.drive(*self.timed_motion);return
        if self.state=='test':
            t=self.test;o=s['odom'];p=t['previous']
            t['progress']+=math.hypot(o['x']-p['x'],o['y']-p['y']) if t['kind']=='distance' else abs(math.atan2(math.sin(o['yaw']-p['yaw']),math.cos(o['yaw']-p['yaw'])))
            t['previous']=o
            if t['progress']>=t['target']-.005:self.stop(f"Test complete: {t['progress']:.3f} {'m' if t['kind']=='distance' else 'rad'}");return
            if time.monotonic()-t['started']>45:self.stop('Test timed out — check obstacle or motion');return
            self.adapter.drive(.06*t['sign'] if t['kind']=='distance' else 0.,.25*t['sign'] if t['kind']=='angle' else 0.);return
        if self.state=='navigating':
            if s['nav']=='failed':
                if self.exploring:self.blacklist.append(self.queue[self.index]);self.next_goal()
                elif self.replans<2:
                    self.replans+=1;self.replan_deadline=time.monotonic()+5;self.adapter.stop();self.state='replanning';self.status=f'Path blocked — replanning {self.replans}/2'
                else:self.adapter.stop();self.state='blocked';self.status='Checkpoint blocked — Retry, Skip, or Cancel'
            elif s['nav']=='succeeded':self.replans=0;self.dwell_until=time.monotonic()+self.queue[self.index].get('dwell',0);self.state='dwell';self.status='Checkpoint reached'
        if self.state=='replanning' and time.monotonic()>=self.replan_deadline:self.next_goal()
        if self.state=='dwell' and time.monotonic()>=self.dwell_until:self.index+=1;self.next_goal()
    def snapshot(self):
        s=self.adapter.snapshot();return {**s,'state':self.state,'status':self.status,'source':self.source,'map_id':self.map_id,'home':self.store.get('homes',self.map_id),'checkpoint':self.index,'checkpoint_count':len(self.queue),'test':self.test}

class OfflineAdapter:
    def __init__(self,error='ROS is not enabled'):self.error=error
    def stop(self):pass
    def load_map(self,path):raise ValueError(self.error)
    def snapshot(self):return {'mode':'disconnected','error':self.error,'scan_age':None,'odom_age':None,'pose':None,'map':None,'scan':None,'topics':{},'navigation_ready':False}

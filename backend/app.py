"""Loopback-only gateway. Launch through scripts/tbot-backend.sh."""
import asyncio
import io
import json
import os
from pathlib import Path
import re
import secrets
import socket
import shlex
import subprocess
import sys
import threading
import time
import uuid
import wave
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import ValidationError
from .domain import Command, Mission, HardwareProfile, PlanRequest, ModeRequest, RunnerRequest, RunnerRootRequest, map_version, parse_voice
from .planner import plan_route
from .storage import Store
from .controller import Controller, OfflineAdapter
from .simulator import VirtualAdapter

ROOT=Path(os.environ.get('TBOT_DATA',str(Path(__file__).resolve().parents[1]/'.tbot-data')))
store=Store(ROOT)
adapter=VirtualAdapter() if os.environ.get('TBOT_MODE', 'virtual') == 'virtual' else OfflineAdapter()
requested_mode='simulation' if isinstance(adapter,VirtualAdapter) else 'hardware'
if os.environ.get('TBOT_ROS')=='1':
    try:
        from .ros_adapter import RosAdapter
        adapter=RosAdapter()
    except Exception as e:adapter=OfflineAdapter(f'ROS startup failed: {e}')
control=Controller(adapter,store)
if isinstance(adapter,VirtualAdapter):
    control.map_id='virtual-workshop-alpha'
    control.status='Virtual robot ready'
ORIGINS=['http://localhost:5173','http://127.0.0.1:5173','http://localhost:3000','http://127.0.0.1:3000']
sessions={};acks={};pairings={};device_state={};voice_model=None
runner_jobs={};runner_lock=threading.RLock()
class DirectServoBridge:
    """Today's PC-attached ST3215 path; the bridge has its own 300 ms stop watchdog."""
    def __init__(self):self.process=None;self.writer=None;self.enabled=False;self.error='Direct servo control is off';self.fifo=Path(f'/tmp/tbot-servo-{os.getuid()}.fifo')
    def enable(self):
        if self.process and self.process.poll() is None:self.enabled=True;return
        if not Path('/dev/ttyACM0').exists():raise ValueError('Waveshare servo adapter not found at /dev/ttyACM0')
        desktop=Path.home()/'Desktop/MYEQ_T-BOT.apex'
        python=desktop/'.venv/bin/python' if (desktop/'.venv/bin/python').exists() else Path.home()/'tbot_servo/venv/bin/python'
        script=Path(__file__).resolve().parents[1]/'robot-code/dashboard_wasd_relay.py'
        if not python.exists():raise ValueError(f'Servo Python environment missing at {python}')
        if self.fifo.exists():self.fifo.unlink()
        os.mkfifo(self.fifo,0o600)
        self.process=subprocess.Popen(['/usr/bin/gnome-terminal','--wait','--title=T-Bot Direct Servo','--',str(python),'-u',str(script),str(self.fifo)],cwd=str(script.parent))
        for _ in range(30):
            if self.process.poll() is not None:break
            try:
                fd=os.open(self.fifo,os.O_WRONLY|os.O_NONBLOCK);self.writer=os.fdopen(fd,'w',buffering=1);break
            except OSError:time.sleep(.05)
        if self.process.poll() is not None:
            self.error='Direct servo terminal could not start';raise ValueError(self.error)
        if not self.writer:
            self.process.terminate();raise ValueError('Direct servo terminal opened but its command relay did not connect')
        self.enabled=True;self.error='Servo connected — command terminal is running'
    def send(self,linear=0.,angular=0.):
        if not self.enabled or not self.process or self.process.poll() is not None:return
        try:self.writer.write(json.dumps({'linear':linear,'angular':angular})+'\n');self.writer.flush()
        except (BrokenPipeError,OSError):self.enabled=False;self.error='Servo bridge disconnected'
    def stop(self):self.send();
    def disable(self):
        self.stop();self.enabled=False
        if self.process and self.process.poll() is None:
            try:self.writer.write('{"action":"quit"}\n');self.writer.flush();self.process.wait(timeout=1)
            except Exception:self.process.terminate()
        if self.writer:
            try:self.writer.close()
            except OSError:pass
        self.writer=None;self.process=None
        if self.fifo.exists():self.fifo.unlink()
        self.error='Direct servo control is off'
direct_servo=DirectServoBridge()

class WasdTerminal:
    """Launch the user's interactive WASD controller in a real local terminal."""
    def __init__(self):
        self.process=None
        self.error='WASD terminal is not running'
    def command(self):
        # Prefer the exact controller and virtual environment already proven on
        # this machine. Keep the dashboard workspace copy as a portable fallback.
        desktop=Path.home()/'Desktop/MYEQ_T-BOT.apex'
        workspace=Path(__file__).resolve().parents[1]
        script=desktop/'t-bot_wasd.py' if (desktop/'t-bot_wasd.py').exists() else workspace/'robot-code/t-bot_wasd.py'
        desktop_python=desktop/'.venv/bin/python'
        servo_python=Path.home()/'tbot_servo/venv/bin/python'
        python=desktop_python if desktop_python.exists() else servo_python
        return python,script
    def status(self):
        running=bool(self.process and self.process.poll() is None)
        if self.process and not running and self.error=='WASD terminal started':
            self.error=f'WASD terminal exited with code {self.process.returncode}'
        python,script=self.command()
        return {'running':running,'error':self.error,'path':str(script),'python':str(python)}
    def start(self):
        if self.process and self.process.poll() is None:return self.status()
        if not Path('/dev/ttyACM0').exists():raise ValueError('Connect the Waveshare servo adapter first: /dev/ttyACM0 is not present')
        python,script=self.command()
        terminal=Path('/usr/bin/gnome-terminal')
        if not python.exists():raise ValueError(f'Servo Python environment missing at {python}')
        if not script.exists():raise ValueError(f'WASD controller missing at {script}')
        if not terminal.exists():raise ValueError('GNOME Terminal is not installed on this computer')
        # --wait keeps this process attached to the Python program, so the
        # dashboard can report whether the terminal controller is still alive.
        self.process=subprocess.Popen([
            str(terminal),'--wait','--title=T-Bot WASD Servo Control','--',
            str(python),'-u',str(script)
        ],cwd=str(script.parent))
        time.sleep(.25)
        if self.process.poll() is not None:
            self.error=f'Terminal failed to start (exit {self.process.returncode})'
            raise ValueError(self.error)
        self.error='WASD terminal started'
        return self.status()
    def stop(self):
        if self.process and self.process.poll() is None:self.process.terminate()
        self.process=None;self.error='WASD terminal is not running'

wasd_terminal=WasdTerminal()
DEFAULT_PROFILE=HardwareProfile().model_dump()
if not store.get('settings','hardware'):store.put('settings','hardware',DEFAULT_PROFILE)

def trusted_origin(origin):
    return origin in ORIGINS or bool(origin and (re.fullmatch(r'https://[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net',origin) or re.fullmatch(r'http://(?:10|127|192\.168)\.[0-9.]+:(?:3000|5173)',origin)))
async def ticker():
    while True:
        try:
            control.tick()
            if hasattr(adapter,'authorize'):adapter.authorize('navigation' if control.state=='navigating' else 'manual' if control.state in ('manual','test','timed') else 'stop')
        except Exception as e:control.stop('Controller error: '+str(e))
        await asyncio.sleep(.05)
@asynccontextmanager
async def lifespan(app):
    task=asyncio.create_task(ticker())
    yield
    control.stop('Gateway shutdown');direct_servo.disable();wasd_terminal.stop();task.cancel()
    if hasattr(adapter,'close'):adapter.close()
app=FastAPI(title='T-bot local ROS gateway',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origin_regex=r'http://(localhost|127\.0\.0\.1|10\.[0-9.]+|192\.168\.[0-9.]+):(3000|5173)|https://[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net',allow_methods=['GET','POST'],allow_headers=['Authorization','Content-Type'])

def auth(token):
    if token not in sessions or time.monotonic()-sessions[token]['seen']>3600:raise HTTPException(401,'Session expired; reconnect')
    sessions[token]['seen']=time.monotonic();return token

def bearer(value):return auth((value or '').removeprefix('Bearer '))

def profile():return HardwareProfile.model_validate(store.get('settings','hardware',DEFAULT_PROFILE))
def runner_root():
    fallback=Path(os.environ.get('TBOT_CODE_ROOT',str(ROOT.parent))).resolve()
    saved=store.get('settings','runner',{}).get('root')
    candidate=Path(saved).expanduser().resolve() if saved else fallback
    allowed=Path.home().resolve()
    if candidate!=allowed and allowed not in candidate.parents: return fallback
    return candidate
def runner_file(relative):
    root=runner_root();target=(root/relative).resolve()
    if root not in target.parents or target.suffix!='.py' or not target.is_file():raise HTTPException(400,'Choose a Python file inside the configured VS Code workspace')
    return target
def runner_output(job):
    process=job['process']
    if process.poll() is None:return
    if job.get('finished') is None:
        job['finished']=time.time();job['returncode']=process.returncode
def start_runner(job):
    process=job['process']
    try:
        for line in iter(process.stdout.readline,''):
            with runner_lock:
                job['output']=(job['output']+line)[-24000:]
    finally:
        process.stdout.close()
        with runner_lock:runner_output(job)
def readiness():
    snap=adapter.snapshot();p=profile();virtual=snap.get('mode')=='virtual-lab';missing=[] if virtual else p.missing_calibration()
    checks={
        'lidar':snap.get('scan_age') is not None and snap.get('scan_age',99)<.75,
        'odometry':snap.get('odom_age') is not None and snap.get('odom_age',99)<.5,
        'tf':bool(snap.get('pose')) and abs((snap.get('pose') or {}).get('transform_age',99))<1,
        'navigation':bool(snap.get('navigation_ready')),
        'safety_guard':bool(snap.get('guard_ready')),
        'imu':virtual or bool((snap.get('diagnostics') or {}).get('imu',{}).get('healthy') or (snap.get('diagnostics') or {}).get('imu',{}).get('state')=='ready'),
        'motor_bus':virtual or bool((snap.get('diagnostics') or {}).get('motor_bus',{}).get('healthy') or (snap.get('diagnostics') or {}).get('motor_bus',{}).get('state')=='ready'),
        'calibration':not missing,
    }
    ready=all(checks.values());return {'ready':ready,'physical_lock':not virtual and not ready,'checks':checks,'missing_calibration':missing,'profile':p.model_dump()}
control.physical_readiness=readiness

def set_runtime_mode(mode):
    global adapter,requested_mode
    if mode==requested_mode:return
    control.stop('Changing robot mode');old=adapter
    if mode=='simulation':
        replacement=VirtualAdapter();control.map_id='virtual-workshop-alpha';control.status='Virtual robot ready'
    else:
        try:
            from .ros_adapter import RosAdapter
            replacement=RosAdapter()
        except Exception as e:replacement=OfflineAdapter(f'Real robot unavailable: {e}')
        control.status='Waiting for Raspberry Pi ROS 2'
    adapter=replacement;control.adapter=replacement;requested_mode=mode
    if hasattr(old,'close'):
        try:old.close()
        except Exception:pass

@app.get('/health')
def health():return {'service':'tbot-gateway','robot':adapter.snapshot()['mode'],'requested_mode':requested_mode,'voice_ready':(ROOT/'models/vosk-model-small-en-us-0.15').exists(),'firebase_configured':bool(store.get('settings','firebase')),'readiness':readiness()}
@app.get('/mode')
def get_mode(authorization:str=Header(default='')):bearer(authorization);return {'mode':requested_mode,'effective':adapter.snapshot().get('mode')}
@app.post('/mode')
def select_mode(value:ModeRequest,authorization:str=Header(default='')):
    token=bearer(authorization)
    if sessions[token]['role']!='cockpit':raise HTTPException(403,'Cockpit role required')
    set_runtime_mode(value.mode);return {'mode':requested_mode,'effective':adapter.snapshot().get('mode'),'readiness':readiness()}

@app.get('/runner/files')
def runner_files(authorization:str=Header(default='')):
    token=bearer(authorization)
    if sessions[token]['role']!='cockpit':raise HTTPException(403,'Cockpit role required')
    root=runner_root();ignored={'.git','.venv','.venv-tbot','node_modules','.tbot-data','dist','.next','__pycache__'};files=[]
    for path in root.rglob('*.py'):
        if any(part in ignored for part in path.relative_to(root).parts):continue
        try:
            if path.stat().st_size<=1_000_000:files.append(str(path.relative_to(root)))
        except OSError:pass
        if len(files)>=400:break
    return {'root':str(root),'files':sorted(files)}

@app.post('/runner/root')
def runner_set_root(value:RunnerRootRequest,authorization:str=Header(default='')):
    token=bearer(authorization)
    if sessions[token]['role']!='cockpit':raise HTTPException(403,'Cockpit role required')
    target=Path(value.root).expanduser().resolve();home=Path.home().resolve()
    if not target.is_dir() or (target!=home and home not in target.parents):raise HTTPException(400,'Workspace must be an existing folder inside this computer user account')
    store.put('settings','runner',{'root':str(target)});return {'root':str(target)}

@app.post('/runner/run')
def runner_run(value:RunnerRequest,authorization:str=Header(default='')):
    token=bearer(authorization)
    if sessions[token]['role']!='cockpit':raise HTTPException(403,'Cockpit role required')
    try:args=shlex.split(value.args)
    except ValueError as e:raise HTTPException(400,'Arguments are not valid: '+str(e))
    if len(args)>24:raise HTTPException(400,'Use at most 24 arguments')
    target=runner_file(value.path);python=ROOT.parent/'.venv-tbot'/'bin'/'python'
    executable=str(python if python.exists() else Path(sys.executable))
    with runner_lock:
        active=[job for job in runner_jobs.values() if job['process'].poll() is None]
        if active:raise HTTPException(409,'A Python run is already active. Stop it before launching another.')
        run_id=uuid.uuid4().hex[:12]
        process=subprocess.Popen([executable,str(target),*args],cwd=str(target.parent),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1,env={**os.environ,'PYTHONUNBUFFERED':'1'})
        job={'id':run_id,'path':str(target.relative_to(runner_root())),'args':args,'started':time.time(),'finished':None,'returncode':None,'output':'','process':process}
        runner_jobs[run_id]=job;threading.Thread(target=start_runner,args=(job,),daemon=True).start()
    return {'id':run_id,'path':job['path']}

@app.get('/runner/runs/{run_id}')
def runner_status(run_id:str,authorization:str=Header(default='')):
    bearer(authorization)
    with runner_lock:
        job=runner_jobs.get(run_id)
        if not job:raise HTTPException(404,'Run not found')
        runner_output(job);return {key:value for key,value in job.items() if key!='process'} | {'running':job['process'].poll() is None}

@app.post('/runner/runs/{run_id}/stop')
def runner_stop(run_id:str,authorization:str=Header(default='')):
    bearer(authorization)
    with runner_lock:
        job=runner_jobs.get(run_id)
        if not job:raise HTTPException(404,'Run not found')
        if job['process'].poll() is None:job['process'].terminate()
        runner_output(job)
    return {'stopped':True}
@app.post('/simulation/reset')
def simulation_reset(authorization:str=Header(default='')):
    bearer(authorization)
    if not isinstance(adapter,VirtualAdapter):raise HTTPException(409,'Virtual Lab is not active')
    control.stop('Virtual Lab reset');adapter.reset();control.map_id='virtual-workshop-alpha';store.put('settings','map',{'name':control.map_id});return {'reset':True}
@app.post('/session')
def session(request:Request,role:str=Query(default='cockpit'),pair:str=Query(default='')):
    if not trusted_origin(request.headers.get('origin')):raise HTTPException(403,'Untrusted browser origin')
    if role not in ('cockpit','tablet'):raise HTTPException(400,'Unknown device role')
    if role=='tablet':
        entry=pairings.get(pair) or store.get('pairings',pair)
        if not entry or time.time()>entry['expires_at']:raise HTTPException(403,'Pairing code is invalid or expired. Create a new Tablet link on the laptop.')
        pairings[pair]=entry
    now=time.monotonic()
    for key in list(sessions):
        if now-sessions[key]['seen']>3600:sessions.pop(key,None);acks.pop(key,None);device_state.pop(key,None)
    token=secrets.token_urlsafe(32);sessions[token]={'seen':now,'role':role,'connected':False};acks[token]={};device_state[token]={'role':role,'connected':False,'authority':False,'camera':False,'gesture':'stop','confidence':0,'x':.5,'y':.5,'fps':0,'latency_ms':0}
    # Keep a short-lived pairing code usable for reconnects. Mobile browsers
    # regularly recreate WebSockets when a camera permission dialog appears;
    # consuming it on the first connection made the tablet look "disconnected"
    # even though its camera had already started.
    return {'token':token,'role':role}

@app.post('/pairing')
def create_pairing(request:Request,authorization:str=Header(default='')):
    token=bearer(authorization)
    if sessions[token]['role']!='cockpit':raise HTTPException(403,'Cockpit role required')
    code=f'{secrets.randbelow(1_000_000):06d}'
    base=os.environ.get('TBOT_PUBLIC_URL') or request.headers.get('origin') or 'http://127.0.0.1:5173'
    if re.match(r'http://(localhost|127\.0\.0\.1)',base):
        try:
            probe=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);probe.connect(('1.1.1.1',80));host=probe.getsockname()[0];probe.close();base=f'http://{host}:5173'
        except OSError:pass
    expires_seconds=1800
    url=f'{base}/tablet?pair={code}';entry={'expires_at':time.time()+expires_seconds,'url':url};pairings[code]=entry;store.put('pairings',code,entry)
    return {'code':code,'expires_seconds':expires_seconds,'url':url,'secure':base.startswith('https://') or 'localhost' in base}

@app.get('/direct-servo')
def direct_servo_status(authorization:str=Header(default='')):
    bearer(authorization);return {'enabled':direct_servo.enabled,'error':direct_servo.error,'port':'/dev/ttyACM0'}

@app.post('/direct-servo')
def set_direct_servo(request:Request,authorization:str=Header(default='')):
    token=bearer(authorization)
    if sessions[token]['role']!='cockpit':raise HTTPException(403,'Cockpit role required')
    enabled=request.query_params.get('enabled','false').lower()=='true'
    try:
        if enabled:direct_servo.enable()
        else:direct_servo.disable()
        return {'enabled':direct_servo.enabled,'error':direct_servo.error,'port':'/dev/ttyACM0'}
    except ValueError as e:raise HTTPException(422,str(e))

@app.get('/wasd-terminal')
def wasd_terminal_status(authorization:str=Header(default='')):
    token=bearer(authorization)
    if sessions[token]['role']!='cockpit':raise HTTPException(403,'Cockpit role required')
    return wasd_terminal.status()

@app.post('/wasd-terminal')
def set_wasd_terminal(request:Request,authorization:str=Header(default='')):
    token=bearer(authorization)
    if sessions[token]['role']!='cockpit':raise HTTPException(403,'Cockpit role required')
    enabled=request.query_params.get('enabled','true').lower()=='true'
    try:return wasd_terminal.start() if enabled else (wasd_terminal.stop() or wasd_terminal.status())
    except ValueError as e:raise HTTPException(422,str(e))

@app.get('/pairing/qr.svg')
def pairing_qr(code:str,token:str):
    auth(token);entry=pairings.get(code) or store.get('pairings',code)
    if not entry or time.time()>entry['expires_at']:raise HTTPException(404,'Pairing expired')
    try:
        import qrcode
        import qrcode.image.svg
        out=io.BytesIO();qrcode.make(entry['url'],image_factory=qrcode.image.svg.SvgPathImage,box_size=7,border=2).save(out)
        return Response(out.getvalue(),media_type='image/svg+xml',headers={'Cache-Control':'no-store'})
    except ImportError:raise HTTPException(503,'QR dependency unavailable; use the displayed URL')

@app.get('/devices')
def devices(authorization:str=Header(default='')):
    token=bearer(authorization)
    if sessions[token]['role']!='cockpit':raise HTTPException(403,'Cockpit role required')
    now=time.monotonic();items=[]
    for key,state in device_state.items():
        if not sessions.get(key):continue
        age=now-sessions[key]['seen'];items.append({'id':key[:8],**state,'connected':bool(state.get('connected') and age<2.0),'authority':bool(state.get('authority') and age<2.0),'heartbeat_age':round(age,3)})
    return {'devices':items}

@app.get('/telemetry')
def telemetry(authorization:str=Header(default='')):
    bearer(authorization);return control.snapshot()

@app.post('/heartbeat')
async def http_heartbeat(authorization:str=Header(default='')):
    token=bearer(authorization);control.heartbeat(token);device_state[token]['connected']=True;device_state[token]['authority']=control.owner==token;return {'ok':True}

@app.post('/device-state')
async def http_device_state(request:Request,authorization:str=Header(default='')):
    token=bearer(authorization);payload=await request.json();state=device_state[token]
    for key in ('camera','gesture','confidence','x','y','fps','latency_ms'):
        if key in payload:state[key]=payload[key]
    state['connected']=True;state['authority']=control.owner==token
    return {'ok':True,'authority':state['authority']}

@app.post('/gesture/activate')
async def activate_gesture(authorization:str=Header(default='')):
    cockpit=bearer(authorization)
    if sessions[cockpit]['role']!='cockpit':raise HTTPException(403,'Laptop cockpit required')
    now=time.monotonic()
    candidates=[key for key,value in sessions.items() if value['role']=='tablet' and device_state.get(key,{}).get('connected') and now-value['seen']<2]
    if not candidates:raise HTTPException(409,'No live tablet is connected')
    tablet=max(candidates,key=lambda key:sessions[key]['seen'])
    control.stop('Gesture control selected');control.owner=tablet;control.source='gesture';control.last_heartbeat=now
    for key,state in device_state.items():state['authority']=key==tablet
    return {'active':True,'device':tablet[:8]}

@app.post('/command')
async def http_command(request:Request,authorization:str=Header(default='')):
    token=bearer(authorization);payload=await request.json()
    try:
        c=Command.model_validate(payload)
        if c.action=='claim' and sessions[token]['role']=='cockpit' and control.owner not in (None,token):
            control.stop('Laptop control takeover');control.owner=None
        control.execute(c,token)
        if c.action=='drive':direct_servo.send(c.linear,c.angular)
        elif c.action in ('stop','release','claim','cancel'):direct_servo.stop()
        device_state[token]['connected']=True;device_state[token]['authority']=control.owner==token
        return {'type':'ack','id':c.id,'ok':True,'action':c.action}
    except (ValueError,ValidationError) as e:raise HTTPException(409,str(e))

@app.get('/readiness')
def get_readiness(authorization:str=Header(default='')):bearer(authorization);return readiness()

@app.get('/hardware-profile')
def get_hardware_profile(authorization:str=Header(default='')):bearer(authorization);return profile().model_dump()

@app.post('/hardware-profile')
def save_hardware_profile(value:HardwareProfile,authorization:str=Header(default='')):
    bearer(authorization);store.put('settings','hardware',value.model_dump());return {'saved':True,'readiness':readiness()}

@app.post('/plan')
def make_plan(value:PlanRequest,authorization:str=Header(default='')):
    bearer(authorization);snap=control.snapshot()
    if value.map_id!=control.map_id:raise HTTPException(409,'Route belongs to a different map')
    grid=snap.get('map');start=(value.start.model_dump() if value.start else snap.get('pose'))
    if not grid or not start:raise HTTPException(409,'Map and localized pose are required')
    p=profile();radius=(max(p.footprint_length_m or .28,p.footprint_width_m or .28)/2)+p.clearance_m
    result=plan_route(grid,start,[v.model_dump() for v in value.points],radius,p.max_linear_mps)
    if result['valid'] and hasattr(adapter,'compute_route'):
        try:
            result['segments']=adapter.compute_route([segment['snapped_goal'] for segment in result['segments']])
            result['distance_m']=round(sum(segment['distance_m'] for segment in result['segments']),3)
            result['eta_seconds']=round(result['distance_m']/max(p.max_linear_mps,.01),1)
        except ValueError as e:
            result['valid']=False;result['errors']=[{'checkpoint':0,'message':str(e)}]
    return result
@app.get('/library')
def library(authorization:str=Header(default='')):
    bearer(authorization);return {kind:store.list(kind) for kind in ('missions','maps','runs','photos')} | {'settings':store.get('settings','ui',{}),'events':store.events(),'firebase':store.get('settings','firebase',{})}
@app.post('/missions')
def save_mission(mission:Mission,authorization:str=Header(default='')):
    bearer(authorization);key=str(uuid.uuid5(uuid.NAMESPACE_URL,mission.map_id+'/'+mission.name));store.put('missions',key,mission.model_dump());return {'id':key}
@app.post('/settings/{kind}')
async def settings(kind:str,request:Request,authorization:str=Header(default='')):
    bearer(authorization)
    if kind not in ('ui','firebase'):raise HTTPException(400,'Unknown settings')
    data=await request.json()
    if len(json.dumps(data))>5000:raise HTTPException(413,'Settings too large')
    if kind=='firebase':data={k:str(data.get(k,''))[:200] for k in ('apiKey','authDomain','projectId','teamId')}
    store.put('settings',kind,data);return {'saved':True}
@app.post('/photos')
async def photo(request:Request,authorization:str=Header(default='')):
    bearer(authorization);body=await request.body()
    if len(body)>8_000_000:raise HTTPException(413,'Photo limit is 8 MB')
    ext='png' if body.startswith(b'\x89PNG\r\n\x1a\n') else 'jpg' if body.startswith(b'\xff\xd8\xff') else None
    if not ext:raise HTTPException(400,'Use PNG or JPEG photos')
    name=f'{uuid.uuid4()}.{ext}';folder=ROOT/'photos';folder.mkdir(exist_ok=True);(folder/name).write_bytes(body);store.put('photos',name,{'name':name});return {'id':name}
@app.get('/photos/{name}')
def read_photo(name:str,token:str):
    auth(token)
    if not re.fullmatch(r'[0-9a-f-]+\.(png|jpg)',name):raise HTTPException(400,'Invalid photo')
    p=ROOT/'photos'/name
    if not p.exists():raise HTTPException(404,'Photo missing')
    return FileResponse(p)
@app.post('/voice/interpret')
async def interpret(request:Request,authorization:str=Header(default='')):
    bearer(authorization);data=await request.json()
    try:return {'transcript':data['text'],'command':parse_voice(data['text'])}
    except (KeyError,ValueError) as e:raise HTTPException(422,str(e))
@app.post('/voice/transcribe')
async def transcribe(request:Request,authorization:str=Header(default='')):
    bearer(authorization);body=await request.body()
    if len(body)>1_000_000:raise HTTPException(413,'Record at most 20 seconds')
    def work():
        global voice_model
        from vosk import Model, KaldiRecognizer
        model=ROOT/'models/vosk-model-small-en-us-0.15'
        if not model.exists():raise ValueError('Download the offline voice model with scripts/tbot-assets.py')
        with wave.open(io.BytesIO(body),'rb') as wav:
            if wav.getnchannels()!=1 or wav.getsampwidth()!=2 or wav.getframerate()!=16000:raise ValueError('Expected mono 16-bit 16kHz WAV')
            if wav.getnframes()>320000:raise ValueError('Recording exceeds 20 seconds')
            if voice_model is None:voice_model=Model(str(model))
            rec=KaldiRecognizer(voice_model,16000);rec.AcceptWaveform(wav.readframes(wav.getnframes()));return json.loads(rec.FinalResult()).get('text','')
    try:
        text=await asyncio.to_thread(work);return {'transcript':text,'command':parse_voice(text)}
    except Exception as e:raise HTTPException(422,str(e))
@app.websocket('/ws')
async def websocket(ws:WebSocket):
    if not trusted_origin(ws.headers.get('origin')):await ws.close(code=1008);return
    await ws.accept();token=None
    try:
        hello=await asyncio.wait_for(ws.receive_json(),5);token=auth(hello.get('token',''));sessions[token]['connected']=True;device_state[token]['connected']=True
        async def receive():
            while True:
                payload=await ws.receive_json();auth(token)
                if payload.get('type')=='heartbeat':control.heartbeat(token);continue
                if payload.get('type')=='device_state':
                    state=device_state[token]
                    for key in ('camera','gesture','confidence','x','y','fps','latency_ms'):
                        if key in payload:state[key]=payload[key]
                    state['authority']=control.owner==token
                    await ws.send_json({'type':'device_ack','at':time.time()});continue
                command_id=payload.get('id','invalid')
                if command_id in acks[token]:await ws.send_json(acks[token][command_id]);continue
                try:
                    c=Command.model_validate(payload)
                    if c.action=='claim' and sessions[token]['role']=='cockpit' and control.owner not in (None,token):
                        control.stop('Laptop control takeover');control.owner=None
                    control.execute(c,token)
                    if c.action=='drive':direct_servo.send(c.linear,c.angular)
                    elif c.action in ('stop','release','claim','cancel'):direct_servo.stop()
                    ack={'type':'ack','id':c.id,'ok':True,'action':c.action};
                    if c.action!='drive':control.log('command',f'{c.source}: {c.action}')
                except (ValueError,ValidationError) as e:ack={'type':'ack','id':command_id,'ok':False,'error':str(e)}
                acks[token][command_id]=ack
                if len(acks[token])>200:acks[token].pop(next(iter(acks[token])))
                await ws.send_json(ack)
        async def send():
            last_map=None
            while True:
                snapshot=control.snapshot();snapshot['readiness']=readiness();snapshot['requested_mode']=requested_mode;grid=snapshot.get('map');stamp=(grid.get('stamp'),grid.get('received')) if grid else None
                if grid and stamp==last_map:snapshot.pop('map',None)
                last_map=stamp
                await ws.send_json({'type':'telemetry',**snapshot});await asyncio.sleep(.2)
        tasks=[asyncio.create_task(receive()),asyncio.create_task(send())]
        try:
            done,pending=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            await asyncio.gather(*done,return_exceptions=True)
        finally:
            for t in tasks:t.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
    except (WebSocketDisconnect,RuntimeError,HTTPException,asyncio.TimeoutError,asyncio.CancelledError):pass
    finally:
        if token:
            sessions.get(token,{}).update(connected=False);device_state.get(token,{}).update(connected=False,authority=False,camera=False,gesture='stop')
            if control.owner==token:control.stop('Control socket disconnected');control.owner=None;direct_servo.stop()

@app.websocket('/voice/live')
async def live_voice(ws:WebSocket):
    """PCM16/16kHz stream for stop detection while push-to-talk is held."""
    global voice_model
    if not trusted_origin(ws.headers.get('origin')):await ws.close(code=1008);return
    await ws.accept()
    try:
        hello=await asyncio.wait_for(ws.receive_json(),5);token=auth(hello.get('token',''))
        from vosk import Model, KaldiRecognizer
        path=ROOT/'models/vosk-model-small-en-us-0.15'
        if not path.exists():await ws.send_json({'error':'Offline voice model missing'});return
        if voice_model is None:voice_model=await asyncio.to_thread(Model,str(path))
        rec=KaldiRecognizer(voice_model,16000);await ws.send_json({'ready':True});count=0;started=time.monotonic()
        while time.monotonic()-started<20:
            chunk=await asyncio.wait_for(ws.receive_bytes(),3);count+=len(chunk)
            if count>640000 or len(chunk)>32000:break
            auth(token)
            complete=await asyncio.to_thread(rec.AcceptWaveform,chunk)
            result=json.loads(rec.Result() if complete else rec.PartialResult());text=result.get('text',result.get('partial',''))
            if text.strip() in ('stop','robot stop','emergency stop','cancel mission'):
                control.stop('Voice stop');await ws.send_json({'stopped':True,'transcript':text})
    except (WebSocketDisconnect,asyncio.TimeoutError,HTTPException):pass
    finally:
        try:await ws.close()
        except RuntimeError:pass

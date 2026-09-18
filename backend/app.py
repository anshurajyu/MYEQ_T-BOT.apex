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
from .controller import Controller, OfflineAdapter, DirectUsbAdapter
from .simulator import VirtualAdapter
from .network import secure_network

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
pair_failures={}
PROTOCOL_VERSION=2
PROJECT_ROOT=Path(__file__).resolve().parents[1]
from .usb_transport import DirectServoBridge
direct_servo=DirectServoBridge()
control.motion_output=lambda linear,angular:direct_servo.send(linear,angular)

class WasdTerminal:
    """Keep the legacy diagnostic endpoint without creating a second motor owner."""
    def status(self):
        return {'running':False,'error':'Use Drive → Take keyboard control through the managed USB relay, or run the standalone controller after disconnecting the relay.','path':str(PROJECT_ROOT/'robot-code/t-bot_wasd.py'),'python':os.environ.get('TBOT_SERVO_PYTHON',sys.executable)}
    def start(self):raise ValueError(self.status()['error'])
    def stop(self):pass

wasd_terminal=WasdTerminal()
DEFAULT_PROFILE=HardwareProfile().model_dump()
if not store.get('settings','hardware'):store.put('settings','hardware',DEFAULT_PROFILE)

def trusted_origin(origin):
    return origin in ORIGINS or bool(origin and origin == secure_network()['public_url'])
async def ticker():
    while True:
        try:
            control.tick()
            if hasattr(adapter,'authorize'):adapter.authorize('navigation' if control.state=='navigating' else 'manual' if control.state in ('manual','test','timed') else 'stop')
        except Exception as e:
            try:control.stop('Controller error: '+str(e))
            except Exception:pass  # stop already clears motion and records the output error
        await asyncio.sleep(.05)
@asynccontextmanager
async def lifespan(app):
    task=asyncio.create_task(ticker())
    yield
    task.cancel();await asyncio.gather(task,return_exceptions=True)
    try:control.stop('Gateway shutdown')
    except Exception:pass  # still close child processes after a failed motor stop
    direct_servo.disable();wasd_terminal.stop()
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
    if snap.get('mode')=='direct-usb':
        ready=direct_servo.status()['enabled']
        return {'ready':ready,'physical_lock':not ready,'transport':'DIRECT_USB','checks':{'motor_transport':ready},'missing_calibration':[],'profile':p.model_dump(),'navigation_ready':False}
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
    if mode==requested_mode and not isinstance(adapter,OfflineAdapter) and (mode!='direct_usb' or direct_servo.status()['enabled']):return
    control.stop('Changing robot mode');old=adapter
    direct_servo.disable()
    if mode=='direct_usb':
        if hasattr(old,'close'):old.close()
        # Never publish ROS velocities while opening the local motor bus.
        adapter=OfflineAdapter('Connecting direct USB');control.adapter=adapter
        try:direct_servo.enable()
        except Exception:
            requested_mode='direct_usb'
            raise
        replacement=DirectUsbAdapter(lambda:direct_servo.status()['enabled'])
        control.map_id='';control.status='Direct USB ready; explicitly select a controller'
    elif mode=='simulation':
        replacement=VirtualAdapter();control.map_id='virtual-workshop-alpha';control.status='Virtual robot ready'
    else:
        try:
            from .ros_adapter import RosAdapter
            replacement=RosAdapter()
        except Exception as e:replacement=OfflineAdapter(f'Real robot unavailable: {e}')
        control.status='Waiting for Raspberry Pi ROS 2'
    adapter=replacement;control.adapter=replacement;requested_mode=mode
    control.stop(control.status + '; explicitly take control')
    if old is not replacement and mode!='direct_usb' and hasattr(old,'close'):
        try:old.close()
        except Exception:pass

@app.get('/health')
def health():return {'instance':{'project_root':str(PROJECT_ROOT),'pid':os.getpid(),'protocol':PROTOCOL_VERSION},'public_url':secure_network()['public_url'],'service':'tbot-gateway','robot':adapter.snapshot()['mode'],'requested_mode':requested_mode,'voice_ready':(ROOT/'models/vosk-model-small-en-us-0.15').exists(),'firebase_configured':bool(store.get('settings','firebase')),'readiness':readiness()}
@app.get('/mode')
def get_mode(authorization:str=Header(default='')):bearer(authorization);return {'mode':requested_mode,'effective':adapter.snapshot().get('mode')}
@app.post('/mode')
async def select_mode(value:ModeRequest,authorization:str=Header(default='')):
    token=bearer(authorization)
    if sessions[token]['role']!='cockpit':raise HTTPException(403,'Cockpit role required')
    try:set_runtime_mode(value.mode)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc))
    return {'mode':requested_mode,'effective':adapter.snapshot().get('mode'),'readiness':readiness()}

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
async def simulation_reset(authorization:str=Header(default='')):
    bearer(authorization)
    if not isinstance(adapter,VirtualAdapter):raise HTTPException(409,'Virtual Lab is not active')
    control.stop('Virtual Lab reset');adapter.reset();control.map_id='virtual-workshop-alpha';store.put('settings','map',{'name':control.map_id});return {'reset':True}
def new_session(role):
    now=time.monotonic()
    for key in list(sessions):
        if now-sessions[key]['seen']>3600:sessions.pop(key,None);acks.pop(key,None);device_state.pop(key,None)
    token=secrets.token_urlsafe(32);sessions[token]={'seen':now,'role':role,'connected':False};acks[token]={}
    device_state[token]={'role':role,'connected':False,'authority':False,'camera':False,'gesture':'stop','confidence':0,'x':.5,'y':.5,'fps':0,'latency_ms':0,'last_command':None}
    return {'token':token,'role':role,'proof':control.issue_permit(token),'authority':False,'armed':False}

@app.post('/session')
async def session(request:Request,role:str=Query(default='cockpit'),pair:str=Query(default='')):
    if not trusted_origin(request.headers.get('origin')):raise HTTPException(403,'Untrusted browser origin')
    if role not in ('cockpit','tablet'):raise HTTPException(400,'Unknown device role')
    if role=='tablet':
        key=request.client.host if request.client else 'local'
        now=time.monotonic();recent=[when for when in pair_failures.get(key,[]) if now-when<60]
        pair_failures[key]=recent
        if len(recent)>=10:raise HTTPException(429,'Too many pairing attempts. Wait one minute.')
        entry=pairings.get(pair)
        if not entry or time.time()>entry['expires_at'] or entry.get('used'):
            recent.append(now)
            raise HTTPException(403,'Pairing code is invalid, used or expired. Create a new Tablet link on the laptop.')
        entry['used']=True
    return new_session(role)

@app.post('/session/resume')
async def resume_session(request:Request,authorization:str=Header(default='')):
    if not trusted_origin(request.headers.get('origin')):raise HTTPException(403,'Untrusted browser origin')
    previous=bearer(authorization);role=sessions[previous]['role']
    if control.owner==previous:control.stop('Tablet reconnected; explicit re-arm required')
    sessions.pop(previous,None);acks.pop(previous,None);device_state.pop(previous,None)
    return new_session(role)

@app.post('/pairing')
async def create_pairing(request:Request,authorization:str=Header(default='')):
    token=bearer(authorization)
    if sessions[token]['role']!='cockpit':raise HTTPException(403,'Cockpit role required')
    network=secure_network();base=network['public_url']
    if not base:raise HTTPException(503,network['reason'])
    code=f'{secrets.randbelow(1_000_000):06d}'
    while code in pairings:code=f'{secrets.randbelow(1_000_000):06d}'
    expires_seconds=300
    url=f'{base}/tablet?pair={code}';pairings[code]={'expires_at':time.time()+expires_seconds,'url':url,'creator':token,'used':False}
    return {'code':code,'expires_seconds':expires_seconds,'url':url,'secure':True}

@app.get('/direct-servo')
def direct_servo_status(authorization:str=Header(default='')):
    bearer(authorization);return direct_servo.status()

@app.post('/direct-servo')
async def set_direct_servo(request:Request,authorization:str=Header(default='')):
    token=bearer(authorization)
    if sessions[token]['role']!='cockpit':raise HTTPException(403,'Cockpit role required')
    enabled=request.query_params.get('enabled','false').lower()=='true'
    try:
        set_runtime_mode('direct_usb' if enabled else 'simulation')
        return direct_servo.status()
    except (ValueError,OSError) as e:
        direct_servo.error=str(e)
        raise HTTPException(422,str(e))

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
def pairing_qr(code:str,authorization:str=Header(default='')):
    token=bearer(authorization);entry=pairings.get(code)
    if entry and entry.get('creator')!=token:raise HTTPException(403,'This pairing belongs to another cockpit')
    if not entry or entry.get('used') or time.time()>entry['expires_at']:raise HTTPException(404,'Pairing expired or used')
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
        age=now-sessions[key]['seen'];items.append({'id':key[:8],**state,'connected':bool(state.get('connected') and age<2.0),'authority':bool(control.owner==key and control.armed and age<2.0),'heartbeat_age':round(age,3)})
    return {'devices':items}

@app.get('/telemetry')
async def telemetry(authorization:str=Header(default='')):
    token=bearer(authorization);return {**control.snapshot(),'proof':control.issue_permit(token),'authority':control.owner==token and control.armed,'requested_mode':requested_mode}

@app.post('/heartbeat')
async def http_heartbeat(authorization:str=Header(default='')):
    token=bearer(authorization);control.heartbeat(token);device_state[token]['connected']=True;device_state[token]['authority']=control.owner==token and control.armed;return {'ok':True,'proof':control.issue_permit(token),'authority':control.owner==token and control.armed,'armed':control.armed,'source':control.source,'status':control.status,'transport':requested_mode}

@app.post('/device-state')
async def http_device_state(request:Request,authorization:str=Header(default='')):
    token=bearer(authorization);payload=await request.json();state=device_state[token]
    for key in ('camera','gesture','confidence','x','y','fps','latency_ms'):
        if key in payload:state[key]=payload[key]
    state['connected']=True;state['authority']=control.owner==token and control.armed
    return {'ok':True,'authority':state['authority']}

@app.post('/gesture/activate')
async def activate_gesture(device:str=Query(default=''),authorization:str=Header(default='')):
    cockpit=bearer(authorization)
    if sessions[cockpit]['role']!='cockpit':raise HTTPException(403,'Laptop cockpit required')
    now=time.monotonic()
    candidates=[key for key,value in sessions.items() if value['role']=='tablet' and device_state.get(key,{}).get('connected') and now-value['seen']<2]
    if not candidates:raise HTTPException(409,'No live tablet is connected')
    matches=[key for key in candidates if key[:8]==device] if device else candidates
    if len(matches)!=1:raise HTTPException(409,'Select the intended connected tablet explicitly')
    tablet=matches[0]
    control.stop('Gesture control selected; phone must explicitly re-arm');control.owner=tablet;control.source='gesture';control.last_heartbeat=now
    for state in device_state.values():state['authority']=False
    return {'selected':True,'active':False,'armed':False,'device':tablet[:8]}

def command_result(payload,token):
    """One validation/ownership/output path for HTTP tablets and WebSocket controls."""
    command_id=payload.get('id','invalid') if isinstance(payload,dict) else 'invalid'
    if not isinstance(command_id,str):command_id='invalid'
    if isinstance(payload,dict) and payload.get('action') not in ('stop','cancel') and command_id in acks[token]:
        return {**acks[token][command_id],'proof':control.issue_permit(token),'authority':control.owner==token and control.armed,'armed':control.armed,'duplicate':True,'bridge_written':False,'hardware_emitted':False}
    try:
        c=Command.model_validate(payload)
        if direct_servo.enabled and c.action in ('distance','angle','mission','home_go','explore','resume','retry','skip'):
            raise ValueError('Direct servo control has no measured robot odometry. Use forward, backward, left, right or stop; disable Direct Servo and connect ROS for measured moves and missions.')
        if sessions[token]['role']=='tablet' and c.source!='gesture' and c.action not in ('stop','cancel'):raise ValueError('Tablet commands must use gesture control')
        control.execute(c,token)
        device_state[token]['connected']=True;device_state[token]['authority']=control.owner==token and control.armed
        ack={'type':'ack','id':c.id,'ok':True,'action':c.action,'output':adapter.snapshot().get('mode'),'bridge_written':requested_mode=='direct_usb' and direct_servo.enabled and c.action in ('drive','stop','cancel'),'hardware_emitted':False}
        device_state[token]['last_command']={'action':c.action,'acknowledged':True,'at':time.time(),'bridge_written':ack['bridge_written']}
        if c.action!='drive':control.log('command',f'{c.source}: {c.action}')
    except (ValueError,ValidationError) as e:ack={'type':'ack','id':command_id,'ok':False,'error':str(e)}
    ack.update(proof=control.issue_permit(token),authority=control.owner==token and control.armed,armed=control.armed)
    acks[token][command_id]=ack
    if len(acks[token])>200:acks[token].pop(next(iter(acks[token])))
    return ack

@app.post('/command')
async def http_command(request:Request,authorization:str=Header(default='')):
    token=bearer(authorization);payload=await request.json();ack=command_result(payload,token)
    if not ack['ok']:return Response(json.dumps({**ack,'detail':ack['error']}),status_code=409,media_type='application/json')
    return ack

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
                    state['authority']=control.owner==token and control.armed
                    await ws.send_json({'type':'device_ack','at':time.time()});continue
                await ws.send_json(command_result(payload,token))
        async def send():
            last_map=None
            while True:
                snapshot=control.snapshot();snapshot['proof']=control.issue_permit(token);snapshot['authority']=control.owner==token and control.armed and control.armed;snapshot['readiness']=readiness();snapshot['requested_mode']=requested_mode;grid=snapshot.get('map');stamp=(grid.get('stamp'),grid.get('received')) if grid else None
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
            if control.owner==token:control.stop('Control socket disconnected');control.owner=None

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
        rec=KaldiRecognizer(voice_model,16000);await ws.send_json({'ready':True});count=0;started=None
        while started is None or time.monotonic()-started<20:
            chunk=await asyncio.wait_for(ws.receive_bytes(),30 if started is None else 3)
            if started is None:started=time.monotonic()
            count+=len(chunk)
            if count>640000 or len(chunk)>32000:break
            auth(token)
            complete=await asyncio.to_thread(rec.AcceptWaveform,chunk)
            result=json.loads(rec.Result() if complete else rec.PartialResult());text=result.get('text',result.get('partial',''))
            if text.strip() in ('stop','robot stop','emergency stop','cancel mission'):
                control.stop('Voice stop');await ws.send_json({'stopped':True,'transcript':text})
    except (WebSocketDisconnect,asyncio.TimeoutError,HTTPException):pass
    except Exception as e:
        try:await ws.send_json({'error':'Offline voice recognition unavailable: '+str(e)})
        except (RuntimeError,WebSocketDisconnect):pass
    finally:
        try:await ws.close()
        except (RuntimeError,WebSocketDisconnect):pass

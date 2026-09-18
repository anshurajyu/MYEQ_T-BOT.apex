"use client";
import { useState, useEffect, useRef, useCallback } from "react";
import {
  Gamepad2,
  Radar,
  Route,
  Mic,
  Settings2,
  Square,
  Home,
  Flag,
  Play,
  Pause,
  Save,
  Cloud,
  Radio,
  Zap,
  ImagePlus,
  FlaskConical,
  ArrowUpRight,
  Tablet,
  Wrench,
  CheckCircle2,
  AlertTriangle,
  Crosshair,
  RotateCcw,
  SkipForward,
  XCircle,
  Terminal,
} from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
  SheetDescription,
} from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import {
  API,
  useGateway,
  type Pose,
  type Mission,
  type Grid,
  type HardwareProfile,
  type ReadinessReport,
  type PathPlan,
  type DeviceSession,
} from "@/lib/tbot/client";
import { recordVoice } from "@/lib/tbot/audio";
import { websocketUrl } from "@/lib/tbot/transport";
import { createVoiceIntent, createVoiceSession, openVoiceLink, type VoicePhase } from "@/lib/tbot/voice-session";
const activities = [
  { name: "Drive", icon: Gamepad2, caption: "Teach the machine." },
  { name: "Explore", icon: Radar, caption: "Map the unknown." },
  { name: "Missions", icon: Route, caption: "Give chaos a route." },
  { name: "Gesture Control", icon: Tablet, caption: "Finger becomes joystick." },
  { name: "Voice", icon: Mic, caption: "Speak a command." },
  { name: "Tests", icon: FlaskConical, caption: "Measure. Don’t guess." },
  { name: "Calibration", icon: Wrench, caption: "Measure the creature." },
  { name: "Diagnostics", icon: Radio, caption: "Interrogate every wire." },
  { name: "Code Runner", icon: Terminal, caption: "Run your Python files." },
];
function MapView({
  grid,
  scan,
  pose,
  points,
  path,
  home,
  onPoint,
  onMove,
  stale,
}: {
  grid: Grid | null;
  scan: number[][];
  pose: Pose | null;
  points: Pose[];
  path: Pose[];
  home: Pose | null;
  onPoint: (p: Pose) => void;
  onMove: (index: number, p: Pose) => void;
  stale: boolean;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const dragging = useRef<number | null>(null);
  useEffect(() => {
    const c = canvas.current;
    if (!c || !grid) return;
    c.width = grid.width;
    c.height = grid.height;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const image = ctx.createImageData(grid.width, grid.height);
    for (let i = 0; i < grid.data.length; i++) {
      const v = grid.data[i],
        j = i * 4;
      const color =
        v < 0 ? [27, 23, 35] : v > 50 ? [170, 148, 191] : [63, 60, 73];
      image.data.set([...color, 255], j);
    }
    ctx.putImageData(image, 0, 0);
  }, [grid]);
  if (!grid)
    return (
      <div className="mc-empty-map">
        <Radar size={65} />
        <h2>Waiting for the real picture.</h2>
        <p>
          Connect ROS Simulation and start SLAM.
          <br />
          Live scans and the map appear here when available.
        </p>
        <small>No generated room. No invented sensor readings.</small>
      </div>
    );
  const toCell = (p: Pose) => {
    const dx = p.x - grid.origin.x,
      dy = p.y - grid.origin.y,
      a = grid.origin.yaw;
    return {
      x: (Math.cos(a) * dx + Math.sin(a) * dy) / grid.resolution,
      y: (-Math.sin(a) * dx + Math.cos(a) * dy) / grid.resolution,
    };
  };
  const marker = (p: Pose) => {
    const n = toCell(p);
    return `${n.x},${n.y}`;
  };
  const pointerPose = (e: React.MouseEvent<SVGSVGElement> | React.PointerEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect(),
      x = ((e.clientX - rect.left) / rect.width) * grid.width * grid.resolution,
      y = (1 - (e.clientY - rect.top) / rect.height) * grid.height * grid.resolution,
      a = grid.origin.yaw;
    return {
      x: grid.origin.x + Math.cos(a) * x - Math.sin(a) * y,
      y: grid.origin.y + Math.sin(a) * x + Math.cos(a) * y,
      yaw: 0,
      dwell: 0,
    };
  };
  return (
    <div
      className="mc-map"
      style={{ aspectRatio: `${grid.width}/${grid.height}` }}
    >
      <canvas ref={canvas} />
      <svg
        viewBox={`0 0 ${grid.width} ${grid.height}`}
        preserveAspectRatio="none"
        onClick={(e) => onPoint(pointerPose(e))}
        onPointerMove={(e) => {
          if (dragging.current != null) onMove(dragging.current, pointerPose(e));
        }}
        onPointerUp={() => { dragging.current = null; }}
        onPointerCancel={() => { dragging.current = null; }}
        aria-label="Live ROS occupancy map. Click to add a mission checkpoint."
      >
        <g transform={`translate(0 ${grid.height}) scale(1 -1)`}>
          {!stale &&
            scan.map(([x, y], i) => {
              const p = toCell({ x, y, yaw: 0 });
              return (
                <circle
                  key={i}
                  cx={p.x}
                  cy={p.y}
                  r={Math.max(1, grid.width / 500)}
                  fill="#c0fc43"
                />
              );
            })}
          <polyline
            points={path.map(marker).join(" ")}
            fill="none"
            stroke="#c0fc43"
            strokeWidth="2.5"
            strokeLinejoin="round"
          />
          <polyline
            points={points.map(marker).join(" ")}
            fill="none"
            stroke="#a680f8"
            strokeWidth="2"
            strokeDasharray="4 3"
          />
          {points.map((p, i) => {
            const c = toCell(p);
            return (
              <g key={i} transform={`translate(${c.x} ${c.y})`} onClick={(e)=>e.stopPropagation()} onPointerDown={(e)=>{e.stopPropagation();dragging.current=i;e.currentTarget.ownerSVGElement?.setPointerCapture(e.pointerId)}}>
                <circle r="6" fill="#a680f8" />
                <text
                  transform="scale(1 -1)"
                  textAnchor="middle"
                  y="3"
                  fontSize="8"
                  fill="#151015"
                >
                  {i + 1}
                </text>
              </g>
            );
          })}
          {home && (
            <circle
              cx={toCell(home).x}
              cy={toCell(home).y}
              r="7"
              stroke="#ad8bff"
              strokeWidth="3"
              fill="none"
            />
          )}
          {pose && (
            <g
              transform={`translate(${toCell(pose).x} ${toCell(pose).y}) rotate(${((pose.yaw - grid.origin.yaw) * 180) / Math.PI})`}
            >
              <circle r="6" fill="#c0fc43" />
              <path d="M0-4 L10 0 L0 4Z" fill="#fff" />
            </g>
          )}
        </g>
      </svg>
      {stale && (
        <span className="mc-stale">Live scan unavailable or stale</span>
      )}
    </div>
  );
}
function GestureCommandMap({direction}:{direction:string}){
  const positions:Record<string,{x:number;y:number;rotation:number}>={
    forward:{x:60,y:17,rotation:-90},backward:{x:60,y:63,rotation:90},left:{x:25,y:40,rotation:180},right:{x:95,y:40,rotation:0},stop:{x:60,y:40,rotation:0},
  };
  const marker=positions[direction]||positions.stop;
  return <div className={`gesture-command-map ${direction}`}>
    <div className="gesture-map-head"><span>TABLET COMMAND MAP</span><strong>{direction.toUpperCase()}</strong></div>
    <svg viewBox="0 0 120 80" role="img" aria-label={`Tablet command ${direction}`}>
      <defs><pattern id="gesture-grid" width="10" height="10" patternUnits="userSpaceOnUse"><path d="M10 0H0V10" fill="none" stroke="#ffffff12" strokeWidth=".5"/></pattern></defs>
      <rect width="120" height="80" rx="4" fill="#17131f"/><rect width="120" height="80" rx="4" fill="url(#gesture-grid)"/>
      <path d={`M60 40L${marker.x} ${marker.y}`} stroke="#c0fc4355" strokeWidth="1.5" strokeDasharray="3 3"/>
      <circle cx="60" cy="40" r="8" fill="#c0fc43" stroke="#0b0d0a" strokeWidth="2"/>
      <path d="M60 32L63 38H57Z" fill="#fff"/>
      <g transform={`translate(${marker.x} ${marker.y}) rotate(${marker.rotation})`}><rect x="-6" y="-4" width="12" height="8" rx="2" fill="#fff"/><path d="M6-6L13 0L6 6Z" fill="#fff"/></g>
      <text x="60" y="76" textAnchor="middle" fill="#a7a1b2" fontSize="4">ROBOT POSITION LOCKED · COMMAND DISPLAY ONLY</text>
    </svg>
  </div>
}
export function MissionConsole() {
  const g = useGateway(),
    t = g.telemetry;
  const [activity, setActivity] = useState("Home"),
    [team, setTeam] = useState(true),
    [source, setSource] = useState("keyboard"),
    [armed, setArmed] = useState(false),
    [points, setPoints] = useState<Pose[]>([]),
    [name, setName] = useState("Lab route"),
    [routeMapId, setRouteMapId] = useState(""),
    [returnHome, setReturnHome] = useState(true),
    [distance, setDistance] = useState(20),
    [angle, setAngle] = useState(90),
    [mapName, setMapName] = useState("Lab map"),
    [voiceText, setVoiceText] = useState(""),
    [voicePhase, setVoicePhase] = useState<VoicePhase>("idle"),
    [cloudConfig, setCloudConfig] = useState<Record<string, string>>({}),
    [cloudStatus, setCloudStatus] = useState("Local only"),
    [padStatus, setPadStatus] = useState("Gamepad not connected"),
    [technical, setTechnical] = useState(false);
  const [readiness, setReadiness] = useState<ReadinessReport | null>(null),
    [hardware, setHardware] = useState<HardwareProfile | null>(null),
    [routePlan, setRoutePlan] = useState<PathPlan | null>(null),
    [plannedFingerprint, setPlannedFingerprint] = useState(""),
    [devices, setDevices] = useState<DeviceSession[]>([]),
    [qrImage, setQrImage] = useState(""),
    [pairing, setPairing] = useState<{code:string;url:string;expires_seconds:number;secure:boolean;expiresAt:number;knownDeviceIds:string[]}|null>(null),
    [directServo, setDirectServoState] = useState<{enabled:boolean;error:string;port:string;last_motor_write?:{direction:string;seq:number;right_speed:number;left_speed:number;received_at:number}}>({enabled:false,error:"Direct servo control is off",port:""}),
    [wasdTerminal, setWasdTerminal] = useState<{running:boolean;error:string;path:string;python?:string}>({running:false,error:"WASD terminal is not running",path:"robot-code/t-bot_wasd.py"}),
    [calibratingPad, setCalibratingPad] = useState(false),
    [padSnapshot, setPadSnapshot] = useState<{id:string;axes:number[];buttons:boolean[]}|null>(null),
    [padMap, setPadMap] = useState({accept:2,back:1,stop:3,mark:0,previous:4,next:5,deadman:7,rtAxis:5,axisX:0,axisY:1}),
    [runnerFiles, setRunnerFiles] = useState<string[]>([]),
    [runnerRoot, setRunnerRoot] = useState(""),
    [runnerPath, setRunnerPath] = useState(""),
    [runnerArgs, setRunnerArgs] = useState(""),
    [runnerJob, setRunnerJob] = useState<{id:string;path:string;output:string;running:boolean;returncode:number|null}|null>(null);
  const voiceGeneration = useRef(0);
  const voiceIntent = useRef<ReturnType<typeof createVoiceIntent> | null>(null);
  const cancelVoiceRef = useRef(() => {});
  const voiceSession = useRef<ReturnType<typeof createVoiceSession> | null>(null);
  const voicePorts = useRef<Parameters<typeof createVoiceSession>[0] | null>(null);
  const photo = useRef<HTMLInputElement>(null),
    driveTimer = useRef<ReturnType<typeof setInterval> | null>(null),
    completionSeen = useRef(false),
    controlState = useRef({ source, armed });
  const activityState = useRef({ activity, pose: t?.pose || null, mapId: t?.map_id || "" });
  activityState.current = { activity, pose: t?.pose || null, mapId: t?.map_id || "" };
  controlState.current = { source, armed };
  useEffect(()=>{if(activity==="Tablet")setActivity("Gesture Control")},[activity]);
  const perform = useCallback(
    async (c: Record<string, unknown>) => {
      try {
        await g.send({ source, ...c });
        return true;
      } catch (e) {
        g.setMessage(String(e));
        return false;
      }
    },
    [g.send, g.setMessage, source],
  );
  const toggleDirectServo = useCallback(async (enabled:boolean) => {
    voiceGeneration.current++;
    cancelVoiceRef.current();
    setArmed(false);
    try { setDirectServoState(await g.request(`/direct-servo?enabled=${enabled}`, {method:"POST"}) as {enabled:boolean;error:string;port:string;last_motor_write?:{direction:string;seq:number;right_speed:number;left_speed:number;received_at:number}}); }
    catch (e) { g.setMessage(String(e)); }
  },[g.request,g.setMessage]);
  const toggleWasdTerminal = useCallback(async (enabled:boolean) => {
    try {
      const state=await g.request(`/wasd-terminal?enabled=${enabled}`, {method:"POST"}) as {running:boolean;error:string;path:string;python?:string};
      setWasdTerminal(state);
      g.setMessage(enabled ? "WASD servo controller opened in Terminal." : "WASD terminal controller stopped.");
    } catch (e) { g.setMessage(String(e)); }
  },[g.request,g.setMessage]);
  const activateGesture = useCallback(async()=>{
    voiceGeneration.current++;
    cancelVoiceRef.current();
    try{
      await g.request('/gesture/activate',{method:'POST'});
      setSource('gesture');setArmed(false);g.setMessage('Gesture control selected. On the phone, center your finger and request control.');
    }catch(e){g.setMessage(String(e));}
  },[g.request,g.setMessage]);
  useEffect(()=>{if(g.token){
    void g.request("/direct-servo").then((v)=>setDirectServoState(v as {enabled:boolean;error:string;port:string;last_motor_write?:{direction:string;seq:number;right_speed:number;left_speed:number;received_at:number}})).catch(()=>{});
    void g.request("/wasd-terminal").then((v)=>setWasdTerminal(v as {running:boolean;error:string;path:string;python?:string})).catch(()=>{});
  }},[g.token,g.request]);
  const stop = useCallback(() => {
    voiceGeneration.current++;
    cancelVoiceRef.current();
    setArmed(false);
    if (driveTimer.current) clearInterval(driveTimer.current);
    void g.send({ action: "stop" }).catch((e) => g.setMessage(String(e)));
  }, [g.send, g.setMessage]);
  useEffect(() => { if (!g.authority) setArmed(false); }, [g.authority]);
  const selectSource = async (s: string) => {
    const attempt = ++voiceGeneration.current;
    const intent = createVoiceIntent(g.controlVersion);
    cancelVoiceRef.current();
    setArmed(false);
    try {
      const stopped = await g.send({ action: "stop", source: s });
      if (attempt !== voiceGeneration.current || !intent.advance(stopped)) return;
      const claimed = await g.send({ action: "claim", source: s });
      if (attempt !== voiceGeneration.current || !intent.advance(claimed)) return;
      setSource(s);
      setArmed(true);
    } catch (error) { g.setMessage(String(error)); }
  };
  useEffect(() => {
    if (!g.connected) {
      voiceGeneration.current++;
      cancelVoiceRef.current();
      setArmed(false);
    }
  }, [g.connected]);
  useEffect(() => {
    const complete = Boolean(
      t?.status?.includes("complete") || t?.status?.startsWith("Test complete"),
    );
    if (complete && !completionSeen.current) void g.refresh();
    completionSeen.current = complete;
  }, [t?.status, g.refresh]);
  useEffect(() => {
    if (g.library.settings.teamMode !== undefined)
      setTeam(Boolean(g.library.settings.teamMode));
    setCloudConfig(g.library.firebase);
  }, [g.library.settings, g.library.firebase]);
  const refreshSystem = useCallback(async () => {
    if (!g.token) return;
    try {
      const [ready, profile, connected, servo] = await Promise.all([
        g.request("/readiness"),
        g.request("/hardware-profile"),
        g.request("/devices"),
        g.request("/direct-servo"),
      ]);
      setDirectServoState(servo);
      setReadiness(ready as ReadinessReport);
      setHardware(profile as HardwareProfile);
      setDevices((connected as { devices: DeviceSession[] }).devices);
    } catch (e) {
      g.setMessage(String(e));
    }
  }, [g.request, g.setMessage, g.token]);
  useEffect(() => {
    if (!g.token) return;
    void refreshSystem();
    const timer = setInterval(() => void refreshSystem(), activity==="Gesture Control"||activity==="Drive" ? 500 : 2000);
    return () => clearInterval(timer);
  }, [g.token, refreshSystem, activity]);
  useEffect(() => {
    if (!pairing || !g.token) { setQrImage(""); return; }
    let active = true, objectUrl = "";
    const controller = new AbortController();
    void fetch(`${API}/pairing/qr.svg?code=${encodeURIComponent(pairing.code)}`, {
      headers: { Authorization: "Bearer " + g.token }, signal: controller.signal,
    }).then(async response => {
      if (!response.ok) throw Error("Pairing QR unavailable; use the secure link.");
      const blob = await response.blob();
      if (active) { objectUrl = URL.createObjectURL(blob); setQrImage(objectUrl); }
    }).catch(error => { if (active) g.setMessage(String(error)); });
    return () => { active = false; controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [pairing, g.token, g.setMessage]);
  const connectedTablet=devices.find((device)=>device.role==="tablet"&&device.connected);
  useEffect(()=>{
    if(!pairing)return;
    if(connectedTablet&&!pairing.knownDeviceIds.includes(connectedTablet.id)){setPairing(null);return}
    const remaining=pairing.expiresAt-Date.now();
    if(remaining<=0){setPairing(null);return}
    const timer=setTimeout(()=>setPairing(null),remaining);
    return()=>clearTimeout(timer);
  },[connectedTablet,pairing]);
  const refreshAfter = async (c: Record<string, unknown>) => {
    if (await perform(c)) await g.refresh();
  };
  const refreshRunner = useCallback(async () => {
    try {
      const result=await g.request('/runner/files') as {root:string;files:string[]};
      setRunnerRoot(result.root);setRunnerFiles(result.files);
      setRunnerPath((current)=>current && result.files.includes(current) ? current : result.files[0] || '');
    } catch(e){g.setMessage(String(e));}
  },[g.request,g.setMessage]);
  useEffect(()=>{if(activity==='Code Runner'&&g.token)void refreshRunner();},[activity,g.token,refreshRunner]);
  useEffect(()=>{
    if(!runnerJob?.running)return;
    const timer=setInterval(()=>void g.request(`/runner/runs/${runnerJob.id}`).then((value)=>setRunnerJob(value as typeof runnerJob)).catch((e)=>g.setMessage(String(e))),700);
    return()=>clearInterval(timer);
  },[runnerJob?.id,runnerJob?.running,g.request,g.setMessage]);
  const runPython = async () => {
    if(!runnerPath)return;
    try {
      const started=await g.request('/runner/run',{method:'POST',body:JSON.stringify({path:runnerPath,args:runnerArgs})}) as {id:string;path:string};
      setRunnerJob({...started,output:'',running:true,returncode:null});
    }
    catch(e){g.setMessage(String(e));}
  };
  const saveRunnerRoot = async () => {
    try {await g.request('/runner/root',{method:'POST',body:JSON.stringify({root:runnerRoot})});await refreshRunner();g.setMessage('VS Code workspace selected.');}
    catch(e){g.setMessage(String(e));}
  };
  const saveMission = async () => {
    try {
      await g.request("/missions", {
        method: "POST",
        body: JSON.stringify({
          name,
          map_id: routeMapId || t?.map_id || "unsaved-map",
          points,
          return_home: returnHome,
          map_version: routePlan?.map_version || "",
        }),
      });
      await g.refresh();
      g.setMessage("Mission saved on this laptop.");
    } catch (e) {
      g.setMessage(String(e));
    }
  };
  const pointFingerprint = JSON.stringify(
    points.map(({ x, y, yaw, dwell }) => ({ x, y, yaw, dwell })),
  );
  const validateRoute = async () => {
    if (!points.length || !t?.map_id) return;
    try {
      const result = (await g.request("/plan", {
        method: "POST",
        body: JSON.stringify({ map_id: t.map_id, points }),
      })) as PathPlan;
      setRoutePlan(result);
      if (result.valid) {
        const snapped = points.map((point, index) => ({
            ...point,
            ...result.segments[index]?.snapped_goal,
            yaw: point.yaw,
            dwell: point.dwell,
            name: point.name,
          }));
        setPoints(snapped);
        setPlannedFingerprint(JSON.stringify(snapped.map(({x,y,yaw,dwell})=>({x,y,yaw,dwell}))));
        g.setMessage(
          `Route validated: ${result.distance_m.toFixed(2)} m · ${Math.ceil(result.eta_seconds)} s.`,
        );
      } else {
        setPlannedFingerprint("");
        g.setMessage(result.errors.map((e) => `Point ${e.checkpoint}: ${e.message}`).join(" · "));
      }
    } catch (e) {
      setRoutePlan(null);
      g.setMessage(String(e));
    }
  };
  const createPairing = async () => {
    try {
      const value=await g.request("/pairing", { method: "POST" }) as {code:string;url:string;expires_seconds:number;secure:boolean};
      setPairing({...value,expiresAt:Date.now()+value.expires_seconds*1000,knownDeviceIds:devices.filter((device)=>device.role==="tablet").map((device)=>device.id)});
      g.setMessage("Tablet link ready. The QR disappears after connection.");
    } catch (e) {
      g.setMessage(String(e));
    }
  };
  const saveHardware = async () => {
    if (!hardware) return;
    try {
      const result = await g.request("/hardware-profile", {
        method: "POST",
        body: JSON.stringify(hardware),
      });
      setReadiness(result.readiness as ReadinessReport);
      g.setMessage("Hardware profile saved. Readiness recalculated.");
    } catch (e) {
      g.setMessage(String(e));
    }
  };
  const runVirtualDemo = async () => {
    if (!virtual) return;
    const mapId = t?.map_id || "virtual-workshop-alpha";
    if (!(await perform({ action: "claim", source: "mission" }))) return;
    setSource("mission");
    setArmed(true);
    if (!(await perform({ action: "home_set", source: "mission" }))) return;
    const demo: Mission = {
      name: "Built-in backend proof",
      map_id: mapId,
      points: [
        { x: -1.65, y: 1.25, yaw: 0, dwell: 0.3, name: "Green gate" },
        {
          x: -1.65,
          y: 0.65,
          yaw: -Math.PI / 2,
          dwell: 0.3,
          name: "Violet bend",
        },
      ],
      return_home: true,
    };
    if (
      await perform({ action: "mission", source: "mission", mission: demo })
    ) {
      setPoints(demo.points);
      setName(demo.name);
      setReturnHome(true);
      setActivity("Missions");
      g.setMessage(
        "Virtual mission launched. Watch the robot, LiDAR, checkpoints, and run history update live.",
      );
    }
  };
  const mission: Mission = {
    name,
    map_id: routeMapId || t?.map_id || "unsaved-map",
    points,
    return_home: returnHome,
    map_version: routePlan?.map_version || "",
  };
  const switchActivity = (a: string) => {
    stop();
    setActivity(a);
  };
  const drive = (linear: number, angular: number) => {
    if (!controlState.current.armed) return;
    void perform({ action: "drive", linear, angular }).then((ok) => {
      if (!ok) setArmed(false);
    });
  };
  const heldDrive = (v: number, w: number) => {
    if (driveTimer.current) clearInterval(driveTimer.current);
    drive(v, w);
    driveTimer.current = setInterval(() => drive(v, w), 100);
  };
  const releaseDrive = () => {
    if (driveTimer.current) clearInterval(driveTimer.current);
    driveTimer.current = null;
    if (controlState.current.armed)
      void perform({ action: "drive", linear: 0, angular: 0 });
  };
  useEffect(() => {
    const keys = new Set<string>();
    const down = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        stop();
        return;
      }
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        e.target instanceof HTMLSelectElement
      )
        return;
      if ("wasd".includes(e.key.toLowerCase()) && e.key.length === 1) {
        e.preventDefault();
        keys.add(e.key.toLowerCase());
      }
    };
    const up = (e: KeyboardEvent) => keys.delete(e.key.toLowerCase());
    const blur = () => {
      keys.clear();
      stop();
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    let driving = false;
    const timer = setInterval(() => {
      if (
        !controlState.current.armed ||
        controlState.current.source !== "keyboard"
      )
        return;
      const v = (Number(keys.has("w")) - Number(keys.has("s"))) * 0.08,
        w = (Number(keys.has("a")) - Number(keys.has("d"))) * 0.4;
      if (v || w || driving) {
        void g
          .send({ action: "drive", source: "keyboard", linear: v, angular: w })
          .catch((e) => {
            g.setMessage(String(e));
            setArmed(false);
          });
        driving = !!(v || w);
      }
    }, 100);
    return () => {
      clearInterval(timer);
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
    };
  }, [stop, g.send, g.setMessage]);
  useEffect(() => {
    let buttons: boolean[] = [],
      lastFocus = 0,
      loadedPad = "",
      driving = false;
    const timer = setInterval(() => {
      const pad = Array.from(navigator.getGamepads?.() ?? []).find(Boolean);
      if (!pad) {
        setPadStatus("Gamepad not connected");
        if (buttons.length) {
          buttons = [];
          stop();
        }
        return;
      }
      if (loadedPad !== pad.id) {
        loadedPad = pad.id;
        try {
          const saved = localStorage.getItem(`tbot-gamepad-v2:${pad.id}`);
          if (saved) setPadMap(JSON.parse(saved));
        } catch {}
      }
      setPadStatus(`Gamepad online • ${pad.id.slice(0, 28)}`);
      const b = pad.buttons.map((v) => v.pressed);
      setPadSnapshot({id:pad.id,axes:[...pad.axes],buttons:b});
      const pulse=(duration:number,strong=.5)=>{const actuator=(pad as any).vibrationActuator;void actuator?.playEffect?.("dual-rumble",{duration,strongMagnitude:strong,weakMagnitude:.35})};
      if (b[padMap.stop] && !buttons[padMap.stop]) {stop();pulse(450,1)}
      if (b[padMap.accept] && !buttons[padMap.accept]) {(document.activeElement as HTMLElement)?.click();pulse(100)}
      if (b[padMap.back] && !buttons[padMap.back]) switchActivity("Home");
      if (b[padMap.mark] && !buttons[padMap.mark] && activityState.current.activity==="Drive" && activityState.current.pose) {
        const pose=activityState.current.pose;setRouteMapId(activityState.current.mapId);setPoints(v=>[...v,{...pose,dwell:0,name:`Checkpoint ${v.length+1}`}]);pulse(90,.7)
      }
      if ((b[padMap.previous]&&!buttons[padMap.previous])||(b[padMap.next]&&!buttons[padMap.next])) {
        const index=Math.max(0,activities.findIndex(item=>item.name===activityState.current.activity));
        const step=b[padMap.previous]?-1:1;switchActivity(activities[(index+step+activities.length)%activities.length].name);pulse(90)
      }
      if(controlState.current.armed&&controlState.current.source==="gamepad"){
        const rtButton=pad.buttons[padMap.deadman]?.value||0;
        const rtAxis=(pad.axes[padMap.rtAxis]??-1);
        const throttle=Math.max(rtButton,Math.max(0,Math.min(1,(rtAxis+1)/2)));
        const active=throttle>.15;
        const x=Math.abs(pad.axes[padMap.axisX]||0)>.12?(pad.axes[padMap.axisX]||0):0;
        const y=Math.abs(pad.axes[padMap.axisY]||0)>.12?(pad.axes[padMap.axisY]||0):0;
        const power=.25+.75*throttle;
        if(active){void g.send({action:"drive",source:"gamepad",linear:-y*.06*power,angular:-x*.4*power}).catch(e=>{g.setMessage(String(e));setArmed(false)});driving=true}
        else if(driving){void g.send({action:"drive",source:"gamepad",linear:0,angular:0});driving=false}
      }
      const navX=Math.abs(pad.axes[2]||0)>.6?(pad.axes[2]||0):b[14]?-1:b[15]?1:0;
      const navY=Math.abs(pad.axes[3]||0)>.6?(pad.axes[3]||0):b[12]?-1:b[13]?1:0;
      if ((navX || navY) && Date.now() - lastFocus > 220) {
        const elements = Array.from(
          document.querySelectorAll<HTMLElement>(
            ".mission-console button:not([disabled]),.mission-console input,.mission-console select",
          ),
        ).filter((e) => e.getClientRects().length);
        const current=document.activeElement as HTMLElement;
        if(!elements.includes(current)){elements[0]?.focus()}
        else{
          const from=current.getBoundingClientRect(),fx=from.left+from.width/2,fy=from.top+from.height/2;
          const length=Math.hypot(navX,navY)||1,dx=navX/length,dy=navY/length;
          const next=elements.filter((element)=>element!==current).map((element)=>{const rect=element.getBoundingClientRect(),x=rect.left+rect.width/2-fx,y=rect.top+rect.height/2-fy,distance=Math.hypot(x,y)||1,alignment=(x*dx+y*dy)/distance;return{element,alignment,score:distance/Math.max(.15,alignment)}}).filter((item)=>item.alignment>.35).sort((a,b)=>a.score-b.score)[0]?.element;
          next?.focus();
        }
        lastFocus = Date.now();
      }
      buttons = b;
    }, 60);
    return () => clearInterval(timer);
  }, [stop,padMap,g.send,g.setMessage]);
  const previewVoice = async (text: string) => {
    const ticket = ++voiceGeneration.current;
    cancelVoiceRef.current();
    const intent = createVoiceIntent(g.controlVersion);
    voiceIntent.current = intent;
    const current = () => ticket === voiceGeneration.current && intent === voiceIntent.current && intent.current();
    try {
      const result = await g.request("/voice/interpret", {
        method: "POST", body: JSON.stringify({ text }),
      });
      if (!current()) return;
      setVoiceText(result.transcript);
      if (result.command.action === "stop") stop();
      else await executeVoice(result.command, current);
    } catch (error) { if (current()) g.setMessage(String(error)); }
  };
  const executeVoice = async (cmd: Record<string, unknown>, active: () => boolean) => {
    const intent = voiceIntent.current;
    const current = () => active() && intent !== null && intent === voiceIntent.current && intent.current();
    if (!current()) return;
    if (!["pause", "resume"].includes(String(cmd.action))) {
      try {
        const stopped = await g.send({action:"stop",source:"voice"});
        if (!intent!.advance(stopped) || !current()) return;
        const claimed = await g.send({action:"claim",source:"voice"});
        if (!intent!.advance(claimed) || !current()) return;
      }
      catch(e){if(current())g.setMessage(String(e));return;}
      // Stop/takeover is already ordered after the claim on this WebSocket.
      // A late acknowledgement must not stop the newly selected controller.
      if (!current()) return;
      setSource("voice");
      setArmed(false);
    }
    if (cmd.action === "named_mission") {
      const m = g.library.missions.find(
        (m) => m.name.toLowerCase() === String(cmd.name).toLowerCase(),
      );
      if (!m) {
        g.setMessage("No saved mission has that name.");
        return;
      }
      await g.send({
        action: "mission",
        source: "voice",
        mission: {
          name: m.name,
          map_id: m.map_id,
          points: m.points,
          return_home: m.return_home,
        },
      });
    } else if (cmd.action === "destination") {
      const matches = g.library.missions
        .flatMap((m) => (m.map_id === t?.map_id ? m.points : []))
        .filter(
          (p) => p.name?.toLowerCase() === String(cmd.name).toLowerCase(),
        );
      if (matches.length !== 1) {
        g.setMessage(
          "Destination missing or ambiguous. Name a checkpoint uniquely.",
        );
        return;
      }
      await g.send({
        action: "mission",
        source: "voice",
        mission: {
          name: "Voice destination",
          map_id: t?.map_id,
          points: matches,
          return_home: false,
        },
      });
    } else await g.send({ ...cmd, source: "voice" });
  };
  voicePorts.current = () => ({
    open: (onStop, onFailure) => openVoiceLink(websocketUrl(API, "/voice/live"), g.token, onStop, onFailure),
    record: recordVoice,
    interpret: (blob) => g.request("/voice/transcribe", {
      method: "POST", body: blob, headers: { "Content-Type": "audio/wav" },
    }),
    execute: executeVoice,
    stop, phase: setVoicePhase, message: g.setMessage, transcript: setVoiceText,
  });
  if (!voiceSession.current) voiceSession.current = createVoiceSession(() => voicePorts.current!());
  cancelVoiceRef.current = () => voiceSession.current?.cancel();
  const recording = voicePhase === "recording";
  const startRecording = () => {
    voiceGeneration.current++;
    if (!g.connected) { g.setMessage("Connect the gateway before recording."); return; }
    voiceIntent.current = createVoiceIntent(g.controlVersion);
    return voiceSession.current?.start();
  };
  const finishRecording = () => voiceSession.current?.finish();
  useEffect(() => () => {
    voiceGeneration.current++;
    voiceSession.current?.cancel();
    if (driveTimer.current) clearInterval(driveTimer.current);
  }, []);
  const sync = async () => {
    try {
      setCloudStatus("Syncing…");
      await g.request("/settings/firebase", {
        method: "POST",
        body: JSON.stringify(cloudConfig),
      });
      const { syncCloud } = await import("@/lib/tbot/cloud");
      const result = await syncCloud(cloudConfig, g.library);
      let imported = 0;
      for (const m of result.missions) {
        if (
          !g.library.missions.some(
            (local) => local.name === m.name && local.map_id === m.map_id,
          )
        ) {
          await g.request("/missions", {
            method: "POST",
            body: JSON.stringify({
              name: m.name,
              map_id: m.map_id,
              points: m.points,
              return_home: m.return_home,
            }),
          });
          imported++;
        }
      }
      setCloudStatus(`Synced as ${result.email}; ${imported} routes imported`);
      await g.refresh();
    } catch (e) {
      setCloudStatus(String(e));
    }
  };
  const fresh = t?.scan_age != null && t.scan_age < 0.75,
    live = t?.mode === "ros-hardware",
    virtual = t?.requested_mode ? t.requested_mode === "simulation" : t?.mode === "virtual-lab";
  const selectRuntimeMode = async (mode:"simulation"|"direct_usb"|"hardware") => {
    stop();
    try {
      const result=await g.request('/mode',{method:'POST',body:JSON.stringify({mode})});
      g.setMessage(result.effective==='ros-hardware'?'Real Raspberry Pi ROS 2 selected.':'Mode changed to '+result.effective+'.');
      await refreshSystem();
    } catch(e){g.setMessage(String(e));}
  };
  return (
    <div className="cockpit mission-console">
      <header className="cp-header">
        <button className="cp-logo" onClick={() => switchActivity("Home")}>
          <span className="logo-mark">
            <Zap fill="currentColor" />
          </span>
          <span>
            T—BOT<small>MISSION CONTROL / 02</small>
          </span>
        </button>
        <div className="cp-top">
          <span className="sim-tag">
            {live ? "ROS_PI" : t?.mode === "direct-usb" ? "DIRECT_USB" : virtual ? "SIMULATION" : "OUTPUT OFFLINE"}
          </span>
          <span>
            <Radio size={15} />
            {g.connected ? "Gateway online" : "Gateway offline"}
          </span>
          <div className="personality runtime-mode">
            <label htmlFor="motor-output-mode">Output</label>
            <select id="motor-output-mode" aria-label="Motor output mode" disabled={!g.connected} value={t?.requested_mode || "simulation"} onChange={event => void selectRuntimeMode(event.target.value as "simulation"|"direct_usb"|"hardware")}>
              <option value="simulation">SIMULATION</option><option value="direct_usb">DIRECT_USB · Mac adapter</option><option value="hardware">ROS_PI · ROS gateway</option>
            </select>
          </div>
          <div className="personality">
            <span>{team ? "TEAM MODE" : "PRESENTATION"}</span>
            <Switch
              aria-label="Team personality mode"
              checked={team}
              onCheckedChange={(v) => {
                setTeam(v);
                void g
                  .request("/settings/ui", {
                    method: "POST",
                    body: JSON.stringify({ teamMode: v }),
                  })
                  .catch((e) => g.setMessage(String(e)));
              }}
            />
          </div>
          <button className="stop-button" onClick={stop}>
            <Square size={15} fill="currentColor" /> STOP
          </button>
          <Sheet open={technical} onOpenChange={setTechnical}>
            <SheetTrigger asChild>
              <button className="mc-icon" aria-label="Open technical panel">
                <Settings2 />
              </button>
            </SheetTrigger>
            <SheetContent className="mc-sheet">
              <SheetHeader>
                <SheetTitle>Under the hood</SheetTitle>
                <SheetDescription>
                  Backend proof, sensor flow, storage, and cloud sync.
                </SheetDescription>
              </SheetHeader>
              <div className="mc-sheet-body">
                <h3>Connections</h3>
                <dl>
                  <dt>Local gateway</dt>
                  <dd>{g.connected ? "Connected" : "Disconnected"}</dd>
                  <dt>Data source</dt>
                  <dd>
                    {virtual ? "Simulation" : live ? "ROS_PI" : t?.mode === "direct-usb" ? "DIRECT_USB" : "Unavailable"}
                  </dd>
                  <dt>Navigation</dt>
                  <dd>{t?.navigation_ready ? "Ready" : "Unavailable"}</dd>
                  <dt>Safety guard</dt>
                  <dd>{t?.guard_ready ? "Ready" : "Unavailable"}</dd>
                  <dt>Scan age</dt>
                  <dd>
                    {t?.scan_age != null
                      ? `${t.scan_age.toFixed(2)} s`
                      : "Unavailable"}
                  </dd>
                  <dt>Odometry age</dt>
                  <dd>
                    {t?.odom_age != null
                      ? `${t.odom_age.toFixed(2)} s`
                      : "Unavailable"}
                  </dd>
                  <dt>Velocity</dt>
                  <dd>
                    {t?.odom
                      ? `${t.odom.linear.toFixed(2)} m/s · ${t.odom.angular.toFixed(2)} rad/s`
                      : "Unavailable"}
                  </dd>
                  <dt>Battery</dt>
                  <dd>
                    {t?.battery?.percent != null
                      ? `${t.battery.percent.toFixed(0)}%`
                      : "Unavailable"}
                  </dd>
                  <dt>Control source</dt>
                  <dd>{t?.source || "None"}</dd>
                  <dt>Map coverage</dt>
                  <dd>
                    {t?.coverage != null
                      ? `${t.coverage.toFixed(1)}%`
                      : "Unavailable"}
                  </dd>
                  <dt>Distance travelled</dt>
                  <dd>
                    {t?.distance_total != null
                      ? `${t.distance_total.toFixed(2)} m`
                      : "Unavailable"}
                  </dd>
                  <dt>Local database</dt>
                  <dd>{g.connected ? "Ready" : "Unavailable"}</dd>
                </dl>
                {virtual && (
                  <button
                    className="control"
                    onClick={() =>
                      void g
                        .request("/simulation/reset", { method: "POST" })
                        .then(() => {
                          setPoints([]);
                          return g.refresh();
                        })
                        .then(() => g.setMessage("Virtual Lab reset."))
                    }
                  >
                    Reset Virtual Lab
                  </button>
                )}
                <a
                  className="control mc-api-link"
                  href={`${API}/docs`}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open backend API inspector
                </a>
                {t?.error && <p className="mc-error">{t.error}</p>}
                <h3>{virtual ? "Virtual data channels" : "ROS topics"}</h3>
                <pre>{JSON.stringify(t?.topics || {}, null, 2)}</pre>
                <h3>Firebase • Spark</h3>
                <p>Optional cloud sync. Photos and maps stay on this laptop.</p>
                {["apiKey", "authDomain", "projectId", "teamId"].map((key) => (
                  <label key={key}>
                    {key}
                    <input
                      value={cloudConfig[key] || ""}
                      onChange={(e) =>
                        setCloudConfig((v) => ({ ...v, [key]: e.target.value }))
                      }
                    />
                  </label>
                ))}
                <button className="control" onClick={() => void sync()}>
                  <Cloud size={15} /> Sign in & sync team data
                </button>
                <button
                  className="control"
                  onClick={() =>
                    void import("@/lib/tbot/cloud")
                      .then((m) => m.signOutCloud(cloudConfig.projectId))
                      .then(() => setCloudStatus("Signed out"))
                  }
                >
                  Sign out of Firebase
                </button>
                <p aria-live="polite">{cloudStatus}</p>
                <h3>Recent events</h3>
                <button className="control" onClick={() => void g.refresh()}>
                  Refresh diagnostics
                </button>
                {g.library.events.map((e, i) => (
                  <p className="mc-event" key={i}>
                    <time>
                      {new Date(e.timestamp * 1000).toLocaleTimeString()}
                    </time>{" "}
                    {e.message}
                  </p>
                ))}
                <h3>Run history</h3>
                {g.library.runs.map((r, i) => (
                  <p key={i}>
                    {String(r.status)} · {String(r.completed_checkpoints)}{" "}
                    checkpoints
                  </p>
                ))}
              </div>
            </SheetContent>
          </Sheet>
        </div>
      </header>
      <main>
        <div className="section-line">
          <span>
            <i />
            ROBOTICS PROGRAM / {activity.toUpperCase()}
          </span>
          <a href="/prototype">Open standalone visual demo ↗</a>
        </div>
        <div className="mc-notice" role="status">
          {g.message}
        </div>
        {activity === "Home" ? (
          <>
            <section className="stage">
              <div className="intro">
                <span className="sticker">
                  {virtual
                    ? "NO BOT? NO EXCUSES."
                    : "THE COCKPIT GREW A BRAIN."}
                </span>
                <h1>
                  Same attitude.
                  <br />
                  <em>{virtual ? "Virtual wheels." : "Real signals."}</em>
                </h1>
                <p>
                  {virtual
                    ? "A complete test robot is running locally."
                    : "One command centre for mapping,"}
                  <br />
                  {virtual
                    ? "Break things safely before tomorrow."
                    : "missions, voice, and gestures."}
                </p>
                <button
                  className="primary"
                  onClick={() => switchActivity("Explore")}
                >
                  {virtual ? "ENTER VIRTUAL LAB" : "OPEN THE MAP"}{" "}
                  <ArrowUpRight size={19} />
                </button>
              </div>
              <div className="mascot-wrap">
                <img
                  className="mascot"
                  src="/tbot-mascot.png"
                  alt="Mischievous TurtleBot3 Burger mascot"
                />
                <span className="bot-label">
                  TB—03{" "}
                  <span>
                    {virtual ? "DIGITAL CLONE ONLINE" : "AWAITING ORDERS"}
                  </span>
                </span>
              </div>
              <aside className="buddy">
                <span className="status-pill">
                  {t?.status || "Gateway disconnected"}
                </span>
                <div className="speech">
                  “
                  {team
                    ? virtual
                      ? "I am imaginary. Your bugs will be painfully real."
                      : "The backend exists. My patience remains experimental."
                    : "Ready to plan your next mission."}
                  ”
                </div>
                <div className="controller-card">
                  <Gamepad2 />
                  <div>
                    <strong>{padStatus}</strong>
                    <p>Button mapping session: next up.</p>
                  </div>
                </div>
              </aside>
            </section>
            <section className="mc-proof" aria-label="Backend status">
              <div>
                <small>01 / GATEWAY</small>
                <strong>{g.connected ? "ONLINE" : "OFFLINE"}</strong>
                <p>Commands and acknowledgements</p>
              </div>
              <div>
                <small>02 / DATA SOURCE</small>
                <strong>
                  {virtual ? "SIMULATION" : live ? "ROS_PI" : t?.mode === "direct-usb" ? "DIRECT_USB" : "WAITING"}
                </strong>
                <p>{t?.scenario || "No active world"}</p>
              </div>
              <div>
                <small>03 / SENSOR STREAM</small>
                <strong>{fresh ? "LIVE" : "STALE"}</strong>
                <p>{t?.scan?.points.length || 0} LiDAR points</p>
              </div>
              <div>
                <small>04 / DATABASE</small>
                <strong>{g.connected ? "READY" : "OFFLINE"}</strong>
                <p>
                  {g.library.missions.length} routes · {g.library.runs.length}{" "}
                  runs
                </p>
              </div>
              <div>
                <small>05 / COVERAGE</small>
                <strong>
                  {t?.coverage != null ? `${t.coverage.toFixed(1)}%` : "—"}
                </strong>
                <p>
                  {t?.distance_total != null
                    ? `${t.distance_total.toFixed(2)} m travelled`
                    : "No odometry"}
                </p>
              </div>
              {virtual && (
                <button
                  className="mc-demo-button"
                  onClick={() => void runVirtualDemo()}
                >
                  <Play /> RUN FULL DEMO
                </button>
              )}
              <button onClick={() => setTechnical(true)}>
                <Settings2 /> INSPECT BACKEND
              </button>
            </section>
            {virtual && (
              <section
                className="mc-test-guide"
                aria-label="How to test without the robot"
              >
                <div>
                  <small>TEST 01</small>
                  <strong>Run full demo</strong>
                  <p>
                    A real command route goes through the WebSocket gateway,
                    planner, simulator, and SQLite history.
                  </p>
                </div>
                <div>
                  <small>TEST 02</small>
                  <strong>Drive it yourself</strong>
                  <p>
                    Open Drive, select keyboard, and use WASD. The map and LiDAR
                    move with the robot.
                  </p>
                </div>
                <div>
                  <small>TEST 03</small>
                  <strong>Try inputs</strong>
                  <p>
                    Voice and camera gestures command the same virtual robot,
                    with the same safety limits.
                  </p>
                </div>
                <div>
                  <small>PROOF</small>
                  <strong>Inspect backend</strong>
                  <p>
                    See sensor age, topics, navigation, guard state, database
                    counts, and the raw API.
                  </p>
                </div>
              </section>
            )}
          </>
        ) : (
          <section className="mc-workspace">
            <div className="mc-work-head">
              <div>
                <span className="micro">
                  {live
                    ? "LIVE ROS 2 SIMULATION"
                    : virtual
                      ? "LIVE VIRTUAL LAB"
                      : "WAITING FOR ROS 2"}
                </span>
                <h1>
                  {activity === "Voice"
                    ? "Speak a command."
                    : activity === "Gesture Control"
                      ? "One finger. Five safe zones."
                      : activity === "Explore"
                        ? "Make the unknown known."
                        : activity === "Missions"
                          ? "Your plan. My wheels."
                          : activity === "Tests"
                            ? "Let’s measure that ego."
                            : activity === "Calibration"
                              ? "Measure first. Regret less."
                            : activity === "Diagnostics"
                              ? "Every wire gets questioned."
                              : activity === "Code Runner"
                                ? "Run the script. Watch the consequences."
                                : "Teach me the route."}
                </h1>
              </div>
              <span className="status-pill">{t?.status || "Disconnected"}</span>
            </div>
            <div className="mc-work-grid">
              <div className="mc-main-panel">
                {activity === "Voice" ? (
                  <div className="mc-input-panel">
                    <Mic size={45} />
                    <h2>Offline voice commands</h2>
                    <p>
                      English • laptop microphone • no audio sent to the cloud
                    </p>
                    <button
                      className="primary"
                      disabled={!g.connected || voicePhase === "starting" || voicePhase === "processing"}
                      onClick={() =>
                        void (recording ? finishRecording() : startRecording())
                      }
                    >
                      {voicePhase === "starting" ? "PREPARING MICROPHONE…" : voicePhase === "processing" ? "RECOGNIZING…" : recording ? "FINISH RECORDING" : "START RECORDING"}
                    </button>
                    <p>
                      {recording
                        ? "Listening. Maximum 15 seconds."
                        : "Recognized commands execute immediately. Stop still wins."}
                    </p>
                    <label>
                      Transcript / command practice
                      <input
                        value={voiceText}
                        onChange={(e) => {
                          setVoiceText(e.target.value);
                        }}
                        placeholder="Forward"
                      />
                    </label>
                    <button
                      className="control"
                      onClick={() => void previewVoice(voiceText)}
                    >
                      RUN COMMAND
                    </button>
                    <p className="mc-command-list">
                      “Forward” · “Backward” · “Left” · “Right” · “Stop” · “Go home” · “Set home” · “Start exploration” ·
                      “Pause mission” · “Resume mission” · “Start mission Lab
                      route” · “Move forward twenty centimeters” · “Turn left
                      ninety degrees”
                    </p>
                    <small>
                      Forward, backward, left, and right run for five seconds.
                      Every recognized command acts immediately; the on-screen
                      Stop and Esc are always available.
                    </small>
                  </div>
                ) : activity === "Gesture Control" ? (
                  <div className="mc-input-panel mc-tablet-pair">
                    <GestureCommandMap direction={connectedTablet?.gesture || "stop"}/>
                    <h2>{connectedTablet ? "Tablet commands are arriving." : "Pair the tablet. Keep the camera there."}</h2>
                    <p>
                      The tablet sees your hand locally. This panel shows the
                      command currently reaching Mission Control.
                    </p>
                    {connectedTablet && !pairing ? (
                      <div className={`device-card gesture-monitor ${connectedTablet.gesture || "stop"}`}>
                        <small>LIVE TABLET COMMAND</small>
                        <strong>{(connectedTablet.gesture || "stop").toUpperCase()}</strong>
                        <span>{connectedTablet.camera ? "CAMERA ACTIVE" : "CAMERA PAUSED"} · {connectedTablet.authority ? "ROBOT CONTROL ACTIVE" : "WAITING FOR CONTROL"}</span>
                        <button className="control" onClick={() => void createPairing()}>PAIR / REPAIR TABLET</button>
                      </div>
                    ) : pairing ? (
                      <>
                        {qrImage && <img src={qrImage} alt="Tablet pairing QR code" />}
                        <small>Scan once. This QR disappears when the tablet connects.</small>
                      </>
                    ) : (
                      <button className="primary" onClick={() => void createPairing()}>
                        CREATE TABLET LINK
                      </button>
                    )}
                    <div className="device-card">
                      <strong>{directServo.enabled ? "DIRECT_USB · MOTOR TRANSPORT READY" : "DIRECT_USB OFF"}</strong>
                      <span>{directServo.port || "No adapter selected"} · Motor IDs: right 1 / left 2 · raw speed 1000</span>
                      <button className="control" onClick={()=>void toggleDirectServo(!directServo.enabled)}>{directServo.enabled ? "DISCONNECT SERVO" : "CONNECT USB ADAPTER"}</button>
                      <small>{directServo.error}</small>
                      <small>{directServo.last_motor_write ? `Last SDK-confirmed output: ${directServo.last_motor_write.direction} · R ${directServo.last_motor_write.right_speed} / L ${directServo.last_motor_write.left_speed} · packet ${directServo.last_motor_write.seq}` : "No motor SDK write confirmed in this connection."} API acceptance does not verify wheel motion.</small>
                    </div>
                  </div>
                ) : activity === "Calibration" ? (
                  <div className="mc-input-panel">
                    <Wrench size={52} />
                    <h2>Physical autonomy stays locked until the measurements exist.</h2>
                    <p>{hardware && ['wheel_separation_m','footprint_length_m','footprint_width_m','lidar_x_m','lidar_y_m','imu_x_m','imu_y_m','encoder_scale'].filter((key)=>hardware[key as keyof HardwareProfile] == null).length ? `Missing: ${['wheel_separation_m','footprint_length_m','footprint_width_m','lidar_x_m','lidar_y_m','imu_x_m','imu_y_m','encoder_scale'].filter((key)=>hardware[key as keyof HardwareProfile] == null).join(", ")}.` : "Geometry and encoder values are recorded. Run the staged floor checks before trusting them."}</p>
                    <div className="calibration-steps">
                      {['Motor polarity','Wheel circumference','Wheel separation','Straight-line trim','90° rotation','Footprint','LiDAR / IMU offsets','Safe velocity limits'].map((item,i)=><div key={item}><b>{String(i+1).padStart(2,'0')}</b><span>{item}</span></div>)}
                    </div>
                  </div>
                ) : activity === "Diagnostics" ? (
                  <div className="mc-input-panel">
                    <Radio size={52} />
                    <h2>{virtual ? "Simulated telemetry. No physical robot is being diagnosed." : readiness?.ready ? "All required systems answer." : "Autonomy is locked. The machine has standards."}</h2>
                    {virtual && <p>These values come from Virtual Lab so you can test the interface. Choose DIRECT_USB for the adapter plugged into this Mac. Choose ROS_PI only with the ROS gateway configured.</p>}
                    <div className="diagnostic-grid">
                      {Object.entries(readiness?.checks || {}).map(([key,ok])=><div className={ok?'ok':'bad'} key={key}>{ok?<CheckCircle2/>:<AlertTriangle/>}<span>{key.replaceAll('_',' ')}</span><b>{ok?'READY':'MISSING'}</b></div>)}
                    </div>
                    <pre>{JSON.stringify(t?.diagnostics || {}, null, 2)}</pre>
                  </div>
                ) : activity === "Code Runner" ? (
                  <div className="mc-input-panel">
                    <Terminal size={52} />
                    <h2>Run Python from your VS Code workspace.</h2>
                    <p>Only Python files beneath the selected workspace can run. Output appears here. No raw shell console has been invited to the party.</p>
                    <label>Python file
                      <select value={runnerPath} onChange={(e)=>setRunnerPath(e.target.value)}>
                        {!runnerFiles.length&&<option value="">No Python files found</option>}
                        {runnerFiles.map((path)=><option key={path} value={path}>{path}</option>)}
                      </select>
                    </label>
                    <label>Arguments (optional)<input value={runnerArgs} onChange={(e)=>setRunnerArgs(e.target.value)} placeholder="--port /dev/ttyACM0"/></label>
                    <button className="primary" disabled={!runnerPath||runnerJob?.running} onClick={()=>void runPython()}><Play size={16}/> RUN PYTHON FILE</button>
                    {runnerJob&&<div className="runner-output"><strong>{runnerJob.running?'RUNNING':'FINISHED'} · {runnerJob.path}{runnerJob.returncode!=null?` · exit ${runnerJob.returncode}`:''}</strong>{runnerJob.running&&<button onClick={()=>void g.request(`/runner/runs/${runnerJob.id}/stop`,{method:'POST'}).then(()=>g.setMessage('Python run stopped.'))}>STOP SCRIPT</button>}<pre>{runnerJob.output||'Waiting for output…'}</pre></div>}
                  </div>
                ) : (
                  <MapView
                    grid={t?.map || null}
                    pose={t?.pose || null}
                    scan={t?.scan?.points || []}
                    points={points}
                    path={routePlan?.segments.flatMap((segment) => segment.poses) || []}
                    home={t?.home || null}
                    stale={!fresh}
                    onPoint={(p) => {
                      if (activity === "Missions") {
                        if (routeMapId && routeMapId !== t?.map_id) {
                          g.setMessage(
                            "Start a new route before adding points from a different map.",
                          );
                          return;
                        }
                        setRouteMapId(t?.map_id || "unsaved-map");
                        setPoints((v) => [...v, p]);
                      }
                    }}
                    onMove={(index, p) => {
                      if (activity !== "Missions") return;
                      setRoutePlan(null);
                      setPlannedFingerprint("");
                      setPoints((value) => value.map((item, i) => i === index ? {...item, x:p.x, y:p.y} : item));
                    }}
                  />
                )}
              </div>
              <aside className="mission-panel">
                <h3>CONTROL AUTHORITY</h3>
                <p className="helper">
                  Select an input before movement. Switching activities stops
                  the current task.
                </p>
                <button
                  className="control"
                  onClick={() => void selectSource("keyboard")}
                >
                  {armed && source === "keyboard"
                    ? "Keyboard / manual armed"
                    : "Take keyboard control"}
                </button>
                <button
                  className="control"
                  onClick={() => void selectSource("mission")}
                >
                  {armed && source === "mission"
                    ? "Mission control selected"
                    : "Take mission control"}
                </button>
                <button
                  className="control"
                  disabled={!padSnapshot}
                  onClick={() => void selectSource("gamepad")}
                >
                  {armed && source === "gamepad"
                    ? "Gamepad control selected"
                    : padSnapshot
                      ? "Take gamepad control"
                      : "Connect gamepad to unlock"}
                </button>
                <button
                  className="control"
                  onClick={() => connectedTablet ? void activateGesture() : (setActivity("Gesture Control"),g.setMessage("Pair the tablet to enable Gesture Control."))}
                >
                  {connectedTablet
                    ? connectedTablet.authority
                      ? "Gesture control active"
                      : "Gesture connected — waiting for control"
                    : "Connect gesture control"}
                </button>
                {activity === "Drive" && (
                  <>
                    <h3>DRIVE & TEACH</h3>
                    <p className="helper">
                      Hold WASD or a direction button. Release to stop.
                    </p>
                    <div className="drive-pad">
                      {[
                        ["↑", 0.08, 0],
                        ["←", 0, 0.4],
                        ["↓", -0.06, 0],
                        ["→", 0, -0.4],
                      ].map(([label, v, w]) => (
                        <button
                          key={label}
                          disabled={!armed || source !== "keyboard"}
                          onPointerDown={(e) => {
                            e.currentTarget.setPointerCapture(e.pointerId);
                            heldDrive(Number(v), Number(w));
                          }}
                          onPointerUp={releaseDrive}
                          onPointerCancel={releaseDrive}
                          onKeyDown={(e) => {
                            if (
                              (e.key === "Enter" || e.key === " ") &&
                              !e.repeat
                            )
                              heldDrive(Number(v), Number(w));
                          }}
                          onKeyUp={releaseDrive}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    <button
                      className="control"
                      disabled={!t?.pose || t.pose.frame !== "map"}
                      onClick={() => {
                        if (t?.pose) {
                          if (routeMapId && routeMapId !== t.map_id) {
                            g.setMessage("Start a new route for this map.");
                            return;
                          }
                          setRouteMapId(t.map_id);
                          setPoints((v) => [
                            ...v,
                            {
                              x: t.pose!.x,
                              y: t.pose!.y,
                              yaw: t.pose!.yaw,
                              dwell: 0,
                              name: `Checkpoint ${v.length + 1}`,
                            },
                          ]);
                        }
                      }}
                    >
                      <Flag size={16} /> Record current checkpoint
                    </button>
                    <p>{points.length} checkpoints recorded</p>
                    <p className="helper">
                      EvoFox: left stick is axes 0/1. RT is axis 5 and acts as
                      throttle; press it farther to accelerate. LT is ignored.
                      Press Y to stamp a checkpoint. Releasing RT stops the robot.
                    </p>
                    <button
                      className="primary"
                      onClick={() => switchActivity("Missions")}
                    >
                      REVIEW ROUTE
                    </button>
                  </>
                )}
                {activity === "Explore" && (
                  <>
                    <h3>AUTOMATIC EXPLORATION</h3>
                    <p className="helper">
                      Choose reachable frontiers in the live SLAM map.
                      Unreachable targets are skipped; the run ends when no
                      reachable frontier remains.
                    </p>
                    <button
                      className="primary"
                      onClick={() =>
                        void perform({ action: "explore", source: "mission" })
                      }
                    >
                      <Radar size={17} /> START EXPLORING
                    </button>
                    <label className="mc-label">
                      Map name
                      <input
                        value={mapName}
                        onChange={(e) => setMapName(e.target.value)}
                      />
                    </label>
                    <button
                      className="control"
                      onClick={() =>
                        void refreshAfter({ action: "map_save", name: mapName })
                      }
                    >
                      <Save size={16} /> Save map locally
                    </button>
                    <p className="helper">{g.library.maps.length} maps saved</p>
                    {g.library.maps.map((m) => (
                      <button
                        className="control"
                        key={m.id}
                        onClick={() =>
                          void perform({ action: "map_load", name: m.id })
                        }
                      >
                        Load {m.name}
                      </button>
                    ))}
                  </>
                )}
                {activity === "Missions" && (
                  <>
                    <label className="mc-label">
                      Mission name
                      <input
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                      />
                    </label>
                    <button
                      className="control"
                      onClick={() => {
                        setPoints([]);
                        setRouteMapId(t?.map_id || "");
                        setName("New route");
                        setRoutePlan(null);
                        setPlannedFingerprint("");
                      }}
                    >
                      New route
                    </button>
                    <p className="helper">
                      Map: {routeMapId || t?.map_id || "unavailable"}. Click
                      free map space to add a checkpoint.
                    </p>
                    <div className="mc-points">
                      {points.map((p, i) => (
                        <div key={i}>
                          <strong>{i + 1}.</strong>
                          <input
                            aria-label={`Checkpoint ${i + 1} name`}
                            value={p.name || ""}
                            placeholder={`Checkpoint ${i + 1}`}
                            onChange={(e) =>
                              setPoints((v) =>
                                v.map((p, j) =>
                                  i === j ? { ...p, name: e.target.value } : p,
                                ),
                              )
                            }
                          />
                          <label>
                            Heading °
                            <input
                              type="number"
                              min="-180"
                              max="180"
                              value={Math.round((p.yaw * 180) / Math.PI)}
                              onChange={(e) =>
                                setPoints((v) =>
                                  v.map((p, j) =>
                                    i === j
                                      ? {
                                          ...p,
                                          yaw:
                                            (Math.max(
                                              -180,
                                              Math.min(180, +e.target.value),
                                            ) *
                                              Math.PI) /
                                            180,
                                        }
                                      : p,
                                  ),
                                )
                              }
                            />
                          </label>
                          <label>
                            Wait s
                            <input
                              type="number"
                              min="0"
                              max="60"
                              value={p.dwell || 0}
                              onChange={(e) =>
                                setPoints((v) =>
                                  v.map((p, j) =>
                                    i === j
                                      ? {
                                          ...p,
                                          dwell: Math.max(
                                            0,
                                            Math.min(60, +e.target.value),
                                          ),
                                        }
                                      : p,
                                  ),
                                )
                              }
                            />
                          </label>
                          <button
                            aria-label={`Move checkpoint ${i + 1} up`}
                            disabled={i === 0}
                            onClick={() =>
                              setPoints((v) => {
                                const a = [...v];
                                [a[i - 1], a[i]] = [a[i], a[i - 1]];
                                return a;
                              })
                            }
                          >
                            ↑
                          </button>
                          <button
                            aria-label={`Remove checkpoint ${i + 1}`}
                            onClick={() =>
                              setPoints((v) => v.filter((_, j) => j !== i))
                            }
                          >
                            ×
                          </button>
                        </div>
                      ))}
                    </div>
                    <label className="return-toggle">
                      Return home
                      <Switch
                        checked={returnHome}
                        onCheckedChange={setReturnHome}
                      />
                    </label>
                    <button
                      className="control"
                      disabled={!points.length}
                      onClick={() => void validateRoute()}
                    >
                      <Crosshair size={15} /> VALIDATE & PREVIEW
                    </button>
                    {routePlan && (
                      <div className={routePlan.valid ? "route-ok" : "route-bad"}>
                        <strong>{routePlan.valid ? "PATH SAFE" : "PATH REJECTED"}</strong>
                        <span>{routePlan.distance_m.toFixed(2)} m · {Math.ceil(routePlan.eta_seconds)} s</span>
                        {routePlan.errors.map((error) => <small key={`${error.checkpoint}-${error.message}`}>Point {error.checkpoint}: {error.message}</small>)}
                      </div>
                    )}
                    <button
                      className="control"
                      disabled={!points.length}
                      onClick={() => void saveMission()}
                    >
                      <Save size={15} /> Save route
                    </button>
                    <button
                      className="primary"
                      disabled={!routePlan?.valid || plannedFingerprint !== pointFingerprint}
                      onClick={() =>
                        void perform({
                          action: "mission",
                          source: "mission",
                          mission,
                        })
                      }
                    >
                      <Play size={16} /> DEPLOY ROUTE
                    </button>
                    {plannedFingerprint !== pointFingerprint && points.length > 0 && (
                      <p className="helper">Route changed. Validate it again before deployment.</p>
                    )}
                    {t?.state === "blocked" && (
                      <div className="blocked-actions">
                        <strong>PATH STILL BLOCKED</strong>
                        <button onClick={() => void perform({ action: "retry", source: "mission" })}><RotateCcw/> Retry</button>
                        <button onClick={() => void perform({ action: "skip", source: "mission" })}><SkipForward/> Skip</button>
                        <button onClick={() => void perform({ action: "cancel", source: "mission" })}><XCircle/> Cancel</button>
                      </div>
                    )}
                    <label className="mc-label">
                      Saved routes
                      <select
                        defaultValue=""
                        onChange={(e) => {
                          const m = g.library.missions.find(
                            (m) => m.id === e.target.value,
                          );
                          if (m) {
                            setRouteMapId(m.map_id);
                            setName(m.name);
                            setPoints(m.points);
                            setReturnHome(m.return_home);
                            setRoutePlan(null);
                            setPlannedFingerprint("");
                            if (m.map_id !== t?.map_id)
                              g.setMessage(
                                "This route belongs to " +
                                  m.map_id +
                                  ". Load that map before deploying.",
                              );
                          }
                        }}
                      >
                        <option value="">Choose a saved route</option>
                        {g.library.missions.map((m) => (
                          <option key={m.id} value={m.id}>
                            {m.name} · {m.map_id}
                          </option>
                        ))}
                      </select>
                    </label>
                  </>
                )}
                {activity === "Tests" && (
                  <>
                    <h3>ODOMETRY TESTS</h3>
                    <label className="mc-label">
                      Distance (cm, negative = reverse)
                      <input
                        type="number"
                        min="-100"
                        max="100"
                        value={distance}
                        onChange={(e) => setDistance(+e.target.value)}
                      />
                    </label>
                    <button
                      className="primary"
                      onClick={() =>
                        void perform({ action: "distance", value: distance })
                      }
                    >
                      RUN DISTANCE TEST
                    </button>
                    <label className="mc-label">
                      Angle (°, positive = left)
                      <input
                        type="number"
                        min="-180"
                        max="180"
                        value={angle}
                        onChange={(e) => setAngle(+e.target.value)}
                      />
                    </label>
                    <button
                      className="control"
                      onClick={() =>
                        void perform({ action: "angle", value: angle })
                      }
                    >
                      Run turn test
                    </button>
                    <p className="helper">
                      Measured from odometry; requires live scan and odometry
                      updates.
                    </p>
                  </>
                )}
                {activity === "Gesture Control" && (
                  <>
                    <h3>GESTURE CONTROL</h3>
                    <button className="primary" onClick={() => void createPairing()}>
                      <Tablet size={16}/> NEW PAIRING CODE
                    </button>
                    <p className="helper">Four stable frames and 75% confidence are required. Center, outside the pad, camera loss, or disconnect stops movement.</p>
                    <button className="control" onClick={()=>void toggleDirectServo(!directServo.enabled)}>{directServo.enabled ? "Disconnect servo" : "Connect USB adapter"}</button>
                  </>
                )}
                {activity === "Calibration" && hardware && (
                  <>
                    <h3>ROBOT GEOMETRY</h3>
                    {([
                      ['wheel_circumference_m','Wheel circumference (m)'],
                      ['wheel_separation_m','Wheel separation (m)'],
                      ['footprint_length_m','Footprint length (m)'],
                      ['footprint_width_m','Footprint width (m)'],
                      ['lidar_x_m','LiDAR X offset (m)'],
                      ['lidar_y_m','LiDAR Y offset (m)'],
                      ['imu_x_m','IMU X offset (m)'],
                      ['imu_y_m','IMU Y offset (m)'],
                      ['encoder_scale','Encoder calibration scale'],
                    ] as [keyof HardwareProfile,string][]).map(([key,label]) => (
                      <label className="mc-label" key={key}>{label}<input type="number" step="0.001" value={hardware[key] == null ? '' : String(hardware[key])} onChange={(e)=>setHardware({...hardware,[key]:e.target.value===''?null:Number(e.target.value)})}/></label>
                    ))}
                    <button className="primary" onClick={() => void saveHardware()}>SAVE HARDWARE PROFILE</button>
                    <h3>GAMEPAD CALIBRATION</h3>
                    <p className="helper">{padSnapshot ? padSnapshot.id : 'Connect the EvoFox gamepad. No button indices are assumed.'}</p>
                    <button className="control" disabled={!padSnapshot} onClick={()=>setCalibratingPad(v=>!v)}>{calibratingPad?'Hide live input':'Show live axes & buttons'}</button>
                    {calibratingPad && padSnapshot && <>
                      <pre>{JSON.stringify(padSnapshot,null,2)}</pre>
                      <div className="pad-mapping">
                        {(Object.entries(padMap) as [keyof typeof padMap,number][]).map(([key,value])=><label key={key}>{key}<input type="number" min="0" max={key.startsWith('axis')?Math.max(0,padSnapshot.axes.length-1):Math.max(0,padSnapshot.buttons.length-1)} value={value} onChange={(e)=>setPadMap({...padMap,[key]:Math.max(0,Number(e.target.value))})}/></label>)}
                      </div>
                      <small>Press each physical control and watch the live arrays. A pressed button is true; moved axes change away from zero.</small>
                    </>}
                    {padSnapshot && <button className="control" onClick={()=>{localStorage.setItem(`tbot-gamepad-v2:${padSnapshot.id}`,JSON.stringify(padMap));g.setMessage('Gamepad mapping saved for this controller ID.')}}>SAVE CURRENT EVOFOX MAPPING</button>}
                  </>
                )}
                {activity === "Diagnostics" && (
                  <>
                    <h3>HARDWARE STATUS</h3>
                    <p className="helper">Profile: ST3215-HS IDs {hardware?.right_motor_id}/{hardware?.left_motor_id} · {hardware?.serial_device}</p>
                    <button className="control" onClick={() => void refreshSystem()}>REFRESH ALL CHECKS</button>
                    <button className="control" onClick={() => setTechnical(true)}>OPEN RAW TECHNICAL PANEL</button>
                  </>
                )}
                {activity === "Code Runner" && (
                  <>
                    <h3>VS CODE WORKSPACE</h3>
                    <label className="mc-label">Folder on this computer<input value={runnerRoot} onChange={(e)=>setRunnerRoot(e.target.value)} placeholder="/home/apex/Documents/project"/></label>
                    <button className="control" onClick={()=>void saveRunnerRoot()}>USE THIS FOLDER</button>
                    <button className="control" onClick={()=>void refreshRunner()}>REFRESH PYTHON FILES</button>
                    <p className="helper">{runnerFiles.length} Python files available. The runner executes one file at a time, without a shell.</p>
                  </>
                )}
                <div className="home-controls">
                  <button
                    onClick={() => void refreshAfter({ action: "home_set" })}
                  >
                    <Flag size={15} /> Set Home
                  </button>
                  <button onClick={() => void perform({ action: "home_go" })}>
                    <Home size={15} /> Return
                  </button>
                </div>
                <div className="home-controls">
                  <button onClick={() => void perform({ action: "pause" })}>
                    <Pause size={15} /> Pause
                  </button>
                  <button onClick={() => void perform({ action: "resume" })}>
                    <Play size={15} /> Resume
                  </button>
                </div>
                <p className="helper">
                  {t?.checkpoint_count
                    ? `Checkpoint ${Math.min(t.checkpoint + 1, t.checkpoint_count)} of ${t.checkpoint_count}`
                    : "No active mission"}
                </p>
                <p className="helper">
                  Person following is planned; camera target tracking is not
                  connected yet.
                </p>
              </aside>
            </div>
          </section>
        )}
        <div className="mode-heading">
          <h2>CHOOSE YOUR NEXT MOVE</h2>
          <span>ONE ROBOT. ONE ACTIVE DRIVER.</span>
        </div>
        <nav className="mode-cards" aria-label="Activities">
          {activities.map((a) => (
            <button
              className={"mode-card " + (activity === a.name ? "active" : "")}
              key={a.name}
              onClick={() => switchActivity(a.name)}
            >
              <div className="card-top">
                <a.icon size={26} />
              </div>
              <h3>{a.name}</h3>
              <small>{a.caption}</small>
            </button>
          ))}
        </nav>
        <section className="scrapbook">
          <div>
            <span className="micro">BUILT BY HUMANS. ALLEGEDLY.</span>
            <h3>The origin story</h3>
          </div>
          <div className="photo-strip">
            {g.library.photos.map((p) => (
              <img
                key={p.id}
                src={`${API}/photos/${p.id}?token=${encodeURIComponent(g.token)}`}
                alt="Team robot build"
              />
            ))}
            {!g.library.photos.length && (
              <p>Build photos now stay saved on this laptop.</p>
            )}
          </div>
          <input
            ref={photo}
            type="file"
            accept="image/png,image/jpeg"
            multiple
            hidden
            onChange={async (e) => {
              try {
                for (const f of Array.from(e.target.files || []))
                  await g.request("/photos", {
                    method: "POST",
                    headers: { "Content-Type": f.type },
                    body: f,
                  });
                await g.refresh();
              } catch (e) {
                g.setMessage(String(e));
              }
            }}
          />
          <button className="photo-add" onClick={() => photo.current?.click()}>
            <ImagePlus size={18} /> Add photos
          </button>
        </section>
      </main>
      <footer className="cp-footer">
        <span>
          <Gamepad2 size={17} />
          {padStatus}
        </span>
        <span>
          Right stick = navigate · X = select · B = back · Y = emergency stop
        </span>
        <button className="mc-link" onClick={() => setTechnical(true)}>
          Technical panel ↗
        </button>
      </footer>
    </div>
  );
}

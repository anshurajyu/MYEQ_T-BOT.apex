"use client";
import { useCallback, useEffect, useRef, useState } from "react";
export type Pose = {
  x: number;
  y: number;
  yaw: number;
  dwell?: number;
  name?: string;
  frame?: string;
};
export type Grid = {
  width: number;
  height: number;
  resolution: number;
  origin: Pose;
  data: number[];
  frame: string;
  stamp: number;
};
export type Mission = {
  id?: string;
  name: string;
  map_id: string;
  points: Pose[];
  return_home: boolean;
  map_version?: string;
};
export type HardwareProfile = {
  wheel_circumference_m: number;
  wheel_separation_m: number | null;
  footprint_length_m: number | null;
  footprint_width_m: number | null;
  lidar_x_m: number | null;
  lidar_y_m: number | null;
  imu_x_m: number | null;
  imu_y_m: number | null;
  encoder_scale: number | null;
  serial_device: string;
  right_motor_id: number;
  left_motor_id: number;
  right_sign: 1 | -1;
  left_sign: 1 | -1;
  raw_speed_limit: number;
  max_linear_mps: number;
  max_angular_rps: number;
  clearance_m: number;
};
export type ReadinessReport = {
  ready: boolean;
  physical_lock: boolean;
  checks: Record<string, boolean>;
  missing_calibration: string[];
  profile: HardwareProfile;
};
export type PathPlan = {
  valid: boolean;
  map_version: string;
  segments: { checkpoint: number; poses: Pose[]; distance_m: number; snapped_goal: Pose }[];
  errors: { checkpoint: number; message: string }[];
  distance_m: number;
  eta_seconds: number;
};
export type DeviceSession = {
  id: string;
  role: "cockpit" | "tablet";
  connected: boolean;
  authority: boolean;
  camera: boolean;
  gesture: string;
  confidence: number;
  x: number;
  y: number;
  fps: number;
  latency_ms: number;
  heartbeat_age: number;
};
export type Telemetry = {
  mode: string;
  requested_mode?: "simulation" | "hardware";
  scenario?: string;
  state: string;
  status: string;
  source: string | null;
  map_id: string;
  home: Pose | null;
  pose: Pose | null;
  map: Grid | null;
  scan: { points: number[][]; error?: string } | null;
  scan_age: number | null;
  odom_age: number | null;
  navigation_ready: boolean;
  guard_ready?: boolean;
  error?: string;
  checkpoint: number;
  checkpoint_count: number;
  topics: Record<string, unknown>;
  odom?: { linear: number; angular: number };
  battery?: { voltage: number; percent: number } | null;
  coverage?: number;
  distance_total?: number;
  readiness?: ReadinessReport;
  diagnostics?: Record<string, Record<string, unknown>>;
};
export type Library = {
  missions: Mission[];
  maps: { id: string; name: string; grid: Grid }[];
  runs: Record<string, unknown>[];
  photos: { id: string }[];
  settings: Record<string, unknown>;
  events: { timestamp: number; kind: string; message: string }[];
  firebase: Record<string, string>;
};
const empty: Library = {
  missions: [],
  maps: [],
  runs: [],
  photos: [],
  settings: {},
  events: [],
  firebase: {},
};
const configuredApi = process.env.NEXT_PUBLIC_TBOT_API;
export const API =
  configuredApi ||
  (typeof window !== "undefined" && !["localhost", "127.0.0.1"].includes(window.location.hostname)
    ? `${window.location.origin}/api`
    : "http://127.0.0.1:8001");
export function useGateway(role: "cockpit" | "tablet" = "cockpit", pair = "") {
  const [connected, setConnected] = useState(false),
    [telemetry, setTelemetry] = useState<Telemetry | null>(null),
    [library, setLibrary] = useState<Library>(empty),
    [message, setMessage] = useState("Connecting to local gateway…"),
    [token, setToken] = useState("");
  const ws = useRef<WebSocket | null>(null),
    pending = useRef(
      new Map<
        string,
        {
          resolve: (v: unknown) => void;
          reject: (e: Error) => void;
          timer: ReturnType<typeof setTimeout>;
        }
      >(),
    );
  const request = useCallback(
    async (path: string, options: RequestInit = {}) => {
      if (!token) throw Error("Gateway disconnected");
      const res = await fetch(API + path, {
        ...options,
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer " + token,
          ...options.headers,
        },
      });
      const data = (await res.json()) as any;
      if (!res.ok)
        throw Error(
          typeof data.detail === "string"
            ? data.detail
            : JSON.stringify(data.detail),
        );
      return data;
    },
    [token],
  );
  const refresh = useCallback(async () => {
    try {
      setLibrary(await request("/library"));
    } catch (e) {
      setMessage(String(e));
    }
  }, [request]);
  useEffect(() => {
    let dead = false,
      socket: WebSocket | null = null,
      retry: ReturnType<typeof setTimeout>,
      heartbeat: ReturnType<typeof setInterval>;
    const connect = async () => {
      try {
        const query = new URLSearchParams({ role });
        if (pair) query.set("pair", pair);
        const res = await fetch(`${API}/session?${query}`, { method: "POST" });
        if (!res.ok) {
          const failure = await res.json().catch(() => ({})) as {detail?:string};
          throw Error(failure.detail || "Local session rejected");
        }
        const { token: t } = (await res.json()) as { token: string };
        if (dead) return;
        setToken(t);
        if (role === "tablet") {
          setConnected(true);
          setMessage("Tablet command link connected.");
          heartbeat = setInterval(async () => {
            try {
              const response=await fetch(`${API}/heartbeat`,{method:"POST",headers:{Authorization:"Bearer "+t}});
              if(!response.ok)throw Error("Tablet heartbeat rejected");
            } catch {
              clearInterval(heartbeat);setConnected(false);setToken("");
              if(!dead)retry=setTimeout(connect,1000);
            }
          },500);
          return;
        }
        socket = new WebSocket(API.replace(/^http/, "ws") + "/ws");
        ws.current = socket;
        socket.onopen = () => {
          socket?.send(JSON.stringify({ token: t }));
          setConnected(true);
          setMessage(
            "Gateway connected. Robot movement requires live sensors.",
          );
          heartbeat = setInterval(() => {
            if (socket?.readyState === WebSocket.OPEN)
              socket.send(JSON.stringify({ type: "heartbeat" }));
          }, 200);
        };
        socket.onmessage = (e) => {
          const data = JSON.parse(e.data);
          if (data.type === "telemetry")
            setTelemetry((previous) => ({ ...previous, ...data }));
          if (data.type === "ack") {
            const p = pending.current.get(data.id);
            if (p) {
              clearTimeout(p.timer);
              pending.current.delete(data.id);
              if (data.ok) p.resolve(data);
              else p.reject(Error(data.error));
            }
            setMessage(data.ok ? `${data.action} acknowledged` : data.error);
          }
        };
        socket.onclose = () => {
          clearInterval(heartbeat);
          setConnected(false);
          setToken("");
          setTelemetry(null);
          for (const p of pending.current.values()) {
            clearTimeout(p.timer);
            p.reject(Error("Control connection lost"));
          }
          pending.current.clear();
          if (!dead) {
            setMessage(
              "Gateway disconnected. Reconnecting without replaying commands.",
            );
            retry = setTimeout(connect, 2500);
          }
        };
        socket.onerror = () => socket?.close();
      } catch (error) {
        if (!dead) {
          setConnected(false);
          setMessage(error instanceof Error ? error.message : "Gateway connection failed");
          retry = setTimeout(connect, 2500);
        }
      }
    };
    connect();
    return () => {
      dead = true;
      clearTimeout(retry);
      clearInterval(heartbeat);
      socket?.close();
    };
  }, [role, pair]);
  useEffect(() => {
    if (token) refresh();
  }, [token, refresh]);
  const send = useCallback(
    (command: Record<string, unknown>) => {
      if(role==="tablet") return (async()=>{
        if(!token)throw Error("Tablet command link disconnected");
        const id=crypto.randomUUID();
        const response=await fetch(`${API}/command`,{method:"POST",headers:{"Content-Type":"application/json",Authorization:"Bearer "+token},body:JSON.stringify({...command,id})});
        const data=await response.json() as {detail?:string;action?:string;[key:string]:unknown};
        if(!response.ok)throw Error(data.detail||"Command rejected");
        setMessage(`${data.action} acknowledged`);return data;
      })();
      return new Promise((resolve, reject) => {
        const socket = ws.current;
        if (!socket || socket.readyState !== WebSocket.OPEN) {
          reject(Error("Gateway disconnected"));
          return;
        }
        const id = crypto.randomUUID();
        const timer = setTimeout(() => {
          pending.current.delete(id);
          reject(Error("Command acknowledgement timed out"));
        }, 2500);
        pending.current.set(id, { resolve, reject, timer });
        socket.send(JSON.stringify({ ...command, id }));
      });
    },
    [role,token],
  );
  const sendDeviceState = useCallback((state: Record<string, unknown>) => {
    if(role==="tablet"&&token){
      void fetch(`${API}/device-state`,{method:"POST",headers:{"Content-Type":"application/json",Authorization:"Bearer "+token},body:JSON.stringify(state)}).catch(()=>{});return;
    }
    const socket = ws.current;
    if (socket?.readyState === WebSocket.OPEN)
      socket.send(JSON.stringify({ type: "device_state", ...state }));
  }, [role,token]);
  return {
    connected,
    telemetry,
    library,
    token,
    message,
    setMessage,
    request,
    refresh,
    send,
    sendDeviceState,
  };
}

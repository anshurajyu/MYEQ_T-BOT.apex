"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { HandLandmarker } from "@mediapipe/tasks-vision";
import { Hand, Radio, Square, Video, Wifi } from "lucide-react";
import { useGateway } from "@/lib/tbot/client";
import { createTabletDirectionFilter, type TabletDirection } from "@/lib/tbot/tablet-gesture";
import { createTabletControl } from "@/lib/tbot/tablet-control";

export function TabletController() {
  const pair = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("pair") || "" : "";
  const g = useGateway("tablet", pair);
  const gateway = useRef(g);
  gateway.current = g;
  const mounted = useRef(true), video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null), detector = useRef<HandLandmarker | null>(null);
  const frame = useRef(0), cameraAttempt = useRef(0), starting = useRef(false);
  const [camera, setCamera] = useState(false), [startingCamera, setStartingCamera] = useState(false);
  const [controlState, setControlState] = useState({ armed: false, busy: false });
  const [direction, setDirection] = useState<TabletDirection>("stop"), [point, setPoint] = useState({ x: .5, y: .5 });
  const [confidence, setConfidence] = useState(0), [fps, setFps] = useState(0);
  const control = useRef<ReturnType<typeof createTabletControl> | null>(null);
  if (!control.current) {
    control.current = createTabletControl(
      () => gateway.current,
      (state) => { if (mounted.current) setControlState(state); },
      (text) => { if (mounted.current) gateway.current.setMessage(text); },
    );
  }

  const stop = useCallback(() => {
    void control.current!.stop();
    gateway.current.sendDeviceState({ gesture: "stop", confidence: 0 });
    if (mounted.current) setDirection("stop");
  }, []);

  const closeCamera = useCallback(() => {
    cameraAttempt.current++;
    starting.current = false;
    cancelAnimationFrame(frame.current);
    control.current!.setCamera(false);
    const currentStream = stream.current;
    stream.current = null;
    currentStream?.getTracks().forEach((track) => { track.onended = null; track.stop(); });
    const currentDetector = detector.current;
    detector.current = null;
    try { currentDetector?.close(); } catch { /* Motion is already disarmed. */ }
    gateway.current.sendDeviceState({ camera: false, gesture: "stop", confidence: 0, x: .5, y: .5, fps: 0 });
    if (mounted.current) {
      setCamera(false); setStartingCamera(false); setDirection("stop");
      setPoint({ x: .5, y: .5 }); setConfidence(0); setFps(0);
    }
  }, []);

  const start = async () => {
    if (starting.current || stream.current) return;
    starting.current = true;
    setStartingCamera(true);
    const attempt = ++cameraAttempt.current;
    const current = () => mounted.current && attempt === cameraAttempt.current;
    let newDetector: HandLandmarker | null = null, newStream: MediaStream | null = null;
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia)
        throw Error("The phone camera needs HTTPS. Open the private Tailscale link; an http://192.168… link can pair but cannot use the camera.");
      const { FilesetResolver, HandLandmarker } = await import("@mediapipe/tasks-vision");
      const files = await FilesetResolver.forVisionTasks("/mediapipe/wasm");
      if (!current()) return;
      newDetector = await HandLandmarker.createFromOptions(files, {
        baseOptions: { modelAssetPath: "/models/hand_landmarker.task", delegate: "CPU" },
        runningMode: "VIDEO", numHands: 1, minHandDetectionConfidence: .75, minTrackingConfidence: .75,
      });
      if (!current()) { newDetector.close(); return; }
      newStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 960 }, height: { ideal: 720 } }, audio: false });
      if (!current()) { newStream.getTracks().forEach((track) => track.stop()); newDetector.close(); return; }
      if (!video.current) throw Error("Camera preview unavailable");
      detector.current = newDetector;
      newDetector = null;
      stream.current = newStream;
      const activeStream = newStream;
      newStream = null;
      activeStream.getVideoTracks().forEach((track) => {
        track.onended = () => { closeCamera(); gateway.current.setMessage("Camera stopped. Restart it and request control again."); };
      });
      video.current.srcObject = activeStream;
      await video.current.play();
      if (!current()) return;
      setCamera(true);
      control.current!.setCamera(true);
      control.current!.setVisible(!document.hidden);
      gateway.current.setMessage("Camera ready. Center your finger, then request gesture control.");
      let previous = performance.now(), lastSent = 0, lastVideoTime = -1, lastFreshFrame = previous;
      const directionFilter = createTabletDirectionFilter();
      const tick = (now: number) => {
        if (!current() || !detector.current || !video.current || !stream.current) return;
        try {
          if (document.hidden) { directionFilter.reset(); lastFreshFrame = now; }
          else if (now - lastSent >= 100 && video.current.readyState >= 2 && video.current.currentTime !== lastVideoTime) {
            lastVideoTime = video.current.currentTime;
            lastFreshFrame = now;
            const result = detector.current.detectForVideo(video.current, now);
            const landmarks = result.landmarks[0], score = result.handedness[0]?.[0]?.score || 0;
            let x = .5, y = .5;
            if (landmarks && score >= .75) { x = 1 - landmarks[8].x; y = landmarks[8].y; }
            const valid = directionFilter.update(x, y, landmarks ? score : 0, now), currentFps = Math.round(1000 / Math.max(1, now - previous));
            setPoint({ x, y }); setConfidence(score); setDirection(valid); setFps(currentFps);
            previous = now;
            control.current!.frame(valid, { camera: true, gesture: valid, confidence: score, x, y, fps: currentFps });
            lastSent = now;
          } else if (now - lastFreshFrame > 750) {
            throw Error("Camera frames stopped arriving. Restart the camera and request control again.");
          }
          frame.current = requestAnimationFrame(tick);
        } catch (error) {
          closeCamera();
          gateway.current.setMessage(String(error));
        }
      };
      frame.current = requestAnimationFrame(tick);
    } catch (error) {
      // Also dispose resources created before they were attached to the preview.
      newStream?.getTracks().forEach((track) => track.stop());
      newDetector?.close();
      if (current()) { closeCamera(); gateway.current.setMessage(String(error)); }
    } finally {
      if (current()) { starting.current = false; setStartingCamera(false); }
    }
  };

  useEffect(() => { control.current!.syncConnection(); }, [g.connected, g.token]);
  useEffect(() => { control.current!.observeAuthority(g.authority); }, [g.authority]);
  useEffect(() => {
    mounted.current = true;
    const visibility = () => {
      control.current!.setVisible(!document.hidden);
      if (document.hidden) {
        setDirection("stop");
        gateway.current.sendDeviceState({ gesture: "stop", confidence: 0 });
      }
    };
    const leaving = () => closeCamera();
    document.addEventListener("visibilitychange", visibility);
    window.addEventListener("pagehide", leaving);
    window.addEventListener("orientationchange", leaving);
    return () => {
      mounted.current = false;
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener("pagehide", leaving);
      window.removeEventListener("orientationchange", leaving);
      closeCamera();
    };
  }, [closeCamera]);

  return <main className="tablet-shell">
    <header><div><b>T—BOT</b><small>GESTURE STICK / TABLET</small></div><span className={g.connected ? "online" : "offline"}><Wifi/> {g.connected ? "PAIRED" : "DISCONNECTED"}</span></header>
    <section className="tablet-stage">
      <video ref={video} playsInline muted/>
      <div className="stick" aria-label="Index finger virtual joystick"><i className="north">FORWARD</i><i className="south">BACK</i><i className="west">LEFT</i><i className="east">RIGHT</i><i className="dead">STOP</i><span style={{ left: `${point.x * 100}%`, top: `${point.y * 100}%` }}/></div>
      {!camera && <div className="camera-empty"><Hand/><h1>Your finger is the joystick.</h1><p>The camera stays on this tablet. Only direction and confidence leave it.</p><button disabled={startingCamera} onClick={() => void start()}><Video/> {startingCamera ? "STARTING CAMERA…" : "START CAMERA"}</button></div>}
      {camera && <div className={`gesture-readout ${direction}`}><small>{controlState.armed ? "COMMAND" : "GESTURE · CONTROL DISARMED"}</small><strong>{direction.toUpperCase()}</strong><span>{Math.round(confidence * 100)}% · {fps} FPS</span></div>}
    </section>
    <section className="tablet-controls"><button disabled={!camera || !g.connected || controlState.armed || controlState.busy} onClick={() => void control.current!.claim()}>{controlState.armed ? <><Radio/> GESTURE AUTHORITY ACTIVE</> : controlState.busy ? "WAITING FOR COMMAND…" : "REQUEST GESTURE CONTROL"}</button><button className="tablet-stop" onClick={stop}><Square fill="currentColor"/> STOP NOW</button></section>
    <p className="tablet-message">{g.message}</p>
  </main>;
}

"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { HandLandmarker } from "@mediapipe/tasks-vision";
import { Hand, Radio, Square, Video, Wifi } from "lucide-react";
import { useGateway } from "@/lib/tbot/client";
import { classifyTabletZone, stableTabletDirection, type TabletDirection } from "@/lib/tbot/tablet-gesture";

type Direction = TabletDirection;
const motion: Record<Direction, [number, number]> = {
  forward: [.06, 0], backward: [-.045, 0], left: [0, .35], right: [0, -.35], stop: [0, 0],
};

export function TabletController(){
  const pair=typeof window!=="undefined"?new URLSearchParams(window.location.search).get("pair")||"":"";
  const g=useGateway("tablet",pair),video=useRef<HTMLVideoElement>(null),stream=useRef<MediaStream|null>(null),detector=useRef<HandLandmarker|null>(null),frame=useRef(0),lastSent=useRef(0);
  const [camera,setCamera]=useState(false),[armed,setArmed]=useState(false),[direction,setDirection]=useState<Direction>("stop"),[point,setPoint]=useState({x:.5,y:.5}),[confidence,setConfidence]=useState(0),[fps,setFps]=useState(0);
  const stop=useCallback(()=>{setArmed(false);setDirection("stop");void g.send({action:"stop",source:"gesture"}).catch(()=>{});},[g.send]);
  const stopRef=useRef(stop);stopRef.current=stop;
  const closeCamera=useCallback(()=>{cancelAnimationFrame(frame.current);stream.current?.getTracks().forEach(t=>t.stop());stream.current=null;detector.current?.close();detector.current=null;setCamera(false);setPoint({x:.5,y:.5});setConfidence(0);g.sendDeviceState({camera:false,gesture:"stop",confidence:0,x:.5,y:.5,fps:0});stop();},[g.sendDeviceState,stop]);
  const start=async()=>{
    try{
      if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia)throw Error("Tablet camera needs the private HTTPS Tailscale link. The http://192.168… test link can pair, but browsers block its camera.");
      const {FilesetResolver,HandLandmarker}=await import("@mediapipe/tasks-vision");
      const files=await FilesetResolver.forVisionTasks("/mediapipe/wasm");
      detector.current=await HandLandmarker.createFromOptions(files,{baseOptions:{modelAssetPath:"/models/hand_landmarker.task",delegate:"CPU"},runningMode:"VIDEO",numHands:1,minHandDetectionConfidence:.75,minTrackingConfidence:.75});
      stream.current=await navigator.mediaDevices.getUserMedia({video:{facingMode:"user",width:{ideal:960},height:{ideal:720}},audio:false});
      if(!video.current)throw Error("Camera preview unavailable");video.current.srcObject=stream.current;await video.current.play();setCamera(true);
      let previous=performance.now(),candidate:Direction="stop",stable=0;
      const tick=(now:number)=>{
        if(!detector.current||!video.current||!stream.current)return;
        if(now-lastSent.current>=100&&video.current.readyState>=2){
          const result=detector.current.detectForVideo(video.current,now),landmarks=result.landmarks[0],score=result.handedness[0]?.[0]?.score||0;
          let next:Direction="stop",x=.5,y=.5;
          if(landmarks&&score>=.75){x=1-landmarks[8].x;y=landmarks[8].y;next=classifyTabletZone(x,y)}
          const filtered=stableTabletDirection(candidate,stable,next);candidate=filtered.candidate;stable=filtered.frames;
          const valid=filtered.output,currentFps=Math.round(1000/Math.max(1,now-previous));setPoint({x,y});setConfidence(score);setDirection(valid);setFps(currentFps);previous=now;
          g.sendDeviceState({camera:true,gesture:valid,confidence:score,x,y,fps:currentFps});
          if(armed){const [linear,angular]=motion[valid];void g.send({action:"drive",source:"gesture",linear,angular}).catch(()=>setArmed(false))}
          lastSent.current=now;
        }
        frame.current=requestAnimationFrame(tick);
      };frame.current=requestAnimationFrame(tick);
    }catch(e){g.setMessage(String(e));closeCamera()}
  };
  const arm=async()=>{try{await g.send({action:"claim",source:"gesture"});setArmed(true)}catch(e){g.setMessage(String(e))}};
  // Camera operation is independent of the gateway. Once both are available,
  // claim gesture authority; a network failure must never close the camera.
  useEffect(()=>{
    if(!camera||!g.connected||armed)return;
    const claim=()=>void g.send({action:"claim",source:"gesture"}).then(()=>setArmed(true)).catch((e)=>g.setMessage(String(e)));
    claim();const timer=setInterval(claim,1000);return()=>clearInterval(timer);
  },[camera,g.connected,armed,g.send,g.setMessage]);
  useEffect(()=>{const hidden=()=>{if(document.hidden)stopRef.current()};document.addEventListener("visibilitychange",hidden);return()=>{document.removeEventListener("visibilitychange",hidden);cancelAnimationFrame(frame.current);stream.current?.getTracks().forEach(t=>t.stop());detector.current?.close();stopRef.current()}},[]);
  useEffect(()=>{if(!g.connected)stop()},[g.connected,stop]);
  return <main className="tablet-shell">
    <header><div><b>T—BOT</b><small>GESTURE STICK / TABLET</small></div><span className={g.connected?"online":"offline"}><Wifi/> {g.connected?"PAIRED":"DISCONNECTED"}</span></header>
    <section className="tablet-stage">
      <video ref={video} playsInline muted/>
      <div className="stick" aria-label="Index finger virtual joystick"><i className="north">FORWARD</i><i className="south">BACK</i><i className="west">LEFT</i><i className="east">RIGHT</i><i className="dead">STOP</i><span style={{left:`${point.x*100}%`,top:`${point.y*100}%`}}/></div>
      {!camera&&<div className="camera-empty"><Hand/><h1>Your finger is the joystick.</h1><p>The camera stays on this tablet. Only direction and confidence leave it.</p><button onClick={()=>void start()}><Video/> START CAMERA</button></div>}
      <div className={`gesture-readout ${direction}`}><small>COMMAND</small><strong>{direction.toUpperCase()}</strong><span>{Math.round(confidence*100)}% · {fps} FPS</span></div>
    </section>
    <section className="tablet-controls"><button disabled={!camera||!g.connected} onClick={()=>void arm()}>{armed?<><Radio/> GESTURE AUTHORITY ACTIVE</>:"REQUEST GESTURE CONTROL"}</button><button className="tablet-stop" onClick={stop}><Square fill="currentColor"/> STOP NOW</button></section>
    <p className="tablet-message">{g.message}</p>
  </main>;
}

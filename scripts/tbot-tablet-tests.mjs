import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { test } from "node:test";
import vm from "node:vm";
import { createTabletControl } from "../lib/tbot/tablet-control.ts";
import { classifyTabletZone, stableTabletDirection, createTabletDirectionFilter } from "../lib/tbot/tablet-gesture.ts";

const flush = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
function harness() {
  const commands = [], telemetry = [], messages = [];
  let state = { armed: false, busy: false }, sendResult = () => Promise.resolve({ ok: true });
  const link = (token, connected = true) => ({
    token, connected,
    send(command) { commands.push({ token, ...command }); return sendResult(command); },
    sendDeviceState(value) { telemetry.push({ token, ...value }); },
  });
  const current = { value: link("session-a") };
  const control = createTabletControl(() => current.value, (value) => { state = value; }, (text) => messages.push(text));
  return { control, current, link, commands, telemetry, messages,
    get state() { return state; },
    setSend(fn) { sendResult = fn; },
  };
}

test("a camera callback created while disarmed drives only after explicit successful claim", async () => {
  const h = harness();
  // The camera retains this same callback across every UI render.
  const cameraTick = () => h.control.frame("forward", { camera: true, gesture: "forward" });
  h.control.setCamera(true);
  cameraTick();
  assert.equal(h.telemetry.length, 1);
  assert.equal(h.commands.length, 0);
  await h.control.claim();
  cameraTick();
  await flush();
  assert.equal(h.state.armed, true);
  assert.deepEqual(h.commands.map(({ action }) => action), ["claim", "drive"]);
  assert.equal(h.commands[1].linear, .06);
});

test("camera started before connection uses the new session and callbacks after reconnect", async () => {
  const h = harness();
  h.current.value = h.link("", false);
  h.control.syncConnection();
  h.control.setCamera(true);
  const cameraTick = () => h.control.frame("left", { gesture: "left" });
  cameraTick();
  assert.equal(h.commands.length, 0);
  h.current.value = h.link("session-b");
  h.control.syncConnection();
  cameraTick();
  assert.equal(h.telemetry.at(-1).token, "session-b");
  assert.equal(h.state.armed, false);
  await h.control.claim();
  cameraTick();
  await flush();
  assert.deepEqual(h.commands.map(({ token }) => token), ["session-b", "session-b"]);
  h.current.value = h.link("session-c");
  cameraTick();
  assert.equal(h.state.armed, false, "a new token must not inherit old movement authority");
  assert.equal(h.commands.length, 2);
  assert.equal(h.telemetry.at(-1).token, "session-c");
  await h.control.claim();
  cameraTick();
  await flush();
  assert.equal(h.commands.at(-1).token, "session-c");
});

for (const interruption of ["stop", "hidden", "disconnect", "camera failure"]) {
  test(`${interruption} disarms and never auto-rearms`, async () => {
    const h = harness();
    h.control.setCamera(true);
    await h.control.claim();
    if (interruption === "stop") h.control.stop();
    if (interruption === "hidden") h.control.setVisible(false);
    if (interruption === "disconnect") { h.current.value = h.link("", false); h.control.syncConnection(); }
    if (interruption === "camera failure") h.control.setCamera(false);
    await flush();
    h.current.value = h.link("session-b");
    h.control.syncConnection();
    h.control.setVisible(true);
    h.control.setCamera(true);
    for (let i = 0; i < 20; i++) h.control.frame("forward", { gesture: "forward" });
    assert.equal(h.state.armed, false);
    assert.equal(h.commands.filter((c) => c.action === "claim").length, 1);
    assert.equal(h.commands.filter((c) => c.action === "drive").length, 0);
  });
}

test("Stop during an in-flight claim ignores its success and releases the canceled session", async () => {
  const h = harness(), claim = deferred();
  h.control.setCamera(true);
  h.setSend((c) => c.action === "claim" ? claim.promise : Promise.resolve());
  const pending = h.control.claim();
  h.control.stop();
  await h.control.claim();
  assert.equal(h.commands.filter((c) => c.action === "claim").length, 1, "no overlapping claims");
  claim.resolve();
  await pending;
  await flush();
  assert.equal(h.state.armed, false);
  assert.deepEqual(h.commands.map((c) => c.action), ["claim", "stop", "release"]);
  h.control.frame("forward", {});
  assert.equal(h.commands.filter((c) => c.action === "drive").length, 0);
});

test("reconnect during claim releases only the old session, then accepts a fresh explicit claim", async () => {
  const h = harness(), claim = deferred();
  h.control.setCamera(true);
  h.setSend((c) => c.action === "claim" ? claim.promise : Promise.resolve());
  const pending = h.control.claim();
  h.current.value = h.link("session-b");
  h.control.syncConnection();
  claim.resolve();
  await pending;
  assert.equal(h.state.armed, false);
  assert.equal(h.commands.at(-1).action, "release");
  assert.equal(h.commands.at(-1).token, "session-a");
  h.setSend(() => Promise.resolve());
  await h.control.claim();
  assert.equal(h.state.armed, true);
  assert.equal(h.commands.at(-1).token, "session-b");
});

test("slow transport has only one movement in flight and does not replay queued gestures", async () => {
  const h = harness(), drive = deferred();
  h.control.setCamera(true);
  await h.control.claim();
  h.setSend((c) => c.action === "drive" ? drive.promise : Promise.resolve());
  h.control.frame("forward", {});
  for (let i = 0; i < 30; i++) h.control.frame("right", {});
  assert.equal(h.commands.filter((c) => c.action === "drive").length, 1);
  drive.resolve();
  await flush();
  assert.equal(h.commands.filter((c) => c.action === "drive").length, 1);
  h.control.frame("stop", {});
  await flush();
  assert.equal(h.commands.at(-1).linear, 0);
  assert.equal(h.commands.at(-1).angular, 0);
});

test("Stop during movement releases a late request without replaying it", async () => {
  const h = harness(), drive = deferred();
  h.control.setCamera(true);
  await h.control.claim();
  h.setSend((c) => c.action === "drive" ? drive.promise : Promise.resolve());
  h.control.frame("forward", {});
  h.control.stop();
  await h.control.claim();
  assert.equal(h.commands.filter((c) => c.action === "claim").length, 1);
  drive.resolve();
  await flush();
  assert.equal(h.state.armed, false);
  assert.equal(h.commands.at(-1).action, "release");
  h.control.frame("forward", {});
  assert.equal(h.commands.filter((c) => c.action === "drive").length, 1);
});

test("rejected movement surfaces its reason and stops without automatically claiming again", async () => {
  const h = harness();
  h.control.setCamera(true);
  await h.control.claim();
  h.setSend((c) => c.action === "drive" ? Promise.reject(Error("Fresh LiDAR required")) : Promise.resolve());
  h.control.frame("forward", {});
  await flush();
  assert.equal(h.state.armed, false);
  assert.ok(h.messages.some((text) => text.includes("Fresh LiDAR required")));
  assert.equal(h.commands.at(-1).action, "release");
});

test("losing ownership releases only the tablet session and never sends a global stop", async () => {
  const h = harness();
  h.control.setCamera(true);
  await h.control.claim();
  h.setSend((c) => c.action === "drive" ? Promise.reject(Error("Take control first")) : Promise.resolve());
  h.control.frame("forward", {});
  await flush();
  assert.equal(h.state.armed, false);
  assert.equal(h.commands.some((c) => c.action === "stop"), false);
  assert.equal(h.commands.at(-1).action, "release");
});

test("unused camera cleanup does not interrupt another control source", async () => {
  const h = harness();
  h.control.setCamera(false);
  h.control.setVisible(false);
  await flush();
  assert.equal(h.commands.length, 0);
});

test("claim requires camera, visible page and a connected session", async () => {
  const h = harness();
  await h.control.claim();
  h.control.setCamera(true);
  h.control.setVisible(false);
  await flush();
  await h.control.claim();
  h.control.setVisible(true);
  h.current.value = h.link("", false);
  await h.control.claim();
  assert.equal(h.commands.filter((c) => c.action === "claim").length, 0);
});

// Execute the production animation callback with controlled camera frames. This
// tests the actual component/helper wiring, not only the classifier in isolation.
function cameraHarness(h) {
  const source = readFileSync(new URL("../components/tablet-controller.tsx", import.meta.url), "utf8");
  const start = source.indexOf("const tick = (now: number) => {");
  const end = source.indexOf("\n      frame.current = requestAnimationFrame(tick);\n    } catch", start);
  assert.ok(start >= 0 && end > start, "production camera callback must be found");
  let closed = 0;
  const landmarks = Array.from({ length: 21 }, () => ({ x: .5, y: .2, z: 0 }));
  const context = vm.createContext({
    current: () => true,
    document: { hidden: false },
    detector: { current: { detectForVideo: () => ({ landmarks: [landmarks], handedness: [[{ score: .99 }]] }) } },
    video: { current: { readyState: 2, currentTime: 0 } },
    stream: { current: {} },
    control: { current: h.control }, gateway: { current: { setMessage: (text) => h.messages.push(text) } },
    frame: { current: 0 },
    requestAnimationFrame: () => 1,
    closeCamera: () => { closed++; h.control.setCamera(false); },
    setPoint() {}, setConfidence() {}, setDirection() {}, setFps() {},
    classifyTabletZone, stableTabletDirection, createTabletDirectionFilter,
  });
  vm.runInContext(stripTypeScriptTypes(`let previous=0,lastSent=0,lastVideoTime=-1,lastFreshFrame=0; const directionFilter=createTabletDirectionFilter(); ${source.slice(start, end)}; globalThis.tick=tick;`), context);
  return { context, get closed() { return closed; } };
}

test("production camera loop emits device state before arming, then movement after arming", async () => {
  const h = harness(), camera = cameraHarness(h);
  h.control.setCamera(true);
  for (let n = 1; n <= 4; n++) { camera.context.video.current.currentTime = n / 10; camera.context.tick(n * 110); }
  assert.equal(h.telemetry.at(-1).gesture, "forward");
  assert.equal(h.commands.length, 0);
  await h.control.claim();
  camera.context.video.current.currentTime = .5;
  camera.context.tick(550);
  await flush();
  assert.equal(h.commands.at(-1).action, "drive");
  assert.equal(h.commands.at(-1).linear, .06);
});

test("the same production camera loop uses new session callbacks after reconnect", async () => {
  const h = harness(), camera = cameraHarness(h);
  h.control.setCamera(true);
  await h.control.claim();
  camera.context.tick(110);
  await flush();
  h.current.value = h.link("session-b");
  h.control.syncConnection();
  await h.control.claim();
  camera.context.video.current.currentTime = .2;
  camera.context.tick(220);
  await flush();
  assert.equal(h.telemetry.at(-1).token, "session-b");
  assert.equal(h.commands.at(-1).token, "session-b");
});

test("production camera loop sends zero movement as soon as the hand is lost or uncertain", async () => {
  for (const condition of ["no hand", "low confidence"]) {
    const h = harness(), camera = cameraHarness(h);
    h.control.setCamera(true);
    await h.control.claim();
    for (let n = 1; n <= 4; n++) {
      camera.context.video.current.currentTime = n / 10;
      camera.context.tick(n * 110);
      await flush();
    }
    assert.equal(h.commands.at(-1).linear, .06);
    const lm = Array.from({ length: 21 }, () => ({ x: .5, y: .2, z: 0 }));
    camera.context.detector.current.detectForVideo = () => ({
      landmarks: condition === "no hand" ? [] : [lm], handedness: [[{ score: .3 }]],
    });
    camera.context.video.current.currentTime = .5;
    camera.context.tick(550);
    await flush();
    assert.equal(h.commands.at(-1).linear, 0);
    assert.equal(h.commands.at(-1).angular, 0);
  }
});

test("production camera loop stops on detector failure and on stale video frames", async () => {
  for (const failure of ["exception", "frozen frame"]) {
    const h = harness(), camera = cameraHarness(h);
    h.control.setCamera(true);
    await h.control.claim();
    if (failure === "exception") camera.context.detector.current.detectForVideo = () => { throw Error("detector failed"); };
    camera.context.tick(110);
    if (failure === "frozen frame") camera.context.tick(1000);
    await flush();
    assert.equal(camera.closed, 1);
    assert.equal(h.state.armed, false);
    assert.ok(h.commands.some((c) => c.action === "release"));
  }
});

test('server authority loss disarms an idle camera and heartbeat cannot re-arm it', async () => {
  const h=harness();h.control.setCamera(true);await h.control.claim();
  h.control.observeAuthority(false);h.control.frame('forward',{});await flush();
  assert.equal(h.state.armed,false);h.control.observeAuthority(true);
  h.control.frame('forward',{});assert.equal(h.commands.filter(c=>c.action==='drive').length,0);
  await h.control.claim();h.control.frame('forward',{});await flush();
  assert.equal(h.commands.at(-1).linear,.06);
});

test('direction dwell uses elapsed time, stops immediately and resets across camera gaps',()=>{
  const f=createTabletDirectionFilter();
  assert.equal(f.update(.5,.2,.99,100),'stop');
  for(let t=110;t<280;t+=10)assert.equal(f.update(.5,.2,.99,t),'stop');
  assert.equal(f.update(.5,.2,.99,280),'forward');
  assert.equal(f.update(.5,.2,.2,290),'stop');
  assert.equal(f.update(.5,.2,.99,300),'stop');
  assert.equal(f.update(.5,.2,.99,480),'forward');
  assert.equal(f.update(.5,.2,.99,1000),'stop');
  assert.equal(f.update(.5,.5,.99,1010),'stop');
});

test('diagonal hysteresis holds a stable direction but never masks the stop zones',()=>{
  const f=createTabletDirectionFilter();
  f.update(.5,.2,.99,100);assert.equal(f.update(.5,.2,.99,280),'forward');
  assert.equal(f.update(.3,.31,.99,300),'forward');
  assert.equal(f.update(.5,.5,.99,320),'stop');
  assert.equal(f.update(.99,.99,.99,340),'stop');
  assert.equal(f.update(NaN,.2,.99,360),'stop');
});

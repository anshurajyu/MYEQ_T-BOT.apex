import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import vm from 'node:vm';
import { createCommandProof } from '../lib/tbot/command-proof.ts';
import { createVoiceIntent, createVoiceSession, openVoiceLink } from '../lib/tbot/voice-session.ts';
import { websocketUrl, gatewayFetch } from '../lib/tbot/transport.ts';
import { encodeVoiceWav, recordVoice } from '../lib/tbot/audio.ts';

const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
function harness(overrides = {}) {
  const events = [], command = { action: 'timed', source: 'voice', linear: .06, angular: 0, value: 5 };
  let liveStop, liveFailure;
  const ports = {
    open: async (stop, fail) => { liveStop = stop; liveFailure = fail; return { send: () => {}, close: () => events.push('closed') }; },
    record: async () => async () => { events.push('mic stopped'); return new Blob(['audio']); },
    interpret: async () => ({ transcript: 'forward', command }),
    execute: async value => events.push(value),
    stop: () => { session.cancel(); events.push('STOP'); },
    phase: value => events.push(value), message: value => events.push(value), transcript: value => events.push(value),
    ...overrides,
  };
  const session = createVoiceSession(() => ports);
  return { session, events, command, ports, liveStop: () => liveStop(), liveFailure: () => liveFailure('link lost') };
}

test('voice records/transcribes once and sends the parsed velocity command to shared send', async () => {
  const h = harness(); await h.session.start(); await h.session.finish(); await h.session.finish();
  assert.equal(h.events.filter(v => v === h.command).length, 1);
  assert.equal(h.events.filter(v => v === 'mic stopped').length, 1);
  assert.equal(h.events.at(-1), 'idle');
});
test('STOP while transcription is pending cannot trigger later motion', async () => {
  const pending = deferred(), h = harness({ interpret: () => pending.promise });
  await h.session.start(); const result = h.session.finish(); await Promise.resolve();
  h.session.cancel(); pending.resolve({ transcript: 'forward', command: h.command }); await result;
  assert.ok(!h.events.includes(h.command));
});
test('production voice claim canceled by keyboard takeover neither moves nor stops the new source', async () => {
  const source = readFileSync(new URL('../components/mission-console.tsx', import.meta.url), 'utf8');
  const start = source.indexOf('const executeVoice = async');
  const end = source.indexOf('  voicePorts.current =', start);
  assert.ok(start >= 0 && end > start, 'production voice executor must be found');
  const claim = deferred(), commands = [], changes = [];
  let current = true;
  const g = {
    send: command => { commands.push(command); return commands.length === 1 ? claim.promise : Promise.resolve(); },
    library: { missions: [] }, setMessage() {},
  };
  const voiceIntent={current:createVoiceIntent(()=> 'boot:0')};
  const context = vm.createContext({ g, voiceIntent, setSource: source => changes.push(source), setArmed: value => changes.push(value), t: { map_id: 'test' } });
  vm.runInContext(stripTypeScriptTypes(source.slice(start, end) + ';globalThis.executeVoice = executeVoice;'), context);
  const pending = context.executeVoice({ action: 'timed', linear: .06, angular: 0, value: 5 }, () => current);
  current = false;
  await g.send({ action: 'claim', source: 'keyboard' });
  claim.resolve();
  await pending;
  assert.deepEqual(commands.map(c => `${c.action}:${c.source}`), ['stop:voice', 'claim:keyboard']);
  assert.deepEqual(changes, [], 'stale completion must not reselect voice in the UI');
});

function productionVoiceHarness() {
  const source=readFileSync(new URL('../components/mission-console.tsx',import.meta.url),'utf8');
  const start=source.indexOf('const previewVoice = async');
  const end=source.indexOf('  useEffect(() => () => {',start);
  assert.ok(start>=0&&end>start,'production typed/recorded voice section must be found');
  const interpreted=deferred(),commands=[];
  let serverEpoch='boot',clientEpoch='boot',serverGeneration=0,clientGeneration=0;
  const external=(kind,delivered=true)=>{
    if(kind==='restart'){serverEpoch='new-boot';serverGeneration=0;}
    else serverGeneration+=kind==='mode'?2:1;
    if(delivered){clientEpoch=serverEpoch;clientGeneration=serverGeneration;}
  };
  const g={
    connected:true,token:'test-session',library:{missions:[]},setMessage(){},
    controlVersion:()=>`${clientEpoch}:${clientGeneration}`,
    request:()=>interpreted.promise,
    send:async command=>{
      commands.push(command);
      if(command.action==='claim'&&(clientEpoch!==serverEpoch||clientGeneration!==serverGeneration))throw Error('Stale control generation');
      if(command.action==='stop'||command.action==='claim')serverGeneration++;
      clientEpoch=serverEpoch;clientGeneration=serverGeneration;
      return {ok:true,proof:{epoch:serverEpoch,generation:serverGeneration}};
    },
  };
  const context=vm.createContext({
    g,createVoiceIntent,createVoiceSession,voiceGeneration:{current:0},voiceIntent:{current:null},
    voicePorts:{current:null},voiceSession:{current:null},cancelVoiceRef:{current:()=>{}},voicePhase:'idle',
    setVoiceText(){},setSource(){},setArmed(){},setVoicePhase(){},t:null,
    API:'http://gateway',websocketUrl,openVoiceLink:async()=>({send(){},close(){}}),
    recordVoice:async()=>async()=>new Blob(['fake microphone']),
    stop:()=>g.send({action:'stop'}),
  });
  vm.runInContext(stripTypeScriptTypes(source.slice(start,end)+';globalThis.voiceActions={previewVoice,startRecording,finishRecording};'),context);
  const begin=async mode=>{
    if(mode==='typed')return {pending:context.voiceActions.previewVoice('forward')};
    await context.voiceActions.startRecording();
    const pending=context.voiceActions.finishRecording();
    await Promise.resolve();
    return {pending};
  };
  return {begin,commands,external,answer:()=>interpreted.resolve({transcript:'forward',command:{action:'timed',source:'voice',linear:.06,angular:0,value:5}})};
}

for(const mode of ['typed','recorded']) {
  test(`production ${mode} voice still moves with only its own Stop/claim acknowledgements`,async()=>{
    const h=productionVoiceHarness(),{pending}=await h.begin(mode);
    h.answer();await pending;
    assert.deepEqual(h.commands.map(command=>command.action),['stop','claim','timed']);
  });
  for(const event of ['stop','mode','restart']) for(const delivered of [true,false]) {
    test(`production ${mode} pending phrase cannot re-arm after external ${event}, telemetry ${delivered?'received':'delayed'}`,async()=>{
      const h=productionVoiceHarness(),{pending}=await h.begin(mode);
      h.external(event,delivered);h.answer();await pending;
      assert.ok(!h.commands.some(command=>command.action==='claim'||command.action==='timed'),JSON.stringify(h.commands));
      assert.deepEqual(h.commands.map(command=>command.action),delivered?[]:['stop']);
    });
  }
}
test('STOP during microphone permission cancels the resulting recorder', async () => {
  const permission = deferred(), h = harness({ record: () => permission.promise });
  const start = h.session.start(); await Promise.resolve(); h.session.cancel();
  permission.resolve(async () => { h.events.push('permission mic closed'); return new Blob(); }); await start;
  assert.ok(h.events.includes('permission mic closed')); assert.ok(!h.events.includes('recording'));
});
test('live STOP discards the buffered phrase instead of replaying it at finish', async () => {
  const h = harness(); await h.session.start(); h.liveStop(); await h.session.finish();
  assert.ok(h.events.includes('STOP')); assert.ok(!h.events.includes(h.command));
});
test('gateway loss cancels recording and never replays movement', async () => {
  const h = harness(); await h.session.start(); h.liveFailure(); await h.session.finish();
  assert.ok(h.events.includes('mic stopped')); assert.ok(!h.events.includes(h.command));
});
test('double start while model/permissions pending creates only one recording', async () => {
  const opening = deferred(); let calls = 0;
  const h = harness({ open: () => { calls++; return opening.promise; } });
  const a = h.session.start(); await h.session.start();
  opening.resolve({ close() {}, send() {} }); await a; assert.equal(calls, 1); h.session.cancel();
});
test('a reconnect uses the latest execute/interpret functions', async () => {
  const h = harness(); await h.session.start(); h.session.cancel();
  h.ports.execute = async () => h.events.push('new connection');
  await h.session.start(); await h.session.finish(); assert.ok(h.events.includes('new connection'));
});
test('missing voice model prevents microphone capture and reports the error', async () => {
  let microphones = 0;
  const h = harness({ open: async () => { throw Error('Offline voice model missing'); }, record: async () => { microphones++; } });
  await h.session.start(); assert.equal(microphones, 0); assert.match(h.events.at(-1), /model missing/);
});
test('HTTPS and HTTP voice/cockpit links retain host and API prefix', () => {
  assert.equal(websocketUrl('https://robot.example.ts.net/api', '/voice/live'), 'wss://robot.example.ts.net/api/voice/live');
  assert.equal(websocketUrl('http://127.0.0.1:8001', '/ws'), 'ws://127.0.0.1:8001/ws');
});
test('lost HTTP tablet command is aborted within its bounded timeout', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async (_url, options) => new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(options.signal.reason)));
  try { await assert.rejects(gatewayFetch('http://unused', {}, 10), /timed out/); }
  finally { globalThis.fetch = original; }
});
test('HTTP headers without a complete response body cannot leave a tablet request pending', async () => {
  const original = globalThis.fetch;
  let headersReceived = false, bodyAborted = false;
  globalThis.fetch = async (_url, options) => {
    headersReceived = true;
    return new Response(new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('{"ok":'));
        options.signal.addEventListener('abort', () => {
          bodyAborted = true;
          controller.error(options.signal.reason);
        }, { once: true });
      },
    }), { headers: { 'Content-Type': 'application/json' } });
  };
  try {
    await assert.rejects(gatewayFetch('http://unused', {}, 10), /timed out/);
    assert.equal(headersReceived, true);
    assert.equal(bodyAborted, true);
  } finally { globalThis.fetch = original; }
});

// Run the actual gateway connection effect with a deterministic clock. Delayed
// HTTP replies exercise the cleanup/reconnection boundary without React timing
// or a network server masking competing heartbeat requests.
function tabletGatewayHarness() {
  const source = readFileSync(new URL('../lib/tbot/client.ts', import.meta.url), 'utf8');
  const start = source.indexOf('  useEffect(() => {\n    let dead');
  const end = source.indexOf('  }, [role, pair]);', start);
  assert.ok(start >= 0 && end > start, 'production connection effect must be found');
  const events = [], requests = [], timers = new Map();
  let now = 0, nextTimer = 0, cleanup;
  const schedule = (callback, delay, repeat = false) => {
    const id = ++nextTimer;
    timers.set(id, { callback, due: now + delay, repeat: repeat ? delay : 0 });
    return id;
  };
  const context = vm.createContext({
    role: 'tablet', pair: 'paired-phone', API: 'https://robot.test/api', URLSearchParams,
    useEffect: callback => { cleanup = callback(); },
    gatewayFetch: (url, options) => {
      let resolve, reject;
      const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
      requests.push({ url, options, resolve, reject });
      return promise;
    },
    setConnected: value => events.push(['connected', value]),
    setToken: value => events.push(['token', value]),
    setMessage: value => events.push(['message', value]),
    setTelemetry: value => events.push(['telemetry', value]),
    ws: { current: null }, pending: { current: new Map() },
    protocol: {current:createCommandProof()}, ingest() {}, setAuthority: value => events.push(['authority',value]),
    sessionStorage: {store:new Map(),getItem(key){return this.store.get(key)},setItem(key,value){this.store.set(key,value)},removeItem(key){this.store.delete(key)}},
    setTimeout: (callback, delay) => schedule(callback, delay),
    setInterval: (callback, delay) => schedule(callback, delay, true),
    clearTimeout: id => timers.delete(id), clearInterval: id => timers.delete(id),
  });
  vm.runInContext(stripTypeScriptTypes(source.slice(start, end + '  }, [role, pair]);'.length)), context);
  const flush = () => new Promise(resolve => setImmediate(resolve));
  const answer = async (request, token = 'session-a') => {
    request.resolve({ ok: true, json: async () => ({ token }) });
    await flush();
  };
  const advance = async milliseconds => {
    const target = now + milliseconds;
    while (true) {
      const next = [...timers].filter(([, task]) => task.due <= target).sort((a, b) => a[1].due - b[1].due)[0];
      if (!next) break;
      const [id, task] = next;
      now = task.due;
      if (task.repeat) task.due += task.repeat; else timers.delete(id);
      task.callback();
      await flush();
    }
    now = target;
    await flush();
  };
  return { requests, events, timers, answer, advance, flush, close: () => cleanup() };
}

test('production tablet heartbeat waits for each reply before sending another', async () => {
  const h = tabletGatewayHarness();
  await h.answer(h.requests[0]);
  await h.advance(500);
  assert.equal(h.requests.length, 2);
  assert.match(h.requests[1].url, /\/heartbeat$/);
  await h.advance(2000);
  assert.equal(h.requests.length, 2, 'slow Wi-Fi must not create overlapping heartbeat requests');
  await h.answer(h.requests[1]);
  await h.advance(499);
  assert.equal(h.requests.length, 2);
  await h.advance(1);
  assert.equal(h.requests.length, 3);
  h.close();
  const eventsAtClose = h.events.length;
  await h.answer(h.requests[2]);
  await h.advance(5000);
  assert.equal(h.requests.length, 3, 'late heartbeat success must not revive a closed connection');
  assert.equal(h.events.length, eventsAtClose);
  assert.equal(h.timers.size, 0);
});
test('production tablet heartbeat failure reconnects once with a fresh session', async () => {
  const h = tabletGatewayHarness();
  await h.answer(h.requests[0]);
  await h.advance(500);
  h.requests[1].reject(Error('Wi-Fi lost'));
  await h.flush();
  assert.deepEqual(h.events.slice(-3), [['connected', false], ['token', ''],['authority',false]]);
  await h.advance(999);
  assert.equal(h.requests.length, 2);
  await h.advance(1);
  assert.equal(h.requests.length, 3);
  assert.match(h.requests[2].url, /\/session\/resume$/);
  await h.answer(h.requests[2], 'session-b');
  await h.advance(500);
  assert.equal(h.requests.length, 4);
  assert.equal(h.requests[3].options.headers.Authorization, 'Bearer session-b');
  assert.ok(h.requests.every(request => !request.url.endsWith('/command')), 'reconnect must never replay motion');
  h.close();
  const eventsAtClose = h.events.length;
  h.requests[3].reject(Error('closed Wi-Fi request'));
  await h.flush();
  await h.advance(5000);
  assert.equal(h.requests.length, 4, 'late heartbeat failure must not restart a closed connection');
  assert.equal(h.events.length, eventsAtClose);
  assert.equal(h.timers.size, 0);
});
test('closing during session creation or a scheduled retry cannot resurrect the tablet link', async () => {
  const pendingSession = tabletGatewayHarness();
  pendingSession.close();
  await pendingSession.answer(pendingSession.requests[0]);
  await pendingSession.advance(5000);
  assert.deepEqual(pendingSession.events, []);
  assert.equal(pendingSession.requests.length, 1);
  assert.equal(pendingSession.timers.size, 0);

  const retry = tabletGatewayHarness();
  retry.requests[0].reject(Error('session unavailable'));
  await retry.flush();
  assert.equal(retry.timers.size, 1);
  retry.close();
  const eventsAtClose = retry.events.length;
  await retry.advance(5000);
  assert.equal(retry.requests.length, 1);
  assert.equal(retry.events.length, eventsAtClose);
  assert.equal(retry.timers.size, 0);
});
test('voice websocket waits for recognition readiness and surfaces missing model', async () => {
  const Original = globalThis.WebSocket; let socket;
  class FakeSocket {
    static OPEN = 1; readyState = 1; sent = [];
    constructor() { socket = this; }
    send(value) { this.sent.push(value); }
    close() { this.readyState = 3; }
  }
  globalThis.WebSocket = FakeSocket;
  try {
    const ready = openVoiceLink('wss://robot/api/voice/live', 'session', () => {}, () => {});
    socket.onopen(); assert.deepEqual(JSON.parse(socket.sent[0]), { token: 'session' });
    socket.onmessage({ data: JSON.stringify({ ready: true }) }); const link = await ready;
    link.send(new ArrayBuffer(20)); assert.equal(socket.sent.length, 2); link.close();
    const failed = openVoiceLink('wss://robot/api/voice/live', 'session', () => {}, () => {});
    socket.onmessage({ data: JSON.stringify({ error: 'Offline voice model missing' }) });
    await assert.rejects(failed, /model missing/);
  } finally { globalThis.WebSocket = Original; }
});
test('44.1/48kHz browser audio produces accepted mono PCM16 16kHz WAV', async () => {
  for (const rate of [44100, 48000]) {
    const buffer = await encodeVoiceWav([new Float32Array(rate).fill(.5)], rate).arrayBuffer(), view = new DataView(buffer);
    assert.equal(view.getUint16(22, true), 1); assert.equal(view.getUint32(24, true), 16000);
    assert.equal(view.getUint16(34, true), 16); assert.equal(view.getUint32(40, true), 32000);
    assert.equal(buffer.byteLength, 32044); assert.equal(view.getInt16(44, true), 16383);
  }
});
test('AudioContext failure releases the already-granted microphone', async () => {
  const previous = { window: globalThis.window, navigator: Object.getOwnPropertyDescriptor(globalThis, 'navigator'), AudioContext: globalThis.AudioContext };
  let stopped = false;
  globalThis.window = { isSecureContext: true };
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop() { stopped = true; } }] }) } } });
  globalThis.AudioContext = class { constructor() { throw Error('Audio device unavailable'); } };
  try { await assert.rejects(recordVoice(), /Audio device/); assert.equal(stopped, true); }
  finally {
    globalThis.window = previous.window; globalThis.AudioContext = previous.AudioContext;
    if (previous.navigator) Object.defineProperty(globalThis, 'navigator', previous.navigator); else delete globalThis.navigator;
  }
});

test('command proof ignores delayed authority ACKs after Stop and never resets sequence on rearm',()=>{
  const protocol=createCommandProof();
  const proof={epoch:'boot-a',generation:0,permit:'fresh',expires_in_ms:750};
  assert.equal(protocol.accept(proof),true);
  assert.equal(protocol.stamp({action:'claim'}).seq,1);
  protocol.stamp({action:'stop'});
  assert.equal(protocol.accept(proof),false);
  assert.throws(()=>protocol.stamp({action:'drive'}),/STOP acknowledgement/);
  assert.equal(protocol.accept({...proof,generation:1}),true);
  assert.equal(protocol.stamp({action:'claim'}).seq,2);
  assert.equal(protocol.accept({...proof,epoch:'other-boot',generation:2}),false);
  protocol.reset();assert.equal(protocol.accept({...proof,epoch:'other-boot'}),true);
  assert.equal(protocol.stamp({action:'claim'}).seq,1);
});

test('live voice failure requests Stop as well as cancelling capture', async()=>{
  const h=harness();await h.session.start();h.liveFailure();await h.session.finish();
  assert.ok(h.events.includes('STOP'));assert.ok(!h.events.includes(h.command));
});

test('Stop during input-source selection prevents a delayed Stop ACK from issuing a fresh claim', async()=>{
  const source=readFileSync(new URL('../components/mission-console.tsx',import.meta.url),'utf8');
  const begin=source.indexOf('  const selectSource = async');
  const end=source.indexOf('  useEffect(',begin);
  const pending=deferred(),commands=[],armed=[],selected=[];
  const generation={current:0};
  const context=vm.createContext({createVoiceIntent,voiceGeneration:generation,cancelVoiceRef:{current(){}},
    setArmed:v=>armed.push(v),setSource:v=>selected.push(v),
    g:{controlVersion:()=> 'boot:0',setMessage(){},send:c=>{commands.push(c);return pending.promise;}}});
  vm.runInContext(stripTypeScriptTypes(source.slice(begin,end)+';globalThis.selectSource=selectSource;'),context);
  const selecting=context.selectSource('keyboard');
  generation.current++; // user STOP or another controller choice during await
  pending.resolve(true);await selecting;
  assert.deepEqual(commands.map(c=>c.action),['stop']);
  assert.deepEqual(armed,[false]);assert.deepEqual(selected,[]);
});

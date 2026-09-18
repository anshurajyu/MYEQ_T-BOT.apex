/** Optional browser checks against a running LOCAL simulator; installs nothing.
 * Start the dashboard and TBOT_MODE=virtual backend first, with USB output off.
 * Run: node scripts/tbot-browser-tests.mjs
 * Requires an existing Playwright module and Chrome. Overrides:
 *   TBOT_PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs
 *   TBOT_BROWSER_CHANNEL=chrome  TBOT_TEST_URL=http://127.0.0.1:5173
 *   TBOT_TEST_API=http://127.0.0.1:8001
 * Optional TBOT_TEST_VOICE_WAV=/absolute/path/to/generated-forward.wav enables
 * real Vosk HTTP + browser microphone checks. Use generated speech saying only
 * "forward", mono PCM16/16kHz, with .5s leading and >=7s trailing silence.
 * Camera and microphone devices are synthetic; no personal recordings are made.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const { chromium } = await import(process.env.TBOT_PLAYWRIGHT_MODULE || 'playwright');
const base = new URL(process.env.TBOT_TEST_URL || 'http://127.0.0.1:5173').origin;
const directApi = new URL(process.env.TBOT_TEST_API || 'http://127.0.0.1:8001').origin;
for (const address of [base, directApi]) {
  assert.ok(['localhost', '127.0.0.1', '[::1]'].includes(new URL(address).hostname), 'Browser tests require a loopback simulator URL');
}
const wavPath = process.env.TBOT_TEST_VOICE_WAV && resolve(process.env.TBOT_TEST_VOICE_WAV);
const wav = wavPath && readFileSync(wavPath);
const browser = await chromium.launch({
  headless: true, channel: process.env.TBOT_BROWSER_CHANNEL || 'chrome',
  args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
    ...(wavPath ? [`--use-file-for-fake-audio-capture=${wavPath}`] : [])],
});
const passed = [];
const pass = message => { passed.push(message); console.log(`PASS ${message}`); };
const waitUntil = async (page, predicate, description, timeout = 10000) => {
  const deadline = Date.now() + timeout;
  while (!predicate() && Date.now() < deadline) await page.waitForTimeout(50);
  assert.ok(predicate(), description);
};

async function preflight(page) {
  // A plain JSON page runs no dashboard control effects before this gate.
  await page.goto(`${base}/api/health`);
  return page.evaluate(async ({ base, directApi }) => {
    const json = async (url, options) => {
      const response = await fetch(url, options);
      if (!response.ok) throw Error(`Simulator preflight failed: ${url} (${response.status})`);
      return response.json();
    };
    let proxyToken;
    for (const api of [`${base}/api`, directApi]) {
      const health = await json(`${api}/health`);
      const { token } = await json(`${api}/session`, { method: 'POST' });
      const servo = await json(`${api}/direct-servo`, { headers: { Authorization: `Bearer ${token}` } });
      if (health.robot !== 'virtual-lab' || servo.enabled !== false)
        throw Error(`REFUSING commands: ${api} must be virtual-lab with direct-servo disabled`);
      if (api === `${base}/api`) proxyToken = token;
    }
    return proxyToken;
  }, { base, directApi });
}

async function withPage(viewport, permissions, run) {
  const context = await browser.newContext({ viewport, permissions });
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  try {
    const token = await preflight(page);
    await run(page, token);
    assert.deepEqual(errors, [], 'browser must not have uncaught application errors');
  } finally { await context.close(); }
}

function captureCommands(page) {
  const sent = [], acknowledged = [];
  page.on('websocket', socket => {
    socket.on('framesent', ({ payload }) => { try { sent.push(JSON.parse(String(payload))); } catch {} });
    socket.on('framereceived', ({ payload }) => {
      try { const message = JSON.parse(String(payload)); if (message.type === 'ack' && message.ok) acknowledged.push(message.id); } catch {}
    });
  });
  return predicate => sent.some(command => predicate(command) && acknowledged.includes(command.id));
}

async function dashboardControls(page) {
  const accepted = captureCommands(page);
  await page.addInitScript(() => {
    window.testPad = null;
    Object.defineProperty(navigator, 'getGamepads', { value: () => window.testPad ? [window.testPad] : [] });
  });
  await page.goto(`${base}/mission-control`);
  await page.getByText('Gateway online', { exact: true }).first().waitFor({ timeout: 45000 });
  await page.getByRole('button', { name: /^Drive/ }).first().click();
  await page.getByRole('button', { name: 'Take keyboard control', exact: true }).click();
  await page.keyboard.down('w');
  await waitUntil(page, () => accepted(c => c.action === 'drive' && c.source === 'keyboard' && c.linear > 0), 'keyboard forward acknowledged');
  await page.keyboard.up('w');
  await waitUntil(page, () => accepted(c => c.action === 'drive' && c.source === 'keyboard' && c.linear === 0), 'keyboard release acknowledged');
  pass('Keyboard W and release reach acknowledged shared drive commands');

  await page.evaluate(() => { window.testPad = { id: 'Mock EvoFox', mapping: '', connected: true, index: 0, axes: [0, 0, 0, 0, 0, -1], buttons: Array.from({ length: 16 }, () => ({ pressed: false, value: 0, touched: false })) }; });
  await page.getByRole('button', { name: 'Take gamepad control', exact: true }).click();
  await page.evaluate(() => { window.testPad.axes[1] = -1; window.testPad.axes[5] = 1; window.testPad.buttons[7] = { pressed: true, value: 1, touched: true }; });
  await waitUntil(page, () => accepted(c => c.action === 'drive' && c.source === 'gamepad' && c.linear > 0), 'gamepad forward acknowledged');
  await page.evaluate(() => { window.testPad.axes[1] = 0; window.testPad.axes[5] = -1; window.testPad.buttons[7] = { pressed: false, value: 0, touched: false }; });
  await waitUntil(page, () => accepted(c => c.action === 'drive' && c.source === 'gamepad' && c.linear === 0), 'gamepad release acknowledged');
  pass('Gamepad forward and deadman release preserve the existing mapping');

  await page.getByRole('button', { name: /^Voice/ }).first().click();
  await page.getByPlaceholder('Forward', { exact: true }).fill('forward');
  await page.getByRole('button', { name: 'RUN COMMAND', exact: true }).click();
  await waitUntil(page, () => accepted(c => c.action === 'timed' && c.source === 'voice' && c.linear === .06), 'typed voice timed command acknowledged');
  await page.getByPlaceholder('Forward', { exact: true }).fill('stop');
  await page.getByRole('button', { name: 'RUN COMMAND', exact: true }).click();
  await waitUntil(page, () => accepted(c => c.action === 'stop'), 'voice stop acknowledged');
  pass('Typed voice parses, claims control, sends timed movement, and stops');
}

async function proxySockets(page, token) {
  await page.evaluate(token => new Promise((resolve, reject) => {
    const url = path => `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api${path}`;
    const cockpit = new WebSocket(url('/ws'));
    let voice, heartbeat, ready = false, count = 0, countAtReady = 0;
    const finish = error => {
      clearTimeout(timeout); clearInterval(heartbeat); cockpit.close(); voice?.close();
      if (error) reject(error); else resolve();
    };
    const timeout = setTimeout(() => finish(Error('Proxied cockpit/live voice sockets did not remain ready')), 30000);
    cockpit.onopen = () => {
      cockpit.send(JSON.stringify({ token }));
      heartbeat = setInterval(() => { if (cockpit.readyState === WebSocket.OPEN) cockpit.send(JSON.stringify({ type: 'heartbeat' })); }, 200);
    };
    cockpit.onerror = () => finish(Error('Cockpit proxy socket error'));
    cockpit.onmessage = ({ data }) => {
      if (JSON.parse(data).type !== 'telemetry') return;
      count++;
      if (ready && count > countAtReady) { finish(); return; }
      if (voice) return;
      voice = new WebSocket(url('/voice/live'));
      voice.onopen = () => voice.send(JSON.stringify({ token }));
      voice.onerror = () => finish(Error('Live voice proxy socket error'));
      voice.onmessage = ({ data }) => {
        const message = JSON.parse(data);
        if (message.error) { finish(Error(message.error)); return; }
        if (message.ready) { ready = true; countAtReady = count; }
      };
    };
  }), token);
  pass('Same-origin cockpit telemetry remains connected while live voice becomes ready');
}

async function pairPhone(page, token) {
  const code = await page.evaluate(async token => {
    const response = await fetch('/api/pairing', { method: 'POST', headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) throw Error(`Pairing failed: ${response.status}`);
    return (await response.json()).code;
  }, token);
  await page.goto(`${base}/tablet?pair=${encodeURIComponent(code)}`);
  await page.getByText('PAIRED', { exact: true }).waitFor({ timeout: 30000 });
}

async function gestureControls(page, token, synthetic) {
  const commands = [], responses = [];
  page.on('request', request => { if (new URL(request.url()).pathname.endsWith('/command') && request.method() === 'POST') commands.push(JSON.parse(request.postData())); });
  page.on('response', response => { if (new URL(response.url()).pathname.endsWith('/command')) responses.push(response.status()); });
  if (synthetic) {
    await page.addInitScript(() => { window.testHand = 'forward'; });
    await page.route(/@mediapipe.*tasks[-_]vision/, route => route.fulfill({ contentType: 'application/javascript', body: `
      export const FilesetResolver={forVisionTasks:async()=>({})};
      export const HandLandmarker={createFromOptions:async()=>({close(){},detectForVideo(){
        const d=window.testHand;if(d==='none')return {landmarks:[],handedness:[]};
        const p={forward:[.5,.2],backward:[.5,.8],left:[.8,.5],right:[.2,.5],stop:[.5,.5]}[d];
        return {landmarks:[Array.from({length:21},()=>({x:p[0],y:p[1],z:0}))],handedness:[[{score:.99}]]};
      }})};` }));
  }
  await pairPhone(page, token);
  await page.getByRole('button', { name: 'START CAMERA', exact: true }).click();
  await page.getByText('Camera ready. Center your finger, then request gesture control.', { exact: true }).waitFor({ timeout: 45000 });
  await page.waitForTimeout(800);
  assert.equal(commands.filter(c => c.action === 'drive').length, 0, 'camera must not auto-arm');
  await page.getByRole('button', { name: 'REQUEST GESTURE CONTROL', exact: true }).click();
  await page.getByRole('button', { name: 'GESTURE AUTHORITY ACTIVE', exact: true }).waitFor();
  if (synthetic) {
    for (const [direction, linear, angular] of [['forward', .06, 0], ['backward', -.045, 0], ['left', 0, .35], ['right', 0, -.35], ['stop', 0, 0]]) {
      const start = commands.length;
      await page.evaluate(value => { window.testHand = value; }, direction);
      await waitUntil(page, () => commands.slice(start).some(c => c.action === 'drive' && c.source === 'gesture' && c.linear === linear && c.angular === angular), `${direction} camera input reaches HTTP drive`);
    }
    await page.evaluate(() => { window.testHand = 'none'; });
  }
  await page.waitForTimeout(350);
  const drives = commands.filter(c => c.action === 'drive');
  assert.ok(drives.length > 0, 'armed camera must send stop frames for no hand');
  assert.equal(drives.at(-1).linear, 0); assert.equal(drives.at(-1).angular, 0);
  if (!synthetic) assert.ok(drives.every(c => c.linear === 0 && c.angular === 0), 'real model must keep synthetic no-hand camera stopped');
  await page.getByRole('button', { name: 'STOP NOW', exact: true }).click();
  await page.getByRole('button', { name: 'REQUEST GESTURE CONTROL', exact: true }).waitFor();
  const stopped = commands.length;
  if (synthetic) await page.evaluate(() => { window.testHand = 'forward'; });
  await page.waitForTimeout(900);
  assert.equal(commands.slice(stopped).filter(c => c.action === 'drive').length, 0, 'Stop must disarm future camera detections');
  assert.ok(responses.length && responses.every(status => status === 200), 'gesture HTTP commands must all be accepted');
  pass(synthetic ? 'Paired phone camera → explicit arm → all gesture directions → real HTTP → no-hand stop → disarm' : 'Real MediaPipe model/wasm loads at 360px and no-hand frames remain stopped');
}

async function securePairing(page, token) {
  const pairing = await page.evaluate(async token => {
    const response = await fetch('/api/pairing', {method:'POST',headers:{Authorization:`Bearer ${token}`}});
    if (!response.ok) throw Error(`Secure pairing failed: ${response.status}`);
    return response.json();
  }, token);
  assert.equal(new URL(pairing.url).protocol,'https:');
  assert.ok(!pairing.url.includes('token='),'Bearer must not appear in the pairing URL');
  await page.goto(pairing.url);
  await page.getByText('PAIRED',{exact:true}).waitFor({timeout:30000});
  await page.reload();
  await page.getByText('PAIRED',{exact:true}).waitFor({timeout:30000});
  assert.equal(await page.getByRole('button',{name:'REQUEST GESTURE CONTROL',exact:true}).isDisabled(),true);
  const duplicate = await page.context().newPage();
  await duplicate.goto(pairing.url);
  await duplicate.getByText(/Pairing code is invalid, used or expired/).waitFor({timeout:10000});
  await duplicate.close();
  pass('Actual private HTTPS pairing from localhost, single-use rejection, refresh/resume without arming');
}

async function speechControls(page, token) {
  const response = await page.request.post(`${base}/api/voice/transcribe`, {
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'audio/wav' }, data: wav,
  });
  assert.equal(response.status(), 200);
  const result = await response.json();
  assert.equal(result.transcript, 'forward'); assert.equal(result.command.action, 'timed');
  pass('Generated WAV → installed Vosk model → forward command');
  const accepted = captureCommands(page);
  await page.goto(`${base}/mission-control`);
  await page.getByText('Gateway online', { exact: true }).first().waitFor({ timeout: 30000 });
  await page.getByRole('button', { name: /^Voice/ }).first().click();
  await page.getByRole('button', { name: 'START RECORDING', exact: true }).click();
  await page.getByRole('button', { name: 'FINISH RECORDING', exact: true }).waitFor({ timeout: 15000 });
  await page.waitForTimeout(3000);
  const transcribed = page.waitForResponse(response => response.url().includes('/voice/transcribe'));
  await page.getByRole('button', { name: 'FINISH RECORDING', exact: true }).click();
  const transcription = await transcribed;
  assert.equal(transcription.status(), 200); assert.equal((await transcription.json()).transcript, 'forward');
  await waitUntil(page, () => accepted(c => c.action === 'timed' && c.source === 'voice' && c.linear === .06), 'microphone voice command acknowledged');
  await page.getByRole('button', { name: 'STOP', exact: true }).first().click();
  pass('Fake microphone → real Web Audio/WAV → Vosk → voice claim → acknowledged timed drive → Stop');
}

try {
  await withPage({ width: 1440, height: 1000 }, [], async (page, token) => {
    await proxySockets(page, token);
    await dashboardControls(page);
  });
  await withPage({ width: 430, height: 900 }, ['camera'], (page, token) => gestureControls(page, token, true));
  await withPage({ width: 360, height: 800 }, ['camera'], (page, token) => gestureControls(page, token, false));
  await withPage({ width:430,height:900 }, [], securePairing);
  if (wav) await withPage({ width: 1440, height: 1000 }, ['microphone'], speechControls);
  else console.log('SKIP generated speech tests: set TBOT_TEST_VOICE_WAV to a padded generated "forward" WAV.');
  console.log(`\n${passed.length} browser checks passed. Physical hardware remains untested.`);
} finally { await browser.close(); }

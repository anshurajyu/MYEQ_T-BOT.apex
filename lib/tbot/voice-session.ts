export type VoiceCommand = Record<string, unknown>;
export type VoicePhase = "idle" | "starting" | "recording" | "processing";
export type VoiceLink = { send: (pcm: ArrayBuffer) => void; close: () => void };

/** Bind a phrase to the control state when the user began it. Only that
 * phrase's own acknowledged Stop/claim may advance its authority version. */
export function createVoiceIntent(version: () => string | null) {
  let expected = version();
  return {
    current: () => expected !== null && version() === expected,
    advance(ack: unknown) {
      const proof = (ack as {proof?:{epoch:string;generation:number}})?.proof;
      if (!expected || !proof) return false;
      const separator = expected.lastIndexOf(":");
      const next = `${proof.epoch}:${proof.generation}`;
      if (proof.epoch !== expected.slice(0,separator) || proof.generation !== Number(expected.slice(separator+1))+1 || version() !== next) return false;
      expected = next;
      return true;
    },
  };
}
type Ports = {
  open: (onStop: () => void, onFailure: (message: string) => void) => Promise<VoiceLink>;
  record: (onPCM: (pcm: ArrayBuffer) => void) => Promise<() => Promise<Blob>>;
  interpret: (blob: Blob) => Promise<{ transcript: string; command: VoiceCommand }>;
  execute: (command: VoiceCommand, current: () => boolean) => Promise<void>;
  stop: () => void;
  phase: (phase: VoicePhase) => void;
  message: (message: string) => void;
  transcript: (text: string) => void;
};

/** A stopped recording must never execute later when permissions/transcription resolve. */
export function createVoiceSession(ports: () => Ports) {
  let generation = 0, phase: VoicePhase = "idle";
  let link: VoiceLink | null = null, finishAudio: null | (() => Promise<Blob>) = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const setPhase = (next: VoicePhase) => { phase = next; ports().phase(next); };
  const clear = () => { clearTimeout(timer); link?.close(); link = null; };
  const cancel = () => {
    generation++;
    clear();
    const finish = finishAudio; finishAudio = null;
    if (finish) void finish().catch(() => {});
    setPhase("idle");
  };
  const finish = async () => {
    if (phase !== "recording" || !finishAudio) return;
    const ticket = generation, current = () => ticket === generation;
    const audio = finishAudio; finishAudio = null;
    setPhase("processing"); clear();
    try {
      const blob = await audio();
      if (!current()) return;
      const result = await ports().interpret(blob);
      if (!current()) return;
      ports().transcript(result.transcript);
      if (result.command.action === "stop") ports().stop();
      else await ports().execute(result.command, current);
    } catch (error) {
      if (current()) ports().message(String(error));
    } finally {
      if (current()) setPhase("idle");
    }
  };
  const start = async () => {
    if (phase !== "idle") return;
    const ticket = ++generation, current = () => ticket === generation;
    setPhase("starting");
    try {
      const opened = await ports().open(
        () => { if (current()) { ports().stop(); ports().message("Voice STOP acknowledged."); } },
        (message) => { if (current()) { cancel(); ports().stop(); ports().message(message); } },
      );
      if (!current()) { opened.close(); return; }
      link = opened;
      const audio = await ports().record(pcm => { if (current()) opened.send(pcm); });
      if (!current()) { await audio(); return; }
      finishAudio = audio;
      setPhase("recording");
      timer = setTimeout(() => void finish(), 15000);
    } catch (error) {
      if (current()) { cancel(); ports().message(String(error)); }
    }
  };
  return { start, finish, cancel };
}

export function openVoiceLink(url: string, token: string, onStop: () => void, onFailure: (message: string) => void): Promise<VoiceLink> {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(url);
    let ready = false, closed = false;
    const close = () => { closed = true; clearTimeout(timer); socket.close(); };
    const fail = (message: string) => {
      if (closed) return;
      close();
      if (ready) onFailure(message); else reject(Error(message));
    };
    const timer = setTimeout(() => fail("Voice gateway did not become ready. Check the offline Vosk model and connection."), 10000);
    socket.onopen = () => socket.send(JSON.stringify({ token }));
    socket.onmessage = event => {
      try {
        const message = JSON.parse(event.data);
        if (message.error) { fail(message.error); return; }
        if (message.ready && !ready) {
          ready = true; clearTimeout(timer);
          resolve({ close, send: pcm => { if (!closed && socket.readyState === WebSocket.OPEN) socket.send(pcm); } });
        }
        if (message.stopped) onStop();
      } catch { fail("Invalid response from voice gateway."); }
    };
    socket.onerror = () => fail("Voice connection failed. Check HTTPS/WebSocket routing and the Vosk model.");
    socket.onclose = () => { if (!closed) fail("Voice connection closed; recording cancelled."); };
  });
}

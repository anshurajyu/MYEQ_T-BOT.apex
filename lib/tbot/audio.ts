export function encodeVoiceWav(chunks: Float32Array[], rate: number): Blob {
  const length = chunks.reduce((n, chunk) => n + chunk.length, 0);
  const input = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) { input.set(chunk, offset); offset += chunk.length; }
  const count = Math.min(320000, Math.floor(length * 16000 / rate));
  const buffer = new ArrayBuffer(44 + count * 2), view = new DataView(buffer);
  const text = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
  };
  text(0, "RIFF"); view.setUint32(4, 36 + count * 2, true);
  text(8, "WAVE"); text(12, "fmt "); view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true); view.setUint32(28, 32000, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, "data"); view.setUint32(40, count * 2, true);
  for (let i = 0; i < count; i++) {
    const position = i * rate / 16000, a = Math.floor(position), f = position - a;
    const value = input[a] * (1 - f) + (input[Math.min(a + 1, length - 1)] || 0) * f;
    view.setInt16(44 + i * 2, Math.max(-1, Math.min(1, value)) * 32767, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

export async function recordVoice(onPCM?: (data: ArrayBuffer) => void) {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia)
    throw Error("Microphone access needs HTTPS, or localhost on this computer.");
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  let context: AudioContext | undefined;
  try {
    context = new AudioContext(); await context.resume();
    const audio = context, source = audio.createMediaStreamSource(stream);
    const processor = audio.createScriptProcessor(4096, 1, 1), chunks: Float32Array[] = [];
    let recorded = 0, stopped = false;
    processor.onaudioprocess = event => {
      if (stopped || recorded >= audio.sampleRate * 20) return;
      const chunk = new Float32Array(event.inputBuffer.getChannelData(0));
      chunks.push(chunk); recorded += chunk.length;
      if (onPCM) {
        const n = Math.floor(chunk.length * 16000 / audio.sampleRate);
        const buffer = new ArrayBuffer(n * 2), view = new DataView(buffer);
        for (let i = 0; i < n; i++) view.setInt16(i * 2, Math.max(-1, Math.min(1, chunk[Math.floor(i * audio.sampleRate / 16000)])) * 32767, true);
        onPCM(buffer);
      }
    };
    source.connect(processor); processor.connect(audio.destination);
    return async () => {
      if (stopped) throw Error("Recording already stopped");
      stopped = true; processor.onaudioprocess = null;
      processor.disconnect(); source.disconnect();
      stream.getTracks().forEach(track => track.stop());
      await audio.close();
      return encodeVoiceWav(chunks, audio.sampleRate);
    };
  } catch (error) {
    stream.getTracks().forEach(track => track.stop());
    await context?.close().catch(() => {});
    throw error;
  }
}

/**
 * Client-side audio prep for /try: the selected span of a decoded clip becomes 16 kHz mono 16-bit PCM WAV, the
 * only format POST /api/try accepts (web/worker/src/try/wav.ts). Nothing leaves the browser until Run.
 */

export const TARGET_RATE = 16_000;
/** The longest span a visitor can send. The Worker allows a little more (TRY_MAX_SECONDS) for rounding. */
export const MAX_CLIP_SECONDS = 60;
export const MIN_CLIP_SECONDS = 1;

/** Samples after resampling `frames` at `fromRate` to `toRate` (what OfflineAudioContext produces). */
export function resampledLength(frames: number, fromRate: number, toRate = TARGET_RATE): number {
  return Math.max(1, Math.ceil((frames * toRate) / fromRate));
}

/** Plain linear resampler, used where OfflineAudioContext is missing (and in tests). */
export function resampleLinear(data: Float32Array, fromRate: number, toRate = TARGET_RATE): Float32Array {
  if (fromRate === toRate) return data.slice();
  const out = new Float32Array(resampledLength(data.length, fromRate, toRate));
  const ratio = fromRate / toRate;
  for (let i = 0; i < out.length; i++) {
    const x = i * ratio;
    const a = Math.floor(x);
    const t = x - a;
    const s0 = data[Math.min(a, data.length - 1)] ?? 0;
    const s1 = data[Math.min(a + 1, data.length - 1)] ?? 0;
    out[i] = s0 + (s1 - s0) * t;
  }
  return out;
}

/** Average every channel into one. */
export function downmix(channels: Float32Array[]): Float32Array {
  if (channels.length === 1) return channels[0]!.slice();
  const n = channels[0]?.length ?? 0;
  const out = new Float32Array(n);
  for (const ch of channels) for (let i = 0; i < n; i++) out[i]! += (ch[i] ?? 0) / channels.length;
  return out;
}

/** 16-bit PCM mono WAV (44 byte header). */
export function encodeWav(samples: Float32Array, rate = TARGET_RATE): ArrayBuffer {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const str = (at: number, s: string) => {
    for (let i = 0; i < s.length; i++) v.setUint8(at + i, s.charCodeAt(i));
  };
  str(0, "RIFF");
  v.setUint32(4, 36 + samples.length * 2, true);
  str(8, "WAVE");
  str(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // mono
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  str(36, "data");
  v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i] ?? 0));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}

/** The span [start, end) of a decoded buffer as 16 kHz mono, via OfflineAudioContext when the browser has it. */
export async function toMono16k(buffer: AudioBuffer, start: number, end: number): Promise<Float32Array> {
  const from = Math.max(0, Math.floor(start * buffer.sampleRate));
  const to = Math.min(buffer.length, Math.ceil(end * buffer.sampleRate));
  const frames = Math.max(1, to - from);
  const channels = Array.from({ length: buffer.numberOfChannels }, (_, c) => buffer.getChannelData(c).subarray(from, to));
  const Offline = typeof window !== "undefined" ? window.OfflineAudioContext : undefined;
  if (!Offline) return resampleLinear(downmix(channels), buffer.sampleRate);
  const ctx = new Offline(1, resampledLength(frames, buffer.sampleRate), TARGET_RATE);
  const slice = ctx.createBuffer(channels.length, frames, buffer.sampleRate);
  channels.forEach((ch, c) => slice.copyToChannel(new Float32Array(ch), c));
  const src = ctx.createBufferSource();
  src.buffer = slice;
  // A mono destination downmixes every channel (speakers interpretation).
  src.connect(ctx.destination);
  src.start();
  const out = await ctx.startRendering();
  return out.getChannelData(0).slice();
}

/** Min and max per bucket, for drawing a waveform. */
export function peaks(data: Float32Array, buckets: number): { min: Float32Array; max: Float32Array } {
  const min = new Float32Array(buckets);
  const max = new Float32Array(buckets);
  const per = data.length / buckets;
  for (let b = 0; b < buckets; b++) {
    let lo = 0;
    let hi = 0;
    const a = Math.floor(b * per);
    const z = Math.min(data.length, Math.max(a + 1, Math.floor((b + 1) * per)));
    // Stride through long buckets; peaks only need to look right.
    const step = Math.max(1, Math.floor((z - a) / 256));
    for (let i = a; i < z; i += step) {
      const s = data[i] ?? 0;
      if (s < lo) lo = s;
      if (s > hi) hi = s;
    }
    min[b] = lo;
    max[b] = hi;
  }
  return { min, max };
}

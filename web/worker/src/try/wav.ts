/**
 * WAV header check for /api/try. The browser resamples every clip to 16 kHz mono 16-bit PCM before upload
 * (web/src/try/wav.ts); anything else is refused here, so the duration read from the header is exact.
 */

export const TRY_SAMPLE_RATE = 16_000;
export const TRY_MAX_SECONDS = 60.5;
export const TRY_MIN_SECONDS = 0.3;
export const TRY_MAX_BYTES = Math.round(2.5 * 1024 * 1024);

export type WavInfo = { sampleRate: number; channels: number; bitsPerSample: number; dataBytes: number; seconds: number };

export class WavError extends Error {}

const tag = (v: DataView, at: number) => String.fromCharCode(v.getUint8(at), v.getUint8(at + 1), v.getUint8(at + 2), v.getUint8(at + 3));

/** Parse RIFF/WAVE chunks up to `data`. Throws WavError with a plain sentence on anything unexpected. */
export function parseWav(buf: ArrayBuffer): WavInfo {
  const v = new DataView(buf);
  if (buf.byteLength < 44 || tag(v, 0) !== "RIFF" || tag(v, 8) !== "WAVE") throw new WavError("The body is not a WAV file.");
  let at = 12;
  let fmt: { format: number; channels: number; rate: number; bits: number } | null = null;
  while (at + 8 <= buf.byteLength) {
    const id = tag(v, at);
    const size = v.getUint32(at + 4, true);
    const body = at + 8;
    if (id === "fmt ") {
      if (size < 16 || body + 16 > buf.byteLength) throw new WavError("The WAV format chunk is too short.");
      fmt = { format: v.getUint16(body, true), channels: v.getUint16(body + 2, true), rate: v.getUint32(body + 4, true), bits: v.getUint16(body + 14, true) };
    } else if (id === "data") {
      if (!fmt) throw new WavError("The WAV file has no format chunk before its data.");
      if (fmt.format !== 1) throw new WavError("The WAV file must be uncompressed PCM.");
      if (fmt.channels !== 1) throw new WavError("The WAV file must be mono.");
      if (fmt.rate !== TRY_SAMPLE_RATE) throw new WavError(`The WAV file must be ${TRY_SAMPLE_RATE / 1000} kHz.`);
      if (fmt.bits !== 16) throw new WavError("The WAV file must be 16-bit.");
      if (body + size > buf.byteLength) throw new WavError("The WAV data is shorter than its header says.");
      return { sampleRate: fmt.rate, channels: fmt.channels, bitsPerSample: fmt.bits, dataBytes: size, seconds: size / (fmt.rate * 2) };
    }
    at = body + size + (size % 2);
  }
  throw new WavError("The WAV file has no data chunk.");
}

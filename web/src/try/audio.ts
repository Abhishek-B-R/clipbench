import { useCallback, useEffect, useRef, useState } from "react";
import { MAX_CLIP_SECONDS } from "./wav";

/** Files bigger than this are refused before decoding (decoding holds the whole file in memory as PCM). */
export const MAX_FILE_BYTES = 200 * 1024 * 1024;
export const ACCEPT = ".mp3,.m4a,.wav,.webm,.mp4,.mov,.aac,.ogg,.flac,audio/*,video/mp4,video/quicktime,video/webm";

let shared: AudioContext | null = null;
export function audioContext(): AudioContext {
  shared ??= new AudioContext();
  return shared;
}

export type Loaded = { name: string; buffer: AudioBuffer; source: "file" | "mic" };

/** Decode an audio or video file in the browser (video: its audio track). Throws a plain sentence on failure. */
export async function decodeFile(file: Blob, name: string): Promise<AudioBuffer> {
  if (file.size > MAX_FILE_BYTES) throw new Error("That file is too large to open here. Trim it to a short clip first.");
  if (file.size === 0) throw new Error("That file is empty.");
  const bytes = await file.arrayBuffer();
  try {
    const buf = await audioContext().decodeAudioData(bytes);
    if (buf.duration < 0.3) throw new Error("short");
    return buf;
  } catch (e) {
    if (e instanceof Error && e.message === "short") throw new Error("That clip is too short to transcribe.");
    const kind = /\.(mov|mp4|webm)$/i.test(name) ? "This browser could not read an audio track from that video." : "This browser could not decode that file.";
    throw new Error(`${kind} Try an mp3, m4a or wav export of it.`);
  }
}

export type RecorderState = { status: "idle" | "asking" | "recording" | "denied" | "unsupported"; elapsed: number };

/** Microphone recording with MediaRecorder; stops itself at MAX_CLIP_SECONDS. */
export function useRecorder(onDone: (blob: Blob) => void) {
  const [state, setState] = useState<RecorderState>({ status: "idle", elapsed: 0 });
  const rec = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const t0 = useRef(0);
  const timer = useRef<number | null>(null);
  const done = useRef(onDone);
  done.current = onDone;

  const cleanup = useCallback(() => {
    if (timer.current != null) window.clearInterval(timer.current);
    timer.current = null;
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
  }, []);

  const stop = useCallback(() => {
    if (rec.current && rec.current.state !== "inactive") rec.current.stop();
  }, []);

  const start = useCallback(async () => {
    if (typeof MediaRecorder === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      setState({ status: "unsupported", elapsed: 0 });
      return;
    }
    setState({ status: "asking", elapsed: 0 });
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: false, autoGainControl: true } });
    } catch {
      setState({ status: "denied", elapsed: 0 });
      return;
    }
    const chunks: Blob[] = [];
    const r = new MediaRecorder(stream.current);
    rec.current = r;
    r.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    r.onstop = () => {
      cleanup();
      setState({ status: "idle", elapsed: 0 });
      if (chunks.length) done.current(new Blob(chunks, { type: r.mimeType || "audio/webm" }));
    };
    r.start(250);
    t0.current = performance.now();
    setState({ status: "recording", elapsed: 0 });
    timer.current = window.setInterval(() => {
      const elapsed = (performance.now() - t0.current) / 1000;
      if (elapsed >= MAX_CLIP_SECONDS) stop();
      else setState({ status: "recording", elapsed });
    }, 100);
  }, [cleanup, stop]);

  useEffect(
    () => () => {
      if (rec.current && rec.current.state !== "inactive") {
        rec.current.onstop = null;
        rec.current.stop();
      }
      cleanup();
    },
    [cleanup],
  );

  return { ...state, start, stop };
}

/** Plays a mono buffer through Web Audio and reports the playhead in seconds (rAF). */
export function usePlayer(samples: Float32Array | null, rate: number) {
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const src = useRef<AudioBufferSourceNode | null>(null);
  const startedAt = useRef(0);
  const offset = useRef(0);
  const raf = useRef<number | null>(null);
  const duration = samples ? samples.length / rate : 0;

  const halt = useCallback(() => {
    if (raf.current != null) cancelAnimationFrame(raf.current);
    raf.current = null;
    if (src.current) {
      src.current.onended = null;
      try {
        src.current.stop();
      } catch {
        /* already stopped */
      }
    }
    src.current = null;
  }, []);

  const play = useCallback(
    (from?: number) => {
      if (!samples) return;
      halt();
      const ctx = audioContext();
      void ctx.resume();
      const buf = ctx.createBuffer(1, samples.length, rate);
      buf.copyToChannel(new Float32Array(samples), 0);
      const s = ctx.createBufferSource();
      s.buffer = buf;
      s.connect(ctx.destination);
      const at = Math.min(Math.max(from ?? offset.current, 0), Math.max(duration - 0.01, 0));
      offset.current = at >= duration - 0.05 ? 0 : at;
      startedAt.current = ctx.currentTime;
      s.start(0, offset.current);
      s.onended = () => {
        halt();
        offset.current = 0;
        setPlaying(false);
        setTime(duration);
      };
      src.current = s;
      setPlaying(true);
      const tick = () => {
        setTime(offset.current + (ctx.currentTime - startedAt.current));
        raf.current = requestAnimationFrame(tick);
      };
      tick();
    },
    [samples, rate, duration, halt],
  );

  const pause = useCallback(() => {
    if (!src.current) return;
    offset.current += audioContext().currentTime - startedAt.current;
    halt();
    setPlaying(false);
    setTime(offset.current);
  }, [halt]);

  const seek = useCallback(
    (t: number) => {
      const clamped = Math.min(Math.max(t, 0), duration);
      offset.current = clamped;
      setTime(clamped);
      if (src.current) play(clamped);
    },
    [duration, play],
  );

  useEffect(() => {
    halt();
    offset.current = 0;
    setPlaying(false);
    setTime(0);
    return halt;
  }, [samples, halt]);

  return { playing, time, duration, play, pause, seek };
}

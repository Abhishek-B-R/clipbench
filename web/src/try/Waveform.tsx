import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent, type RefObject } from "react";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/cn";
import { peaks } from "./wav";

export type Selection = { start: number; end: number };

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";

function useWidthOf(el: RefObject<HTMLElement | null>) {
  const [w, setW] = useState(0);
  useEffect(() => {
    if (!el.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.round(e!.contentRect.width)));
    ro.observe(el.current);
    return () => ro.disconnect();
  }, [el]);
  return w;
}

const fmt = (s: number) => {
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return `${m}:${r.toFixed(1).padStart(4, "0")}`;
};

/**
 * Waveform drawn on a canvas (min/max bars, hairline baseline). Optional trim window with two keyboard
 * accessible handles (role=slider), a playhead, click to seek, and tick marks (cue starts).
 */
export function Waveform({
  data,
  duration,
  height = 88,
  selection,
  onSelection,
  minSpan = 1,
  maxSpan = 60,
  playhead,
  onSeek,
  marks,
  label,
}: {
  data: Float32Array;
  duration: number;
  height?: number;
  selection?: Selection;
  onSelection?: (s: Selection) => void;
  minSpan?: number;
  maxSpan?: number;
  playhead?: number;
  onSeek?: (t: number) => void;
  marks?: number[];
  label: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const width = useWidthOf(box);
  const { resolved } = useTheme();
  const bars = Math.max(1, Math.floor(width / 3));
  const pk = useMemo(() => peaks(data, bars), [data, bars]);

  useEffect(() => {
    const c = canvas.current;
    if (!c || !width) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = width * dpr;
    c.height = height * dpr;
    const g = c.getContext("2d");
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, width, height);
    const mid = height / 2;
    g.fillStyle = css("--line");
    g.fillRect(0, mid, width, 1);
    let top = 0;
    for (let i = 0; i < bars; i++) top = Math.max(top, pk.max[i]!, -pk.min[i]!);
    const scale = top > 0 ? (mid - 4) / top : 0;
    const on = css("--ink-2");
    const off = css("--line-strong");
    for (let i = 0; i < bars; i++) {
      const t = ((i + 0.5) / bars) * duration;
      const inSel = !selection || (t >= selection.start && t <= selection.end);
      g.fillStyle = inSel ? on : off;
      const hi = Math.max(1, pk.max[i]! * scale);
      const lo = Math.max(1, -pk.min[i]! * scale);
      g.fillRect(i * 3, mid - hi, 2, hi + lo);
    }
  }, [pk, bars, width, height, duration, selection, resolved]);

  const x = (t: number) => (duration ? (t / duration) * 100 : 0);
  const tAt = (clientX: number) => {
    const r = box.current!.getBoundingClientRect();
    return Math.min(Math.max(((clientX - r.left) / r.width) * duration, 0), duration);
  };

  const drag = useRef<{ kind: "start" | "end" | "move"; t0: number; sel: Selection } | null>(null);
  const clamp = (s: Selection, kind: "start" | "end" | "move"): Selection => {
    let { start, end } = s;
    if (kind === "move") {
      const span = end - start;
      start = Math.min(Math.max(start, 0), duration - span);
      return { start, end: start + span };
    }
    if (kind === "start") start = Math.min(Math.max(start, 0, end - maxSpan), end - minSpan);
    else end = Math.max(Math.min(end, duration, start + maxSpan), start + minSpan);
    return { start, end };
  };
  const down = (kind: "start" | "end" | "move") => (e: PointerEvent) => {
    if (!selection || !onSelection) return;
    e.stopPropagation();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { kind, t0: tAt(e.clientX), sel: selection };
  };
  const move = (e: PointerEvent) => {
    const d = drag.current;
    if (!d || !onSelection) return;
    const dt = tAt(e.clientX) - d.t0;
    const next = d.kind === "start" ? { ...d.sel, start: d.sel.start + dt } : d.kind === "end" ? { ...d.sel, end: d.sel.end + dt } : { start: d.sel.start + dt, end: d.sel.end + dt };
    onSelection(clamp(next, d.kind));
  };
  const up = () => (drag.current = null);
  const key = (kind: "start" | "end") => (e: KeyboardEvent) => {
    if (!selection || !onSelection) return;
    const step = e.shiftKey ? 5 : 0.5;
    const d = e.key === "ArrowRight" || e.key === "ArrowUp" ? step : e.key === "ArrowLeft" || e.key === "ArrowDown" ? -step : 0;
    if (!d) return;
    e.preventDefault();
    onSelection(clamp({ ...selection, [kind]: selection[kind] + d }, kind));
  };

  return (
    <div
      ref={box}
      className={cn("relative w-full touch-none select-none", onSeek && "cursor-pointer")}
      style={{ height }}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={up}
      onClick={(e) => onSeek && !drag.current && onSeek(tAt(e.clientX))}
    >
      <canvas ref={canvas} role="img" aria-label={label} style={{ width: "100%", height }} className="block" />
      {marks?.map((m, i) => (
        <span key={i} aria-hidden className="pointer-events-none absolute top-0 h-1.5 w-px bg-ink-3" style={{ left: `${x(m)}%` }} />
      ))}
      {selection && onSelection && (
        <>
          <div
            className="absolute inset-y-0 cursor-grab rounded-xs border border-ink/70 bg-ink/[0.04] active:cursor-grabbing"
            style={{ left: `${x(selection.start)}%`, width: `${x(selection.end) - x(selection.start)}%` }}
            onPointerDown={down("move")}
          />
          {(["start", "end"] as const).map((k) => (
            <button
              key={k}
              type="button"
              role="slider"
              aria-label={k === "start" ? "Clip start" : "Clip end"}
              aria-valuemin={0}
              aria-valuemax={Math.round(duration * 10) / 10}
              aria-valuenow={Math.round(selection[k] * 10) / 10}
              aria-valuetext={`${fmt(selection[k])}`}
              onPointerDown={down(k)}
              onKeyDown={key(k)}
              className="absolute inset-y-0 z-10 flex w-4 -translate-x-1/2 cursor-ew-resize items-center justify-center"
              style={{ left: `${x(selection[k])}%` }}
            >
              <span className="h-8 w-1.5 rounded-full border border-surface bg-ink shadow-card" />
            </button>
          ))}
        </>
      )}
      {playhead != null && (
        <span aria-hidden className="pointer-events-none absolute inset-y-0 w-px bg-ink" style={{ left: `${x(playhead)}%` }}>
          <span className="absolute -top-1 -left-[3px] size-[7px] rounded-full bg-ink" />
        </span>
      )}
    </div>
  );
}

export { fmt as clock };

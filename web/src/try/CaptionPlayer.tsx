import { useMemo } from "react";
import { Download, Pause, Play } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { SeriesDot } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";
import type { TryResult } from "./api";
import { usePlayer } from "./audio";
import { buildCues, MAX_CUE_MS, MAX_LINES, maxChars, PAUSE_MS, sortedWords, toSrt, toVtt } from "./captions";
import { modelColor } from "./Results";
import { clock, Waveform } from "./Waveform";
import { TARGET_RATE } from "./wav";

function download(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Plays the exact clip the models heard, with the chosen model's word timings as live captions. Cues follow the
 * benchmark's fixed line-break rule (clipbench/scoring/captions.py); the current word is highlighted.
 */
export function CaptionPlayer({
  samples,
  results,
  model,
  onModel,
  language,
}: {
  samples: Float32Array;
  results: TryResult[];
  model: string;
  onModel: (p: string) => void;
  language: string | null;
}) {
  const ok = results.filter((r) => !r.error && r.words.length);
  const current = ok.find((r) => r.provider === model) ?? ok[0];
  const lang = language ?? current?.detected_language ?? "en";
  const words = useMemo(() => (current ? sortedWords(current.words) : []), [current]);
  const cues = useMemo(() => (current ? buildCues(current.words, lang) : []), [current, lang]);
  const p = usePlayer(samples, TARGET_RATE);
  const tMs = p.time * 1000;
  const cue = cues.find((c) => tMs >= c.start_ms && tMs <= c.end_ms) ?? null;
  const spaced = lang !== "ja";

  if (!current) return <p className="text-[13.5px] text-ink-2">No model returned word timings, so there is nothing to caption.</p>;

  // Words of the active cue, line by line (the rule's break index).
  const lineWords: number[][] = cue
    ? [Array.from({ length: cue.firstLine }, (_, j) => cue.first + j), Array.from({ length: cue.count - cue.firstLine }, (_, j) => cue.first + cue.firstLine + j)].filter((l) => l.length)
    : [];
  const base = `clipbench-try-${current.provider}`;

  return (
    <div className="grid gap-4">
      <div role="radiogroup" aria-label="Captions from" className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1">
        {ok.map((r) => {
          const on = r.provider === current.provider;
          return (
            <button
              key={r.provider}
              type="button"
              role="radio"
              aria-checked={on}
              onClick={() => onModel(r.provider)}
              className={cn(
                "inline-flex h-8 shrink-0 items-center gap-2 rounded-sm border px-2.5 text-[12.5px] font-medium whitespace-nowrap transition-colors duration-150",
                on ? "border-ink bg-surface text-ink" : "border-line bg-sunken text-ink-3 hover:text-ink-2",
              )}
            >
              <SeriesDot color={modelColor(r.provider)} />
              {r.label}
            </button>
          );
        })}
      </div>

      <div className="rounded-md border border-line bg-sunken/50 px-3 pt-3 pb-2">
        <Waveform data={samples} duration={p.duration} height={64} playhead={p.time} onSeek={p.seek} marks={cues.map((c) => c.start_ms / 1000)} label="Clip waveform; click to seek" />
        <div className="mt-2 flex items-center gap-3">
          <Button variant="primary" size="icon" onClick={() => (p.playing ? p.pause() : p.play())} aria-label={p.playing ? "Pause" : "Play"}>
            {p.playing ? <Pause size={15} /> : <Play size={15} />}
          </Button>
          <span className="num text-[12.5px] text-ink-2">
            {clock(Math.min(p.time, p.duration))} / {clock(p.duration)}
          </span>
          <span className="ml-auto font-mono text-[11px] text-ink-3">{cues.length} cues</span>
        </div>
      </div>

      {/* The caption "screen": always dark, like a video frame, so it reads the same in both themes. */}
      <div aria-live="off" className="grid min-h-[132px] place-items-center rounded-md border border-line bg-[oklch(0.18_0.005_70)] px-4 py-6 text-center">
        {cue ? (
          <div lang={lang} className="grid gap-1 text-[19px] leading-snug font-semibold text-[oklch(0.62_0.006_85)] sm:text-[22px]">
            {lineWords.map((ws, li) => (
              <p key={li}>
                {ws.map((wi, j) => {
                  const word = words[wi]!;
                  const state = tMs >= word.end_ms ? "past" : tMs >= word.start_ms ? "now" : "next";
                  return (
                    <span key={wi}>
                      <span className={cn("transition-colors duration-100", state !== "next" && "text-white", state === "now" && "underline decoration-2 underline-offset-[6px]")}>{word.text.trim()}</span>
                      {spaced && j < ws.length - 1 ? " " : ""}
                    </span>
                  );
                })}
              </p>
            ))}
          </div>
        ) : (
          <p className="font-mono text-[12px] text-[oklch(0.62_0.006_85)]">{p.time === 0 && !p.playing ? "Press play to watch the captions" : " "}</p>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="secondary" size="sm" onClick={() => download(`${base}.srt`, toSrt(cues), "application/x-subrip")}>
          <Download size={14} /> SRT
        </Button>
        <Button variant="secondary" size="sm" onClick={() => download(`${base}.vtt`, toVtt(cues), "text/vtt")}>
          <Download size={14} /> VTT
        </Button>
        <p className="text-[12.5px] text-ink-3">
          Fixed rule: up to {MAX_LINES} lines of {maxChars(lang)} characters, a new cue after a {PAUSE_MS} ms pause, a sentence end, or {MAX_CUE_MS / 1000} s.
        </p>
      </div>
    </div>
  );
}

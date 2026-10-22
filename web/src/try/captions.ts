/**
 * The benchmark's fixed caption line-break rule, ported from clipbench/scoring/captions.py (`build_cues`,
 * `split_lines`, `to_srt`, `to_vtt`). Keep the constants and order of checks in step with that file.
 * Lengths count code points, like Python's len().
 */

export type Word = { text: string; start_ms: number; end_ms: number };
export type Cue = {
  start_ms: number;
  end_ms: number;
  lines: string[];
  /** Index of the cue's first word in sortedWords(), how many words it holds, and how many sit on line one. */
  first: number;
  count: number;
  firstLine: number;
};

export const MAX_LINES = 2;
export const MAX_CHARS: Record<string, number> = { en: 42, es: 42, hi: 42, ja: 16, ko: 16 };
export const DEFAULT_MAX_CHARS = 42;
export const MAX_CUE_MS = 7000;
export const PAUSE_MS = 700;
const SENTENCE_END = [".", "?", "!", "。", "？", "！", "…"];
const COMMAS = [",", "、", "，"];
const UNSPACED = new Set(["ja"]);

const lang = (l: string) => String(l).split("-")[0]!.toLowerCase();
const len = (s: string) => Array.from(s).length;

export const maxChars = (language: string) => MAX_CHARS[lang(language)] ?? DEFAULT_MAX_CHARS;

export function join(tokens: string[], language: string): string {
  return tokens.filter(Boolean).join(UNSPACED.has(lang(language)) ? "" : " ");
}

/** One line if it fits, else two lines split at the boundary closest to the middle (comma preferred). */
export function splitLines(tokens: string[], language: string): string[] {
  const at = breakIndex(tokens, language);
  return at >= tokens.length ? [join(tokens, language)] : [join(tokens.slice(0, at), language), join(tokens.slice(at), language)];
}

/** Number of tokens on the first line (tokens.length when the cue is one line). */
export function breakIndex(tokens: string[], language: string): number {
  const text = join(tokens, language);
  const limit = maxChars(language);
  if (len(text) <= limit || tokens.length < 2) return tokens.length;
  let bestI = 1;
  let best: [number, number, number] | null = null;
  const total = len(text);
  for (let i = 1; i < tokens.length; i++) {
    const a = join(tokens.slice(0, i), language);
    const b = join(tokens.slice(i), language);
    const over = Math.max(len(a) - limit, 0) + Math.max(len(b) - limit, 0);
    const balance = Math.abs(len(a) - len(b));
    const comma = COMMAS.some((c) => tokens[i - 1]!.endsWith(c)) && balance <= total / 4;
    const score: [number, number, number] = [over, comma ? 0 : 1, balance];
    if (!best || score[0] < best[0] || (score[0] === best[0] && (score[1] < best[1] || (score[1] === best[1] && score[2] < best[2])))) {
      bestI = i;
      best = score;
    }
  }
  return bestI;
}

/** Timed words sorted by start (stable), blanks dropped. The player highlights words by index into this list. */
export function sortedWords(words: Word[]): Word[] {
  return words.filter((w) => w.text.trim()).map((w, i) => [w, i] as const).sort((a, b) => a[0].start_ms - b[0].start_ms || a[1] - b[1]).map(([w]) => w);
}

/** Cues from timed words with the fixed rule (captions.py docstring). */
export function buildCues(words: Word[], language: string): Cue[] {
  const ws = sortedWords(words);
  const limit = maxChars(language) * MAX_LINES;
  const cues: Cue[] = [];
  let first = 0;
  let current: Word[] = [];
  const flush = () => {
    if (!current.length) return;
    const tokens = current.map((w) => w.text.trim());
    const at = breakIndex(tokens, language);
    cues.push({ start_ms: current[0]!.start_ms, end_ms: Math.max(...current.map((w) => w.end_ms)), lines: splitLines(tokens, language), first, count: current.length, firstLine: at });
    first += current.length;
    current = [];
  };
  for (const w of ws) {
    if (current.length) {
      const prev = current[current.length - 1]!;
      const text = join([...current, w].map((x) => x.text.trim()), language);
      if (
        w.start_ms - prev.end_ms >= PAUSE_MS ||
        SENTENCE_END.some((e) => prev.text.trim().endsWith(e)) ||
        Math.max(w.end_ms, prev.end_ms) - current[0]!.start_ms > MAX_CUE_MS ||
        len(text) > limit
      ) {
        flush();
      }
    }
    current.push(w);
  }
  flush();
  return cues;
}

function ts(ms: number, sep: string): string {
  let r = Math.max(Math.trunc(ms), 0);
  const h = Math.floor(r / 3_600_000);
  r -= h * 3_600_000;
  const m = Math.floor(r / 60_000);
  r -= m * 60_000;
  const s = Math.floor(r / 1000);
  const f = r - s * 1000;
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${p(h)}:${p(m)}:${p(s)}${sep}${p(f, 3)}`;
}

export function toSrt(cues: Cue[]): string {
  const blocks = cues.map((c, i) => `${i + 1}\n${ts(c.start_ms, ",")} --> ${ts(c.end_ms, ",")}\n${c.lines.join("\n")}`);
  return blocks.join("\n\n") + (blocks.length ? "\n" : "");
}

export function toVtt(cues: Cue[]): string {
  const blocks = cues.map((c) => `${ts(c.start_ms, ".")} --> ${ts(c.end_ms, ".")}\n${c.lines.join("\n")}`);
  return "WEBVTT\n\n" + blocks.join("\n\n") + (blocks.length ? "\n" : "");
}

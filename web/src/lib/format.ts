/** Number formatting. Every number on the site goes through here so units stay consistent. */

export function pct(x: number | null | undefined, digits = 1) {
  if (x == null || Number.isNaN(x)) return "n/a";
  return `${(x * 100).toFixed(digits)}%`;
}

/** Percentage points, for differences between two rates. */
export function pts(x: number, digits = 1) {
  const v = Math.abs(x * 100).toFixed(digits);
  return `${v} pt${v === "1.0" ? "" : "s"}`;
}

export function usdPerHour(x: number | null | undefined) {
  if (x == null || Number.isNaN(x)) return "n/a";
  if (x === 0) return "$0";
  return x < 0.1 ? `$${x.toFixed(3)}` : `$${x.toFixed(2)}`;
}

export function ms(x: number | null | undefined) {
  if (x == null || Number.isNaN(x)) return "n/a";
  return x >= 1000 ? `${(x / 1000).toFixed(1)} s` : `${Math.round(x)} ms`;
}

export function int(x: number) {
  return new Intl.NumberFormat("en-US").format(x);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "19 Oct 2026". Fixed month names so every browser prints the same thing (en-GB says "Sept" in some). */
export function date(iso: string | number) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "n/a";
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

export function pValue(p: number) {
  return p < 0.001 ? "p < 0.001" : `p = ${p.toFixed(3)}`;
}

/** Signed percentage points, for paired differences: "+1.3 pts", "-0.4 pts". */
export function signedPts(x: number, digits = 1) {
  const v = (x * 100).toFixed(digits);
  return `${x > 0 ? "+" : x < 0 ? "-" : ""}${v.replace("-", "")} pts`;
}

/** Seconds as m:ss (clip durations). */
export function duration(s: number | null | undefined) {
  if (s == null) return "n/a";
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  return `${m}:${String(r).padStart(2, "0")}`;
}

/** Epoch milliseconds (Worker timestamps) as a short date. */
export function dateMs(t: number | null | undefined) {
  if (t == null) return "never";
  return date(t);
}

/** "3 days ago" style, coarse. */
export function ago(t: number | null | undefined) {
  if (t == null) return "never";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  const d = Math.floor(s / 86400);
  return d === 1 ? "yesterday" : d < 30 ? `${d} days ago` : dateMs(t);
}

const WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];
/** Small counts as words ("two"), larger as digits. Capitalise at the call site when it starts a sentence. */
export function words(n: number) {
  return WORDS[n] ?? int(n);
}

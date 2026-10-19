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

export function date(iso: string) {
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function pValue(p: number) {
  return p < 0.001 ? "p < 0.001" : `p = ${p.toFixed(3)}`;
}

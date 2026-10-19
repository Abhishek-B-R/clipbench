/**
 * Safe scale helpers. Math.min/Math.max over an empty list give Infinity/-Infinity, which then render as
 * "Infinity%" in legends and NaN in SVG paths. Every chart domain goes through these.
 */
export function extent(xs: readonly number[], fallback: [number, number] = [0, 1]): [number, number] {
  const f = xs.filter((x) => Number.isFinite(x));
  if (!f.length) return fallback;
  return [Math.min(...f), Math.max(...f)];
}

export function maxOf(xs: readonly number[], fallback = 0) {
  return extent(xs, [fallback, fallback])[1];
}

export function minOf(xs: readonly number[], fallback = 0) {
  return extent(xs, [fallback, fallback])[0];
}

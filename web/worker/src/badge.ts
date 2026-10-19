/**
 * SVG badges ("clipbench | 6.2% CER ja"). Flat, two panes, no scripts, no external references.
 * Text widths are estimated from a per-character table for 11px Verdana, the way shields-style badges do.
 */

const NARROW = new Set("fijlrt.,:;!|'()[] ");
const WIDE = new Set("mwMW%@");

export function textWidth(s: string): number {
  let w = 0;
  for (const ch of s) {
    if (NARROW.has(ch)) w += 4;
    else if (WIDE.has(ch)) w += 10;
    else if (ch >= "A" && ch <= "Z") w += 7.5;
    else w += 6.5;
  }
  return Math.ceil(w);
}

export function escapeXml(s: string): string {
  return s.replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" })[ch]!);
}

export type BadgeTone = "value" | "muted";

const COLORS: Record<BadgeTone, string> = { value: "#1f2328", muted: "#8c959f" };

export function badgeSvg(label: string, message: string, tone: BadgeTone = "value"): string {
  const pad = 6;
  const lw = textWidth(label) + pad * 2;
  const mw = textWidth(message) + pad * 2;
  const w = lw + mw;
  const l = escapeXml(label);
  const m = escapeXml(message);
  const title = `${l}: ${m}`;
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="20" role="img" aria-label="${title}">` +
    `<title>${title}</title>` +
    `<rect width="${lw}" height="20" fill="#555"/>` +
    `<rect x="${lw}" width="${mw}" height="20" fill="${COLORS[tone]}"/>` +
    `<g fill="#fff" font-family="Verdana,DejaVu Sans,sans-serif" font-size="11" text-anchor="middle">` +
    `<text x="${lw / 2}" y="14">${l}</text>` +
    `<text x="${lw + mw / 2}" y="14">${m}</text>` +
    `</g></svg>`
  );
}

/** "6.2%" from 0.0623. One decimal; below 0.1% shows "<0.1%" rather than a misleading 0.0%. */
export function percent(rate: number): string {
  const p = rate * 100;
  if (p > 0 && p < 0.05) return "<0.1%";
  return `${p.toFixed(1)}%`;
}

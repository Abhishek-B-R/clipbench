import type { Aggregate, Paired, Provider, Run, SliceKind, Tier } from "./types";

/** Plain names for clip tags (clipbench/models.py TAGS). */
export const TAG_LABEL: Record<string, string> = {
  background_music: "Music under speech",
  fast_speech: "Fast talkers",
  multiple_speakers: "Several speakers",
  slang_or_dialect: "Slang and dialect",
  code_switching: "Code-switching",
  noisy: "Noisy audio",
  caption_not_verbatim: "Loose captions",
  unusable: "Unusable",
};

/** Lowercase form for use inside a sentence. */
export const TAG_PHRASE: Record<string, string> = {
  background_music: "music under speech",
  fast_speech: "fast talkers",
  multiple_speakers: "several speakers",
  slang_or_dialect: "slang and dialect",
  code_switching: "code-switching",
  noisy: "noisy audio",
  caption_not_verbatim: "loose captions",
};

/** Order the clip-type views use. caption_not_verbatim is about the reference, not the audio, so it is left out. */
export const TAG_ORDER = ["background_music", "fast_speech", "slang_or_dialect", "multiple_speakers", "code_switching", "noisy"];

export const LANGUAGE_NAME: Record<string, string> = { ja: "Japanese", es: "Spanish", ko: "Korean", en: "English" };

export const TIER_LABEL: Record<Tier, string> = { verified: "Verified", creator: "Creator captions" };

export function primaryMetric(run: Run) {
  return run.metrics.find((m) => m.primary) ?? run.metrics[0]!;
}

/** Stable provider order (sorted ids) so a provider keeps one colour everywhere. Max 6 categorical slots, then grey. */
export function providerOrder(run: Run): string[] {
  return [...run.providers.map((p) => p.id)].sort();
}

export function providerColor(run: Run, id: string): string {
  const i = providerOrder(run).indexOf(id);
  return i >= 0 && i < 6 ? `var(--series-${i + 1})` : "var(--ink-3)";
}

export function providerById(run: Run, id: string): Provider | undefined {
  return run.providers.find((p) => p.id === id);
}

export function providerLabel(run: Run, id: string) {
  return providerById(run, id)?.label ?? id;
}

export interface SliceQuery {
  tier: Tier;
  metric: string;
  kind: SliceKind;
  slice: string;
}

export function aggregatesFor(run: Run, q: SliceQuery): Aggregate[] {
  return run.aggregates
    .filter((a) => a.tier === q.tier && a.metric === q.metric && a.slice_kind === q.kind && a.slice === q.slice)
    .sort((a, b) => a.error_rate - b.error_rate);
}

export function overall(run: Run, tier: Tier = "verified", metric = primaryMetric(run).name) {
  return aggregatesFor(run, { tier, metric, kind: "all", slice: "all" });
}

/** Paired result between x and y in a slice, oriented so diff = x minus y. */
export function pair(run: Run, q: SliceQuery, x: string, y: string) {
  const row: Paired | undefined = run.paired.find(
    (p) =>
      p.tier === q.tier && p.metric === q.metric && p.slice_kind === q.kind && p.slice === q.slice &&
      ((p.provider_a === x && p.provider_b === y) || (p.provider_a === y && p.provider_b === x)),
  );
  if (!row) return null;
  const flip = row.provider_a !== x;
  const xBetter = flip ? row.verdict === "b_better" : row.verdict === "a_better";
  const yBetter = flip ? row.verdict === "a_better" : row.verdict === "b_better";
  return {
    diff: flip ? -row.diff : row.diff,
    ci_low: flip ? -row.ci_high : row.ci_low,
    ci_high: flip ? -row.ci_low : row.ci_high,
    p: row.p_value,
    n: row.n_clips,
    better: xBetter ? x : yBetter ? y : null,
  };
}

/** Tags with data in this run, in display order. */
export function tagsInRun(run: Run, tier: Tier = "verified") {
  const present = new Set(run.aggregates.filter((a) => a.tier === tier && a.slice_kind === "tag").map((a) => a.slice));
  return TAG_ORDER.filter((t) => present.has(t));
}

export interface Headline {
  tag: string | null;
  winner: Aggregate;
  loser: Aggregate;
  /** 0..1, share of errors avoided: 1 - winner/loser */
  fewer: number;
  p: number;
  n: number;
}

/**
 * The landing page claim. For each clip type with enough clips, take the lowest-error model and compare it with
 * the best overall model that the paired test says is worse on that clip type. Pick the largest gap.
 * Falls back to the overall leader vs the runner-up when no clip type has a tested difference.
 */
export function headline(run: Run, tier: Tier = "verified"): Headline | null {
  const metric = primaryMetric(run).name;
  const ranks = overall(run, tier, metric).map((a) => a.provider);
  let best: Headline | null = null;
  const MIN_N = 30; // prefer a claim backed by a decent number of clips; fall back to any too-small slice
  for (const pass of [MIN_N, 15]) {
  if (best) break;
  for (const tag of tagsInRun(run, tier)) {
    const q: SliceQuery = { tier, metric, kind: "tag", slice: tag };
    const rows = aggregatesFor(run, q);
    const winner = rows[0];
    if (!winner || winner.too_small || winner.n_clips < pass) continue;
    for (const pid of ranks) {
      if (pid === winner.provider) continue;
      const loser = rows.find((r) => r.provider === pid);
      const t = pair(run, q, winner.provider, pid);
      if (!loser || !t || t.better !== winner.provider) continue;
      const fewer = 1 - winner.error_rate / loser.error_rate;
      if (!best || fewer > best.fewer) best = { tag, winner, loser, fewer, p: t.p, n: t.n };
      break;
    }
  }
  }
  if (best) return best;
  const rows = overall(run, tier, metric);
  if (rows.length < 2) return null;
  const t = pair(run, { tier, metric, kind: "all", slice: "all" }, rows[0]!.provider, rows[1]!.provider);
  return { tag: null, winner: rows[0]!, loser: rows[1]!, fewer: 1 - rows[0]!.error_rate / rows[1]!.error_rate, p: t?.p ?? 1, n: t?.n ?? rows[0]!.n_clips };
}

/** The clip type where a model's error rate rises the most relative to its own overall rate. */
export function breaksOn(run: Run, provider: string, tier: Tier = "verified") {
  const metric = primaryMetric(run).name;
  const base = run.aggregates.find((a) => a.provider === provider && a.tier === tier && a.metric === metric && a.slice_kind === "all");
  if (!base) return null;
  let worst: { tag: string; ratio: number; agg: Aggregate } | null = null;
  for (const a of run.aggregates) {
    if (a.provider !== provider || a.tier !== tier || a.metric !== metric || a.slice_kind !== "tag" || a.too_small) continue;
    if (!TAG_ORDER.includes(a.slice)) continue;
    const ratio = a.error_rate / base.error_rate;
    if (!worst || ratio > worst.ratio) worst = { tag: a.slice, ratio, agg: a };
  }
  return worst ? { ...worst, base } : null;
}

/** Best model per clip type, plus whether the lead is backed by the paired test against the runner-up. */
export function winnersByTag(run: Run, tier: Tier = "verified") {
  const metric = primaryMetric(run).name;
  return tagsInRun(run, tier).map((tag) => {
    const q: SliceQuery = { tier, metric, kind: "tag", slice: tag };
    const rows = aggregatesFor(run, q);
    const [first, second] = rows;
    const t = first && second ? pair(run, q, first.provider, second.provider) : null;
    return { tag, rows, first, second, clear: t?.better === first?.provider, tooSmall: Boolean(first?.too_small) };
  });
}

/** Lower-left frontier on (cost, error): models no other model beats on both. */
export function frontier(rows: Aggregate[]) {
  const sorted = [...rows].sort((a, b) => (a.cost_per_hour ?? 0) - (b.cost_per_hour ?? 0) || a.error_rate - b.error_rate);
  const out: Aggregate[] = [];
  let bestErr = Infinity;
  for (const r of sorted) {
    if (r.error_rate < bestErr) {
      out.push(r);
      bestErr = r.error_rate;
    }
  }
  return out;
}

export function totalClips(run: Run, tier: Tier = "verified") {
  return run.clips.filter((c) => c.tier === tier).length;
}

export function audioHours(run: Run, tier: Tier = "verified") {
  return run.clips.filter((c) => c.tier === tier).reduce((s, c) => s + (c.duration_s ?? 0), 0) / 3600;
}

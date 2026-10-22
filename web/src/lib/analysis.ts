import type { Aggregate, Clip, LanguageStatus, Paired, Provider, ProviderExclusion, RankEntry, Run, RunIndex, SliceKind, Tier } from "./types";
import { CONDITION_ORDER, LANGUAGES } from "./config";

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

export const LANGUAGE_NAME: Record<string, string> = { en: "English", ja: "Japanese", ko: "Korean", es: "Spanish", hi: "Hindi and Hinglish", fr: "French" };

export const TIER_LABEL: Record<Tier, string> = { verified: "Verified", creator: "Creator captions" };

/** Tiers that have clips in this run, verified first. */
export function tiersInRun(run: Run): Tier[] {
  const has = new Set(run.clips.map((c) => c.tier));
  return (["verified", "creator"] as Tier[]).filter((t) => has.has(t));
}

/** The tier a view opens on: verified when the run has it, else whatever exists (an early run may be creator only). */
export function defaultTier(run: Run): Tier {
  return tiersInRun(run)[0] ?? "verified";
}

/** Use a tier from the URL only if the run has it. */
export function pickTier(run: Run | undefined, wanted: string | null): Tier {
  if (!run) return (wanted as Tier) || "verified";
  const tiers = tiersInRun(run);
  return tiers.includes(wanted as Tier) ? (wanted as Tier) : defaultTier(run);
}

export function primaryMetric(run: Run) {
  return run.metrics.find((m) => m.primary) ?? run.metrics[0]!;
}

/** Use a metric from the URL only if the run has it, else the primary one (a stale or edited link never shows n/a everywhere). */
export function pickMetric(run: Run | undefined, wanted: string | null): string {
  if (!run) return wanted || "cer";
  return run.metrics.some((m) => m.name === wanted) ? wanted! : primaryMetric(run).name;
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

function sliceRows(run: Run, q: SliceQuery): Aggregate[] {
  return run.aggregates.filter((a) => a.tier === q.tier && a.metric === q.metric && a.slice_kind === q.kind && a.slice === q.slice);
}

/**
 * Ranked rows of a slice: eligible providers of a published language only (docs/V3.md, "Publication gate fields").
 * Slice all follows the exporter's `ranking` exactly. Other slices have no exported order, so they use the same rule
 * as clipbench.report.rules.rank_order (error rate ascending, ties by provider id). Empty when unpublished.
 */
export function aggregatesFor(run: Run, q: SliceQuery): Aggregate[] {
  if (!isPublished(run)) return [];
  if (q.kind === "all" && q.slice === "all") return overall(run, q.tier, q.metric);
  const ok = eligibleProviders(run);
  return sliceRows(run, q)
    .filter((a) => ok.has(a.provider) && Number.isFinite(a.error_rate))
    .sort((a, b) => a.error_rate - b.error_rate || (a.provider < b.provider ? -1 : a.provider > b.provider ? 1 : 0));
}

/** The exporter's ranking for a tier and metric (slice all). Empty when unpublished or missing (older exports). */
export function ranking(run: Run, tier: Tier, metric: string): RankEntry[] {
  if (!isPublished(run)) return [];
  const rows = run.ranking?.[tier]?.[metric];
  if (!Array.isArray(rows)) return [];
  const ok = eligibleProviders(run);
  return rows.filter((r) => ok.has(r.provider)).sort((a, b) => a.rank - b.rank);
}

/** Overall rows (slice all) in the exporter's ranking order, eligible providers only. */
export function overall(run: Run, tier: Tier = defaultTier(run), metric = primaryMetric(run).name): Aggregate[] {
  const rows = sliceRows(run, { tier, metric, kind: "all", slice: "all" });
  return ranking(run, tier, metric).flatMap((r) => rows.filter((a) => a.provider === r.provider).slice(0, 1));
}

/** The exporter's 1-based rank of a provider (slice all), or null when it is not ranked. */
export function rankOf(run: Run, provider: string, tier: Tier = defaultTier(run), metric = primaryMetric(run).name): number | null {
  return ranking(run, tier, metric).find((r) => r.provider === provider)?.rank ?? null;
}

/**
 * Paired result between x and y in a slice, oriented so diff = x minus y. Only tests between two eligible providers
 * of a published language. `better` comes from the Benjamini-Hochberg adjusted verdict; an older export without
 * `p_adjusted` never yields a winner.
 */
export function pair(run: Run, q: SliceQuery, x: string, y: string) {
  if (!isPublished(run)) return null;
  const ok = eligibleProviders(run);
  if (!ok.has(x) || !ok.has(y)) return null;
  const row: Paired | undefined = run.paired.find(
    (p) =>
      p.tier === q.tier && p.metric === q.metric && p.slice_kind === q.kind && p.slice === q.slice &&
      ((p.provider_a === x && p.provider_b === y) || (p.provider_a === y && p.provider_b === x)),
  );
  if (!row) return null;
  const flip = row.provider_a !== x;
  const adjusted = typeof row.p_adjusted === "number";
  const verdict = adjusted ? row.verdict : "no_difference";
  const xBetter = flip ? verdict === "b_better" : verdict === "a_better";
  const yBetter = flip ? verdict === "a_better" : verdict === "b_better";
  const cl = row.ci_low_cluster;
  const ch = row.ci_high_cluster;
  return {
    diff: flip ? -row.diff : row.diff,
    ci_low: flip ? -row.ci_high : row.ci_low,
    ci_high: flip ? -row.ci_low : row.ci_high,
    /** Benjamini-Hochberg adjusted p value (the one behind the verdict). */
    p: adjusted ? (row.p_adjusted as number) : row.p_value,
    p_raw: row.p_value,
    n: row.n_clips,
    better: xBetter ? x : yBetter ? y : null,
    /** Channel-clustered interval of the difference, same orientation, when the export has channels. */
    cluster: cl != null && ch != null ? { ci_low: flip ? -ch : cl, ci_high: flip ? -cl : ch, n_channels: row.n_channels ?? null } : null,
  };
}

/** Tags with data in this run, in display order. */
export function tagsInRun(run: Run, tier: Tier = defaultTier(run)) {
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
export function headline(run: Run, tier: Tier = defaultTier(run)): Headline | null {
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
export function breaksOn(run: Run, provider: string, tier: Tier = defaultTier(run)) {
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
export function winnersByTag(run: Run, tier: Tier = defaultTier(run)) {
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

export function totalClips(run: Run, tier: Tier = defaultTier(run)) {
  return run.clips.filter((c) => c.tier === tier).length;
}

export function audioHours(run: Run, tier: Tier = defaultTier(run)) {
  return run.clips.filter((c) => c.tier === tier).reduce((s, c) => s + (c.duration_s ?? 0), 0) / 3600;
}

// ---------------------------------------------------------------- links

/** YouTube link for a clip. Windows cut from a longer video open at their start time. */
/**
 * Link to the source video. A window's clip id is `<video_id>@<start_ms>-<end_ms>` (docs/V2.md), so the platform id
 * is the part before "@" and the window opens at its start (from `start_s`, else from the id).
 */
export function youtubeUrl(clip: Pick<Clip, "video_id"> & Partial<Pick<Clip, "kind" | "start_s">>) {
  const m = /^(.+?)@(\d+)-(\d+)$/.exec(clip.video_id);
  const id = m?.[1] ?? clip.video_id;
  const start = clip.start_s ?? (m ? Number(m[2]) / 1000 : null);
  if (m || clip.kind === "window") {
    const t = start != null && Number.isFinite(start) ? `&t=${Math.floor(start)}s` : "";
    return `https://www.youtube.com/watch?v=${encodeURIComponent(id)}${t}`;
  }
  return `https://www.youtube.com/shorts/${encodeURIComponent(id)}`;
}

export function clipById(run: Run, id: string) {
  return run.clips.find((c) => c.video_id === id);
}

// ---------------------------------------------------------------- one provider

export type PairResult = NonNullable<ReturnType<typeof pair>>;

/** This model against every other model in a slice, in overall rank order. */
export function pairsFor(run: Run, q: SliceQuery, x: string) {
  const order = overall(run, q.tier, q.metric).map((a) => a.provider);
  return order
    .filter((y) => y !== x)
    .map((y) => ({ other: y, t: pair(run, q, x, y) }))
    .filter((r): r is { other: string; t: PairResult } => r.t != null);
}

export function median(xs: number[]) {
  if (!xs.length) return NaN;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m]! : (s[m - 1]! + s[m]!) / 2;
}

/** Per clip type: this model's row, the field median (other models) and the best model. */
export function tagProfile(run: Run, provider: string, tier: Tier, metric: string, kind: Exclude<SliceKind, "all"> = "tag") {
  const slices =
    kind === "tag"
      ? tagsInRun(run, tier)
      : kind === "duration"
        ? ["<15s", "15-30s", "30-60s", "60-90s"]
        : kind === "audio_condition"
          ? ["music", "no_music"]
          : kind === "condition"
          ? CONDITION_ORDER
          : [...new Set(run.aggregates.filter((a) => a.tier === tier && a.slice_kind === kind).map((a) => a.slice))].sort((a, b) => (a === "other" ? 1 : b === "other" ? -1 : a.localeCompare(b)));
  return slices
    .map((slice) => {
      const rows = aggregatesFor(run, { tier, metric, kind, slice });
      const mine = rows.find((r) => r.provider === provider);
      const others = rows.filter((r) => r.provider !== provider);
      return { slice, mine, rows, fieldMedian: median(others.map((r) => r.error_rate)), best: rows[0], rank: rows.findIndex((r) => r.provider === provider) + 1 };
    })
    .filter((r) => r.mine);
}

export interface RankPoint {
  run_id: string;
  created_at: string;
  model_id: string;
  rank: number;
  of: number;
  agg: Aggregate;
}

/** Rank and error rate of one provider in every run where it appears (oldest first). */
export function rankHistory(runs: Run[], provider: string, tier: Tier, metric: string): RankPoint[] {
  const out: RankPoint[] = [];
  for (const r of runs) {
    const rows = overall(r, tier, metric);
    const i = rows.findIndex((a) => a.provider === provider);
    if (i < 0) continue;
    out.push({ run_id: r.run_id, created_at: r.created_at, model_id: rows[i]!.model_id, rank: i + 1, of: rows.length, agg: rows[i]! });
  }
  return out;
}

/** Mean run-to-run disagreement and latency spread from the stability table. */
export function stabilityFor(run: Run, provider: string) {
  const rows = run.stability.filter((s) => s.provider === provider);
  if (!rows.length) return null;
  return {
    n: rows.length,
    repeats: rows[0]!.n_repeats,
    disagreement: rows.reduce((t, s) => t + s.text_disagreement, 0) / rows.length,
    latencyStd: rows.reduce((t, s) => t + (s.latency_std ?? 0), 0) / rows.length,
  };
}

// ---------------------------------------------------------------- per clip

/** video_id -> provider -> error rate, for one tier and the primary metric. */
export function perClipMatrix(run: Run, tier: Tier, metric = primaryMetric(run).name) {
  const m = new Map<string, Map<string, number>>();
  for (const r of run.per_clip) {
    if (r.tier !== tier || r.metric !== metric) continue;
    let row = m.get(r.video_id);
    if (!row) m.set(r.video_id, (row = new Map()));
    row.set(r.provider, r.error_rate);
  }
  return m;
}

/** Seeded RNG (mulberry32) so bootstrap figures are stable between visits. */
export function seeded(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function quantile(sorted: number[], q: number) {
  const i = (sorted.length - 1) * q;
  const lo = Math.floor(i);
  const hi = Math.ceil(i);
  return sorted[lo]! + (sorted[hi]! - sorted[lo]!) * (i - lo);
}

/** Percentile bootstrap of a mean. Returns the resampled means too (for the methodology figure). */
export function bootstrapMean(xs: number[], seed = 7, B = 1000) {
  const r = seeded(seed);
  const means: number[] = [];
  for (let b = 0; b < B; b++) {
    let s = 0;
    for (let i = 0; i < xs.length; i++) s += xs[Math.floor(r() * xs.length)]!;
    means.push(s / Math.max(xs.length, 1));
  }
  const sorted = [...means].sort((a, b) => a - b);
  const mean = xs.reduce((a, b) => a + b, 0) / Math.max(xs.length, 1);
  return { mean, ci_low: quantile(sorted, 0.025), ci_high: quantile(sorted, 0.975), samples: means };
}

/**
 * Paired bootstrap over clips both models scored (METHODOLOGY.md, "Paired comparisons"): one set of indices per
 * resample applied to both, diff = a minus b, p = twice the share with the opposite sign, capped at 1.
 */
export function pairedBootstrap(a: number[], b: number[], seed = 7, B = 1000) {
  const diffs = a.map((x, i) => x - b[i]!);
  const { mean, ci_low, ci_high, samples } = bootstrapMean(diffs, seed, B);
  const opposite = samples.filter((m) => Math.sign(m) !== Math.sign(mean) || m === 0).length;
  const p = mean === 0 ? 1 : Math.min(1, (2 * opposite) / B);
  return { diff: mean, ci_low, ci_high, p, n: diffs.length, samples, tied: ci_low <= 0 && ci_high >= 0 };
}

// ---------------------------------------------------------------- picker

export type LatencyNeed = "any" | "3000" | "1500";

export interface PickerInput {
  tags: string[];
  /** Max USD per hour of audio; null = no limit. */
  budget: number | null;
  latency: LatencyNeed;
  /** true = audio must stay on your machine. */
  local: boolean;
}

export interface PickerRow {
  provider: string;
  score: number;
  ci_low: number;
  ci_high: number;
  price: number;
  p50: number | null;
  uploads: boolean;
  excluded: string[];
  /** Rank on each selected clip type (aggregates), for the reasoning. */
  tagRanks: { tag: string; rank: number; of: number; tooSmall: boolean }[];
  /** Paired test against the pick (only for eligible models other than the pick). */
  vsPick?: ReturnType<typeof pairedBootstrap>;
}

/**
 * "Which model for my clips". Score = mean per-clip error over the verified clips that have any of the chosen clip
 * types (all clips when none is chosen), with a bootstrap interval. Constraints exclude, they never reweight.
 * Every eligible model is then paired-tested against the pick on exactly those clips.
 */
export function recommend(run: Run, input: PickerInput) {
  const tier: Tier = defaultTier(run);
  const metric = primaryMetric(run).name;
  const clips = run.clips.filter((c) => c.tier === tier && (input.tags.length === 0 || c.tags.some((t) => input.tags.includes(t))));
  const ids = clips.map((c) => c.video_id);
  const m = perClipMatrix(run, tier, metric);
  const all = overall(run, tier, metric);
  // Only eligible providers of a published language are scored and ranked here; the rest are listed as not ranked.
  const ok = isPublished(run) ? eligibleProviders(run) : new Set<string>();
  const providers = run.providers.filter((p) => ok.has(p.id) && ids.every((id) => m.get(id)?.has(p.id)));
  const vec = (p: string) => ids.map((id) => m.get(id)!.get(p)!);
  const rows: PickerRow[] = providers.map((p, i) => {
    const xs = vec(p.id);
    const bs = bootstrapMean(xs, 11 + i, 600);
    const agg = all.find((a) => a.provider === p.id);
    const p50 = agg?.latency_p50 ?? null;
    const excluded: string[] = [];
    if (input.local && p.uploads_audio) excluded.push("Sends audio to a server");
    if (input.budget != null && p.usd_per_hour > input.budget + 1e-9) excluded.push("Over budget");
    if (input.latency !== "any" && (p50 == null || p50 > Number(input.latency))) excluded.push("Too slow");
    const tagRanks = input.tags.map((tag) => {
      const r = aggregatesFor(run, { tier, metric, kind: "tag", slice: tag });
      const k = r.findIndex((a) => a.provider === p.id);
      return { tag, rank: k + 1, of: r.length, tooSmall: Boolean(r[k]?.too_small) };
    });
    return { provider: p.id, score: bs.mean, ci_low: bs.ci_low, ci_high: bs.ci_high, price: p.usd_per_hour, p50, uploads: p.uploads_audio, excluded, tagRanks };
  });
  rows.sort((a, b) => a.score - b.score);
  const eligible = rows.filter((r) => r.excluded.length === 0);
  const pick = eligible[0];
  if (pick) {
    const pv = vec(pick.provider);
    for (const r of rows) if (r !== pick) r.vsPick = pairedBootstrap(vec(r.provider), pv, 97, 1000);
  }
  const tied = pick ? eligible.filter((r) => r !== pick && r.vsPick?.tied) : [];
  const cheaperTie = tied.filter((r) => r.price < (pick?.price ?? 0)).sort((a, b) => a.price - b.price)[0];
  const betterExcluded = pick ? rows.filter((r) => r.excluded.length > 0 && r.score < pick.score && r.vsPick && !r.vsPick.tied) : [];
  return { rows, eligible, pick, tied, cheaperTie, betterExcluded, nClips: ids.length };
}

// ---------------------------------------------------------------- changelog

export interface RunChange {
  run: Run;
  prev?: Run;
  added: string[];
  removed: string[];
  changed: { provider: string; from: string; to: string }[];
  leader?: Aggregate;
  prevLeader?: Aggregate;
  deltas: { provider: string; now: Aggregate; before?: Aggregate; rank: number; prevRank?: number }[];
}

/** What changed from one run to the next: models added, removed or re-versioned, the leader, rank and error deltas. */
export function runChanges(runs: Run[], wanted?: Tier): RunChange[] {
  return runs.map((run, i) => {
    const tier = wanted ?? defaultTier(run);
    const prev = i > 0 ? runs[i - 1] : undefined;
    const metric = primaryMetric(run).name;
    const ids = new Set(run.providers.map((p) => p.id));
    const prevIds = new Set(prev?.providers.map((p) => p.id) ?? []);
    const now = overall(run, tier, metric);
    const before = prev ? overall(prev, tier, metric) : [];
    return {
      run,
      prev,
      added: prev ? [...ids].filter((x) => !prevIds.has(x)) : [...ids],
      removed: [...prevIds].filter((x) => !ids.has(x)),
      changed: prev
        ? run.providers.flatMap((p) => {
            const old = prev.providers.find((q) => q.id === p.id);
            return old && old.model_id !== p.model_id ? [{ provider: p.id, from: old.model_id, to: p.model_id }] : [];
          })
        : [],
      leader: now[0],
      prevLeader: before[0],
      deltas: now.map((a, k) => {
        const j = before.findIndex((b) => b.provider === a.provider);
        return { provider: a.provider, now: a, before: before[j], rank: k + 1, prevRank: j >= 0 ? j + 1 : undefined };
      }),
    };
  });
}


// ---------------------------------------------------------------- publication gate (docs/V3.md, "Publication gate fields")

/**
 * The exporter's status, read as is. Fails closed: a status without the three `gate` flags (an older export) is not
 * published, and `published` must agree with every flag. Nothing here re-derives eligibility or the gate.
 */
export function normalizeStatus(st: LanguageStatus): LanguageStatus {
  const g = st.gate;
  const flags = Boolean(g && g.verified_clips_ok === true && g.providers_ok === true && g.diversity_ok === true);
  return {
    ...st,
    verified_clips: Number.isFinite(st.verified_clips) ? st.verified_clips : 0,
    providers_ok: Number.isFinite(st.providers_ok) ? st.providers_ok : 0,
    published: st.published === true && flags,
  };
}

/** True when the status carries the v3 gate fields (so the gate panel can show all three conditions). */
export function hasGate(st: LanguageStatus | undefined): st is LanguageStatus & Required<Pick<LanguageStatus, "gate">> {
  return Boolean(st?.gate && typeof st.gate === "object");
}

/** Status of a run's language from `run.languages` (the exporter). Missing: not published, nothing eligible. */
export function runLanguageStatus(run: Run): LanguageStatus {
  const given = run.languages?.find((l) => l.lang === run.language);
  if (given) return normalizeStatus(given);
  return { lang: run.language, verified_clips: run.clips.filter((c) => c.tier === "verified").length, providers_ok: 0, published: false };
}

export function isPublished(run: Run) {
  return runLanguageStatus(run).published;
}

const eligibleCache = new WeakMap<Run, Set<string>>();

/**
 * Providers that may be ranked or compared: listed in the status's `providers_eligible` and marked `eligible: true` on
 * their aggregates rows. Empty for an older export without these fields.
 */
export function eligibleProviders(run: Run): Set<string> {
  let out = eligibleCache.get(run);
  if (out) return out;
  const st = run.languages?.find((l) => l.lang === run.language);
  const listed = new Set(Array.isArray(st?.providers_eligible) ? st.providers_eligible : []);
  const flagged = new Set(run.aggregates.filter((a) => a.eligible === true).map((a) => a.provider));
  const denied = new Set(run.aggregates.filter((a) => a.eligible !== true).map((a) => a.provider));
  out = new Set([...listed].filter((p) => flagged.has(p) && !denied.has(p)));
  eligibleCache.set(run, out);
  return out;
}

export function isEligible(run: Run, provider: string) {
  return eligibleProviders(run).has(provider);
}

/** A provider's coverage of the verified evaluation clips (0..1), from the export, or null. */
export function coverageOf(run: Run, provider: string): number | null {
  const st = run.languages?.find((l) => l.lang === run.language);
  const ex = st?.providers_excluded?.find((e) => e.provider === provider);
  if (ex && typeof ex.coverage === "number") return ex.coverage;
  const row = run.aggregates.find((a) => a.provider === provider && typeof a.coverage === "number");
  return row ? (row.coverage as number) : null;
}

const NO_GATE_REASON = "This export has no eligibility data, so the provider is not ranked.";

/**
 * Every provider in the run that is not eligible, with its coverage and the exporter's reason, sorted by id. Providers
 * the export lists in `providers_excluded` come first as written; any other provider seen in the run (an older export)
 * is listed with a generic reason. These are shown in a "Not ranked" group, never in a ranking.
 */
export function excludedProviders(run: Run): ProviderExclusion[] {
  const st = run.languages?.find((l) => l.lang === run.language);
  const ok = eligibleProviders(run);
  const listed = (Array.isArray(st?.providers_excluded) ? st.providers_excluded : []).filter((e) => e && typeof e.provider === "string" && !ok.has(e.provider));
  const seen = new Set(listed.map((e) => e.provider));
  const others = [...new Set([...run.providers.map((p) => p.id), ...run.aggregates.map((a) => a.provider)])]
    .filter((p) => !ok.has(p) && !seen.has(p))
    .map((p): ProviderExclusion => ({ provider: p, coverage: coverageOf(run, p), n_scored: null, n_skipped: null, n_error: null, reason: NO_GATE_REASON }));
  return [...listed, ...others].sort((a, b) => (a.provider < b.provider ? -1 : a.provider > b.provider ? 1 : 0));
}

/** The exclusion entry of one provider, or null when it is eligible. */
export function exclusionOf(run: Run, provider: string): ProviderExclusion | null {
  if (isEligible(run, provider)) return null;
  return excludedProviders(run).find((e) => e.provider === provider) ?? { provider, coverage: coverageOf(run, provider), n_scored: null, n_skipped: null, n_error: null, reason: NO_GATE_REASON };
}

const V3_KEYS = ["streaming", "semantic", "drift", "history", "sweeps", "formatting", "lattice", "longform", "compute", "diarization", "lid", "entities", "style", "reliability", "captions"] as const;

/** Drops rows (`provider` not in `ok`) and per-provider keys (a key in `bad`, as in history.series) at any depth. */
function dropIneligible(value: unknown, ok: Set<string>, bad: Set<string>): unknown {
  if (Array.isArray(value))
    return value
      .filter((v) => !(v && typeof v === "object" && typeof (v as { provider?: unknown }).provider === "string" && !ok.has((v as { provider: string }).provider)))
      .map((v) => dropIneligible(v, ok, bad));
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).filter(([k]) => !bad.has(k)).map(([k, v]) => [k, dropIneligible(v, ok, bad)]));
  return value;
}

const rankedCache = new WeakMap<Run, Run>();

/**
 * The run as a ranked view sees it: every v3 section row (anything with a `provider`) of a provider that is not
 * eligible is dropped, so the views beyond accuracy can never rank an excluded provider. `providers` stays whole so
 * colours do not shift; views that list providers filter with isEligible.
 */
export function rankedRun(run: Run): Run {
  let out = rankedCache.get(run);
  if (out) return out;
  const ok = isPublished(run) ? eligibleProviders(run) : new Set<string>();
  const copy: Run = { ...run };
  const bad = new Set([...run.providers.map((p) => p.id), ...run.aggregates.map((a) => a.provider)].filter((p) => !ok.has(p)));
  for (const k of V3_KEYS) if (run[k] != null) (copy as unknown as Record<string, unknown>)[k] = dropIneligible(run[k], ok, bad);
  rankedCache.set(copy, copy);
  rankedCache.set(run, copy);
  return copy;
}

export interface LanguageProgress extends LanguageStatus {
  /** Latest run for this language, if any. */
  latestRun?: string;
  latestAt?: string;
  /** Clips scored on any tier in the latest run (creator captions included). */
  scored: number;
}

/**
 * Progress for every v2 language, from index.json only (no run fetch): `index.languages` or the run summary's
 * `languages`, read through normalizeStatus. Without the exporter's status a language is not published.
 */
export function languageProgress(index: RunIndex | undefined): LanguageProgress[] {
  const runs = [...(index?.runs ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at));
  return LANGUAGES.map((lang) => {
    const r = runs.find((x) => x.language === lang);
    const given = index?.languages?.find((l) => l.lang === lang) ?? r?.languages?.find((l) => l.lang === lang);
    const scored = r ? Object.values(r.n_clips).reduce<number>((t, n) => t + (n ?? 0), 0) : 0;
    if (given) return { ...normalizeStatus(given), latestRun: r?.run_id, latestAt: r?.created_at, scored };
    return { lang, verified_clips: r?.n_clips.verified ?? 0, providers_ok: 0, published: false, latestRun: r?.run_id, latestAt: r?.created_at, scored };
  });
}

export const CATEGORY_KEYS = ["talking_head", "vlog_music", "gaming", "comedy_skit", "cooking_tutorial", "news_explainer"] as const;

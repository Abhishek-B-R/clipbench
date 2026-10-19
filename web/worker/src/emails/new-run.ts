import { button, escapeHtml, layout, type Email } from "../email";

export type RunSummary = {
  run_id: string;
  language: string;
  created_at: string | null;
  tier: string | null;
  metric: string | null;
  /** provider id -> {rank (1 = lowest error), error_rate} on the headline tier and primary metric */
  ranks: Map<string, { rank: number; error_rate: number | null; label: string }>;
  n_providers: number;
};

type RunJson = {
  run_id?: string;
  language?: string;
  created_at?: string;
  metrics?: { name: string; primary: boolean }[];
  providers?: { id: string; label?: string }[];
  aggregates?: { provider: string; tier: string; metric: string; slice_kind: string; error_rate: number | null }[];
};

const LANGUAGES: Record<string, string> = { ja: "Japanese", es: "Spanish", ko: "Korean" };

export function summarize(runId: string, run: RunJson): RunSummary {
  const metric = run.metrics?.find((m) => m.primary)?.name ?? null;
  const tiers = new Set((run.aggregates ?? []).map((a) => a.tier));
  const tier = tiers.has("verified") ? "verified" : ([...tiers][0] ?? null);
  const labels = new Map((run.providers ?? []).map((p) => [p.id, p.label ?? p.id]));
  const rows = (run.aggregates ?? [])
    .filter((a) => a.tier === tier && a.metric === metric && a.slice_kind === "all")
    .sort((a, b) => (a.error_rate ?? Infinity) - (b.error_rate ?? Infinity));
  const ranks = new Map<string, { rank: number; error_rate: number | null; label: string }>();
  rows.forEach((r, i) => ranks.set(r.provider, { rank: i + 1, error_rate: r.error_rate, label: labels.get(r.provider) ?? r.provider }));
  return {
    run_id: run.run_id ?? runId,
    language: run.language ?? "",
    created_at: run.created_at ?? null,
    tier,
    metric,
    ranks,
    n_providers: labels.size || rows.length,
  };
}

const pct = (x: number | null) => (x === null || !Number.isFinite(x) ? "n/a" : `${(x * 100).toFixed(1)}%`);

export function newRunEmail(s: RunSummary, watched: string[], siteUrl: string): Omit<Email, "to"> {
  const lang = LANGUAGES[s.language] ?? s.language;
  const runUrl = `${siteUrl}/leaderboard?run=${encodeURIComponent(s.run_id)}`;
  const accountUrl = `${siteUrl}/account`;
  const subject = `New clipbench run: ${lang} speech-to-text results`;
  const mine = watched
    .map((pid) => ({ pid, r: s.ranks.get(pid) }))
    .filter((x): x is { pid: string; r: NonNullable<typeof x.r> } => x.r !== undefined)
    .sort((a, b) => a.r.rank - b.r.rank);

  const intro = `A new benchmark run is published: ${s.n_providers} speech-to-text models on ${lang} short-form video.`;
  const scope = s.tier && s.metric ? `Ranks below are on the ${s.tier} tier, ${s.metric}, lower is better.` : "";
  const textLines = [intro, ""];
  if (mine.length) {
    textLines.push("Your watchlist:", ...mine.map((m) => `  #${m.r.rank} ${m.r.label}: ${pct(m.r.error_rate)}`), "", scope, "");
  }
  textLines.push(
    "Rankings are not significance tests; see the paired comparisons on the site before drawing conclusions.",
    "",
    `See the results: ${runUrl}`,
    "",
    `You get this because "email me about new runs" is on. Turn it off: ${accountUrl}`,
  );

  const rowsHtml = mine
    .map(
      (m) =>
        `<tr><td style="padding:6px 12px 6px 0;color:#6b6b66;">#${m.r.rank}</td><td style="padding:6px 12px 6px 0;">${escapeHtml(m.r.label)}</td><td style="padding:6px 0;text-align:right;font-variant-numeric:tabular-nums;">${pct(m.r.error_rate)}</td></tr>`,
    )
    .join("");
  const watchHtml = mine.length
    ? `<p style="margin:20px 0 6px;font-weight:600;">Your watchlist</p>
<table style="border-collapse:collapse;font-size:14px;">${rowsHtml}</table>
<p style="margin:6px 0 0;color:#6b6b66;font-size:13px;">${escapeHtml(scope)}</p>`
    : "";
  const html = layout(
    subject,
    `<p style="margin:0;">${escapeHtml(intro)}</p>
${watchHtml}
${button(runUrl, "See the results")}
<p style="margin:0;color:#6b6b66;font-size:13px;">Rankings are not significance tests; check the paired comparisons before drawing conclusions.</p>`,
    `You get this because "email me about new runs" is on. <a href="${escapeHtml(accountUrl)}" style="color:#6b6b66;">Change alerts</a>`,
  );
  return { subject, text: textLines.join("\n"), html };
}

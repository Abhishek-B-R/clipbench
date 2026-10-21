import { button, escapeHtml, layout, type Email } from "../email";
import { primaryMetric, ranking, type Run } from "../gate";

export type RunSummary = {
  run_id: string;
  language: string;
  created_at: string | null;
  tier: string | null;
  metric: string | null;
  /** provider id -> {rank, error_rate} from the export's ranking (headline tier, primary metric), eligible providers only */
  ranks: Map<string, { rank: number; error_rate: number | null; label: string }>;
  n_providers: number;
};

type RunJson = Partial<Run>;

const LANGUAGES: Record<string, string> = { en: "English", ja: "Japanese", ko: "Korean", es: "Spanish", hi: "Hindi" };

/** Ranks from the export's `ranking` (eligible providers of a published language only, docs/V3.md). */
export function summarize(runId: string, run: RunJson): RunSummary {
  const r = { run_id: runId, language: "", ...run } as Run;
  const metric = primaryMetric(r) ?? null;
  const tiers = new Set((run.aggregates ?? []).map((a) => a.tier));
  const tier = tiers.has("verified") ? "verified" : ([...tiers][0] ?? null);
  const labels = new Map((run.providers ?? []).map((p) => [p.id, p.label ?? p.id]));
  const rows = tier && metric ? ranking(r, r.language, tier, metric) : [];
  const ranks = new Map<string, { rank: number; error_rate: number | null; label: string }>();
  for (const x of rows) ranks.set(x.provider, { rank: x.rank, error_rate: x.error_rate, label: labels.get(x.provider) ?? x.provider });
  return {
    run_id: run.run_id ?? runId,
    language: run.language ?? "",
    created_at: run.created_at ?? null,
    tier,
    metric,
    ranks,
    n_providers: rows.length,
  };
}

const pct = (x: number | null) => (x === null || !Number.isFinite(x) ? "n/a" : `${(x * 100).toFixed(1)}%`);

export function newRunEmail(s: RunSummary, watched: string[], siteUrl: string): Omit<Email, "to"> {
  const lang = LANGUAGES[s.language] ?? s.language;
  // The leaderboard selects a run by language (?lang=), the latest run for it.
  const runUrl = `${siteUrl}/leaderboard?lang=${encodeURIComponent(s.language)}`;
  const accountUrl = `${siteUrl}/account`;
  const subject = `New clipbench run: ${lang} speech-to-text results`;
  const mine = watched
    .map((pid) => ({ pid, r: s.ranks.get(pid) }))
    .filter((x): x is { pid: string; r: NonNullable<typeof x.r> } => x.r !== undefined)
    .sort((a, b) => a.r.rank - b.r.rank);

  const intro = `A new benchmark run is published: ${s.n_providers} ranked speech-to-text models on ${lang} short-form video.`;
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

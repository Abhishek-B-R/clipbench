import { useSearchParams } from "react-router";
import { ArrowUpRight, Check, Minus } from "lucide-react";
import type { Run } from "@/lib/types";
import { useRun } from "@/lib/data";
import { providerColor } from "@/lib/analysis";
import { LANGUAGES } from "@/lib/config";
import { FEATURE_KEYS, FEATURE_LABEL, pricePer1000Min, safeUrl, supports, v3, type FeatureCell, type FeatureKey, type FeatureProvider } from "@/lib/v3";
import { date, int, usdPerHour } from "@/lib/format";
import { cn } from "@/lib/cn";
import { PageHeader } from "@/components/layout/PageHeader";
import { Figure } from "@/components/ui/Card";
import { Chip } from "@/components/ui/Chip";
import { SeriesDot } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/State";
import { Tooltip } from "@/components/ui/Tooltip";
import { Link } from "react-router";

/** Features a builder filters on first; each chip keeps only providers that offer it. */
const NEEDS: FeatureKey[] = ["streaming", "word_timestamps", "diarization", "language_id", "custom_vocabulary"];

/**
 * Feature matrix (B20): what each provider offers, from pricing.toml's [<id>.features] tables via the export. Every
 * cell links to the provider's documentation and carries the date it was checked. Not a ranking, so it is not
 * gated; rows are alphabetical.
 */
export default function Features() {
  const state = useRun();
  const run = state.data?.run;
  const f = run ? v3(run).features : null;
  const [params, setParams] = useSearchParams();
  const needs = (params.get("needs") ?? "").split(",").filter((k): k is FeatureKey => (NEEDS as string[]).includes(k));
  const toggle = (k: FeatureKey) => {
    const next = needs.includes(k) ? needs.filter((x) => x !== k) : [...needs, k];
    const p = new URLSearchParams(params);
    if (next.length) p.set("needs", next.join(","));
    else p.delete("needs");
    setParams(p, { replace: true });
  };
  const rows = (f?.providers ?? [])
    .filter((p) => needs.every((k) => supports(f, p.id, k) === true))
    .sort((a, b) => label(run, a).localeCompare(label(run, b)));
  const keys = FEATURE_KEYS.filter((k) => f?.providers.some((p) => p.cells[k]));

  return (
    <>
      <PageHeader
        eyebrow="Providers / feature matrix"
        title="What each provider offers"
        description="Languages, batch and streaming, word timestamps, speaker labels, language detection, custom vocabulary, file limits, data retention and price. Every cell links to the provider's own documentation and shows when we last checked it."
      >
        {f && (
          <>
            <span className="label mr-1">Needs</span>
            {NEEDS.filter((k) => keys.includes(k)).map((k) => (
              <Chip key={k} pressed={needs.includes(k)} onClick={() => toggle(k)}>
                {FEATURE_LABEL[k]}
              </Chip>
            ))}
          </>
        )}
      </PageHeader>
      <div className="frame py-8">
        {state.status === "error" && <ErrorState title="Could not load the run data." body="The results files did not load. Check your connection and try again." onRetry={() => location.reload()} />}
        {state.status === "loading" && <Skeleton className="h-96 w-full" />}
        {state.status === "ready" && !f && <EmptyState title="No feature matrix in this export yet." body="It is written from each provider's features table in pricing.toml when a run is exported." />}
        {run && f && (
          <Figure
            n={1}
            title={`Feature matrix${needs.length ? `, offering ${needs.map((k) => FEATURE_LABEL[k].toLowerCase()).join(" and ")}` : ""}`}
            caption={
              <>
                A tick means the provider documents the feature; a dash means it does not offer it. Hover or focus a cell for the source.
                {needs.length > 0 && rows.length === 0 && " No provider offers all of those."}
              </>
            }
            source={`source: features (pricing.toml)${f.checked_on ? `, checked ${f.checked_on}` : ""}, run=${run.run_id}`}
            bodyClassName="p-0 sm:p-0"
          >
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1180px] border-separate border-spacing-0 text-[13px]">
                <caption className="sr-only">Feature support per provider, with documentation links and the date each was checked</caption>
                <thead>
                  <tr>
                    <th scope="col" className="sticky left-0 z-10 border-b border-line bg-surface px-4 py-2.5 text-left font-normal sm:px-5">
                      <span className="label">Provider</span>
                    </th>
                    {keys.map((k) => (
                      <th key={k} scope="col" className="border-b border-line bg-surface px-3 py-2.5 text-left align-bottom font-normal">
                        <span className="label">{FEATURE_LABEL[k]}</span>
                      </th>
                    ))}
                    <th scope="col" className="border-b border-line bg-surface px-3 py-2.5 pr-4 text-right align-bottom font-normal sm:pr-5">
                      <span className="label">Price</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((p) => (
                    <tr key={p.id} className="group">
                      <th scope="row" className="sticky left-0 z-[1] border-b border-line bg-surface px-4 py-3 text-left font-normal group-last:border-b-0 group-hover:bg-sunken sm:px-5">
                        <Link to={`/providers/${p.id}`} className="inline-flex items-center gap-2 hover:underline">
                          <SeriesDot color={providerColor(run, p.id)} />
                          <span className="font-medium whitespace-nowrap text-ink">{label(run, p)}</span>
                        </Link>
                      </th>
                      {keys.map((k) => (
                        <td key={k} className="border-b border-line px-3 py-3 align-top group-last:border-b-0 group-hover:bg-sunken/60">
                          <Cell k={k} c={p.cells[k]} provider={label(run, p)} />
                        </td>
                      ))}
                      <td className="border-b border-line px-3 py-3 pr-4 text-right align-top group-last:border-b-0 group-hover:bg-sunken/60 sm:pr-5">
                        <Price run={run} p={p} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Figure>
        )}
      </div>
    </>
  );
}

function label(run: Run | undefined, p: FeatureProvider) {
  return run?.providers.find((x) => x.id === p.id)?.label ?? p.label ?? p.id;
}

function show(k: FeatureKey, v: unknown): { text: string; sub?: string } {
  if (k === "languages" && Array.isArray(v)) {
    const ours = LANGUAGES.filter((l) => v.includes(l));
    return { text: `${int(v.length)} ${v.length === 1 ? "language" : "languages"}`, sub: ours.length ? `benchmarked: ${ours.join(" ")}` : undefined };
  }
  if (k === "max_file_minutes" && typeof v === "number") return { text: v >= 120 ? `${+(v / 60).toFixed(1)} h` : `${int(v)} min` };
  if (k === "retention" && typeof v === "number") return { text: v === 0 ? "Not stored" : `${int(v)} days` };
  if (v == null || v === "") return { text: "Not stated" };
  if (Array.isArray(v)) return { text: v.join(", ") };
  return { text: String(v) };
}

function Cell({ k, c, provider }: { k: FeatureKey; c: FeatureCell | undefined; provider: string }) {
  if (!c) return <span className="text-[12.5px] text-ink-3">n/a</span>;
  const yesNo = c.support === "yes" || c.support === "no";
  const v = show(k, c.value);
  const body = yesNo ? (
    c.support === "yes" ? (
      <span className="inline-flex items-center gap-1 text-ink">
        <Check size={14} strokeWidth={2} className="text-good" aria-hidden />
        <span className="sr-only">Yes</span>
      </span>
    ) : (
      <span className="inline-flex items-center gap-1 text-ink-3">
        <Minus size={14} strokeWidth={2} aria-hidden />
        <span className="sr-only">No</span>
      </span>
    )
  ) : (
    <span className="grid">
      <span className={cn("text-[12.5px]", c.support === "partial" ? "text-ink-2" : "text-ink")}>{v.text}</span>
      {v.sub && <span className="font-mono text-[10.5px] text-ink-3">{v.sub}</span>}
    </span>
  );
  const tip = `${provider}, ${FEATURE_LABEL[k].toLowerCase()}${c.checked_on ? `: checked ${date(c.checked_on)}` : ""}${c.url ? `. Source: ${new URL(c.url).hostname}` : ""}`;
  return (
    <span className="inline-flex items-start gap-1.5">
      {c.url ? (
        <Tooltip content={tip}>
          <a href={c.url} target="_blank" rel="noreferrer" className="inline-flex items-start gap-1 rounded-xs hover:underline" aria-label={tip}>
            {body}
            <ArrowUpRight size={11} className="mt-0.5 shrink-0 text-ink-3" aria-hidden />
          </a>
        </Tooltip>
      ) : (
        body
      )}
      {c.checked_on && <span className="sr-only">checked {date(c.checked_on)}</span>}
    </span>
  );
}

function Price({ run, p }: { run: Run; p: FeatureProvider }) {
  const prov = run.providers.find((x) => x.id === p.id);
  const perHour = prov?.usd_per_hour ?? (typeof p.price?.value === "number" ? p.price.value : null);
  const url = safeUrl(p.price?.url ?? null);
  const checked = p.price?.checked_on ?? prov?.pricing_checked_on ?? null;
  if (perHour == null) return <span className="text-[12.5px] text-ink-3">n/a</span>;
  const inner = (
    <span className="inline-flex flex-col items-end leading-tight whitespace-nowrap">
      <span className="num text-[13px] text-ink">{prov && !prov.paid ? "Free" : `${usdPerHour(perHour)} / h`}</span>
      <span className="num text-[10.5px] text-ink-3">{prov && !prov.paid ? "runs locally" : `${usdPerHour(pricePer1000Min(perHour))} / 1k min`}</span>
      {checked && <span className="num text-[10.5px] text-ink-3">{date(checked)}</span>}
    </span>
  );
  return url ? (
    <a href={url} target="_blank" rel="noreferrer" className="hover:underline" aria-label={`${label(run, p)} pricing page`}>
      {inner}
    </a>
  ) : (
    inner
  );
}

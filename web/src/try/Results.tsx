import { AlertTriangle } from "lucide-react";
import { Badge, SeriesDot } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { cn } from "@/lib/cn";
import { ms } from "@/lib/format";
import { TRY_MODELS, type TryResult } from "./api";
import { diffMode, tokenize } from "./diff";

export const modelColor = (provider: string) => {
  const i = TRY_MODELS.findIndex((m) => m.provider === provider);
  return i >= 0 ? `var(--series-${i + 1})` : "var(--ink-3)";
};

export function usd(x: number | null | undefined) {
  if (x == null || Number.isNaN(x)) return "n/a";
  if (x === 0) return "$0";
  if (x < 0.0001) return "<$0.0001";
  return x < 0.01 ? `$${x.toFixed(4)}` : `$${x.toFixed(3)}`;
}

const LANG_NAMES: Record<string, string> = { en: "English", fr: "French", ja: "Japanese", ko: "Korean", es: "Spanish", hi: "Hindi" };
export const langName = (c: string | null | undefined) => (c ? (LANG_NAMES[c] ?? c) : "n/a");

/** Words for spaced languages, characters for ja/ko (the unit the comparison view uses). */
export function countUnits(r: TryResult, language: string | null) {
  const mode = diffMode(language ?? r.detected_language);
  return { n: tokenize(r.text, mode).length, unit: mode === "char" ? "chars" : "words" };
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="min-w-0">
      <dt className="label">{k}</dt>
      <dd className="num mt-0.5 truncate text-[13px] text-ink">{v}</dd>
    </div>
  );
}

/** One card per model, filled in as its line arrives. */
export function ResultCard({ provider, label, model_id, result, language }: { provider: string; label: string; model_id: string; result: TryResult | null; language: string | null }) {
  const lang = language ?? result?.detected_language ?? undefined;
  const units = result && !result.error ? countUnits(result, language) : null;
  return (
    <article aria-busy={!result} className="flex min-w-0 flex-col rounded-md border border-line bg-surface shadow-card">
      <header className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <SeriesDot color={modelColor(provider)} />
            <h3 className="truncate text-[14px] font-semibold text-ink">{label}</h3>
          </div>
          <p className="mt-0.5 truncate font-mono text-[11px] text-ink-3">{model_id}</p>
        </div>
        {!result ? (
          <Badge tone="neutral">Waiting</Badge>
        ) : result.error ? (
          <Badge tone="signal">Error</Badge>
        ) : (
          <Badge tone="outline">Done</Badge>
        )}
      </header>
      <div className="flex-1 px-4 py-3">
        {!result ? (
          <div className="grid gap-2 py-1" aria-label={`Waiting for ${label}`}>
            <Skeleton className="h-3.5 w-full" />
            <Skeleton className="h-3.5 w-11/12" />
            <Skeleton className="h-3.5 w-3/5" />
          </div>
        ) : result.error ? (
          <p role="alert" className="flex items-start gap-2 text-[13.5px] text-signal-ink">
            <AlertTriangle size={14} strokeWidth={1.75} className="mt-0.5 shrink-0" aria-hidden />
            <span>{result.error} The other models are unaffected.</span>
          </p>
        ) : result.text ? (
          <p lang={lang} className="max-h-48 overflow-y-auto text-[14.5px] leading-relaxed whitespace-pre-wrap text-ink">
            {result.text}
          </p>
        ) : (
          <p className="text-[13.5px] text-ink-3">No speech heard.</p>
        )}
      </div>
      <dl className={cn("grid grid-cols-4 gap-3 border-t border-line px-4 py-2.5", !result && "opacity-50")}>
        <Stat k="Latency" v={result?.latency_ms != null ? ms(result.latency_ms) : "..."} />
        <Stat k="Cost" v={result ? usd(result.cost_usd) : "..."} />
        <Stat k={units?.unit === "chars" ? "Chars" : "Words"} v={units ? String(units.n) : result ? "0" : "..."} />
        <Stat k="Heard as" v={result && !result.error ? (result.detected_language ?? language ?? "n/a").toUpperCase() : "..."} />
      </dl>
    </article>
  );
}

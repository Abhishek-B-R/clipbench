import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { ArrowRight, Check, Link2 } from "lucide-react";
import { useFindings, useRunFor, useSource } from "@/lib/data";
import { LANGUAGE_NAME, languageProgress, runLanguageStatus } from "@/lib/analysis";
import { LANGUAGES } from "@/lib/config";
import { findingHref, findingSource } from "@/lib/metrics";
import { date } from "@/lib/format";
import { cn } from "@/lib/cn";
import type { FindingV2 } from "@/lib/types";
import { Rule } from "@/components/layout/Section";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/State";
import { NotPublished } from "@/components/ui/Gate";
import { toast } from "@/components/ui/Toast";

/**
 * Shareable findings for one language, written only from /data/findings/<lang>.json. Each sentence links to the
 * exact table and filters it came from. /findings/:lang/:slug opens the same page on that finding.
 */
export default function Findings() {
  const { lang = "", slug } = useParams();
  const known = (LANGUAGES as readonly string[]).includes(lang);
  const name = LANGUAGE_NAME[lang] ?? lang;
  const src = useSource();
  const runState = useRunFor(known ? lang : null);
  const fState = useFindings(lang);
  const run = runState.data?.run ?? null;
  const status = run ? runLanguageStatus(run) : src.data ? languageProgress(src.data.index).find((l) => l.lang === lang) : undefined;
  const file = fState.data;
  const published = Boolean(status?.published) && file?.published !== false;
  const findings = published ? (file?.findings ?? []) : [];
  const target = slug ? findings.find((f) => f.slug === slug) : undefined;

  useEffect(() => {
    document.title = target ? `${target.text} | clipbench` : `What we found in ${name} | clipbench`;
  }, [target, name]);
  useEffect(() => {
    if (target) document.getElementById(`finding-${target.slug}`)?.scrollIntoView({ block: "center" });
  }, [target]);

  const loading = runState.status === "loading" || fState.status === "loading" || src.status === "loading";

  return (
    <>
      <header className="frame frame-lines pt-12 pb-10 sm:pt-20 sm:pb-14">
        <div className="label mb-5 flex flex-wrap items-center gap-2">
          <span className="text-signal-ink">Findings</span> / {name}
          {file?.created_at && <span>/ run of {date(file.created_at)}</span>}
        </div>
        <h1 className="display max-w-[18ch] text-[44px] text-ink sm:text-[68px]">What we found in {name}.</h1>
        <p className="mt-5 max-w-[60ch] text-[16.5px] leading-relaxed text-ink-2">
          Each sentence is generated from the run data and kept only if the paired test backs it. Follow any of them to the table it came from.
        </p>
        <nav aria-label="Language" className="mt-7 flex flex-wrap gap-1.5">
          {LANGUAGES.map((l) => (
            <Link
              key={l}
              to={`/findings/${l}`}
              aria-current={l === lang ? "page" : undefined}
              className={cn(
                "inline-flex h-8 items-center rounded-full border px-3 text-[13px] transition-colors",
                l === lang ? "border-ink bg-surface text-ink shadow-card" : "border-line-strong text-ink-2 hover:border-ink-3 hover:text-ink",
              )}
            >
              {LANGUAGE_NAME[l]}
            </Link>
          ))}
        </nav>
      </header>
      <Rule />
      <div className="frame frame-lines grid gap-6 pt-10 pb-24">
        {!known && <EmptyState title={`clipbench does not cover "${lang}".`} action={<Link to="/findings/en" className="text-[13.5px] text-ink underline underline-offset-4">English findings</Link>} />}
        {known && (runState.status === "error" || src.status === "error") && <ErrorState title="Could not load the run data." onRetry={() => location.reload()} />}
        {known && loading && (
          <div className="grid gap-4">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-28 w-full" />
            ))}
          </div>
        )}
        {known && !loading && status && !published && <NotPublished status={status} scored={run?.clips.length} models={run?.providers.length} what="findings" />}
        {known && !loading && published && findings.length === 0 && (
          <EmptyState title={`No findings written for ${name} yet.`} body="Findings appear once the exporter writes them for this run. The leaderboard already has the numbers." action={<Link to={`/leaderboard?lang=${lang}`} className="text-[13.5px] text-ink underline underline-offset-4">Open the leaderboard</Link>} />
        )}
        {known && !loading && published && findings.length > 0 && (
          <ol className="grid gap-4">
            {findings.map((f, i) => (
              <FindingRow key={f.id} f={f} i={i} lang={lang} active={f.slug === slug} />
            ))}
          </ol>
        )}
        {slug && !loading && published && !target && findings.length > 0 && (
          <p role="status" className="text-[13.5px] text-ink-3">
            That finding is not in the latest run. The current findings are listed above.
          </p>
        )}
      </div>
    </>
  );
}

function FindingRow({ f, i, lang, active }: { f: FindingV2; i: number; lang: string; active: boolean }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(`${location.origin}/findings/${lang}/${f.slug}`);
      setCopied(true);
      toast("Link copied");
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Could not copy the link");
    }
  };
  return (
    <li
      id={`finding-${f.slug}`}
      className={cn(
        "relative grid scroll-mt-28 gap-4 rounded-md border bg-surface p-5 shadow-card transition-colors sm:grid-cols-[48px_minmax(0,1fr)] sm:p-6",
        active ? "border-ink ring-4 ring-signal/15" : "border-line",
      )}
    >
      <span className={cn("num text-[13px]", active ? "text-signal-ink" : "text-ink-3")}>{String(i + 1).padStart(2, "0")}</span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <p className="max-w-[68ch] text-[18px] leading-snug text-ink sm:text-[20px]" style={{ fontStretch: "96%" }}>
            {f.text}
          </p>
          {f.status === "verified" ? <Badge tone="good">Checked</Badge> : <Badge tone="warn">Draft</Badge>}
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
          <Link to={findingHref(f, lang)} className="group inline-flex items-center gap-1.5 text-[13.5px] font-medium text-ink">
            See the table it came from <ArrowRight size={14} className="transition-transform group-hover:translate-x-0.5" />
          </Link>
          <button type="button" onClick={copy} className="inline-flex items-center gap-1.5 text-[13px] text-ink-3 hover:text-ink">
            {copied ? <Check size={13} /> : <Link2 size={13} />} Copy link
          </button>
        </div>
        <p className="mt-3 font-mono text-[11px] break-words text-ink-3">{findingSource(f)}</p>
      </div>
    </li>
  );
}

import { useMemo, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { AnimatePresence, m, useReducedMotion } from "motion/react";
import { ArrowRight, Check, Clock, Cpu, Minus, Radio, Scale, Timer, Users, Wallet, X } from "lucide-react";
import type { Run } from "@/lib/types";
import { useRunFor, useSource } from "@/lib/data";
import { LANGUAGE_NAME, isPublished, languageProgress, providerById, providerColor, providerLabel } from "@/lib/analysis";
import { parsePickParams, pick, type PickInput, type PickRow } from "@/lib/picker";
import { sliceLabel } from "@/lib/metrics";
import { LANGUAGES } from "@/lib/config";
import { ms, pct, pValue, signedPts, usdPerHour } from "@/lib/format";
import { maxOf } from "@/lib/scale";
import { cn } from "@/lib/cn";
import { PageHeader } from "@/components/layout/PageHeader";
import { Figure, Corners } from "@/components/ui/Card";
import { Segmented } from "@/components/ui/Segmented";
import { Chip } from "@/components/ui/Chip";
import { Badge, SeriesDot } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { Button, buttonClass } from "@/components/ui/Button";
import { EmptyState, ErrorState } from "@/components/ui/State";
import { v3 } from "@/lib/v3";
import { TierNote } from "@/components/ui/TierControl";
import { Gated, NotRanked } from "@/components/ui/Gate";

const ease = [0.16, 1, 0.3, 1] as const;

/**
 * "Pick a provider" for builders. Inputs: language, music-heavy or not, budget per audio hour, word timing,
 * latency, and whether audio may leave your machine. Output: a ranked short list with the numbers behind each
 * model and the paired-test caveat, from the published run's aggregates. Unpublished languages show progress.
 */
export default function Picker() {
  const src = useSource();
  const [params, setParams] = useSearchParams();
  const progress = src.data ? languageProgress(src.data.index) : undefined;
  const firstPublished = progress?.find((l) => l.published)?.lang;
  const latestLang = src.data?.index.runs.find((r) => r.run_id === src.data!.index.latest)?.language;
  const lang = params.get("lang") || firstPublished || latestLang || "en";
  const state = useRunFor(src.data ? lang : null);
  const run = state.data?.run ?? null;
  const { audio, budget, latency, mode, diarization } = parsePickParams(params);
  const timing = params.get("timing") === "1";
  const local = params.get("local") === "1";
  const set = (patch: Record<string, string | null>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) (v == null ? p.delete(k) : p.set(k, v));
    setParams(p, { replace: true });
  };
  const input: PickInput = { audio, budget, timing, latency: mode === "streaming" ? "any" : latency, local, mode, diarization };
  const published = Boolean(run && isPublished(run));
  const result = useMemo(() => (run && published ? pick(run, input) : null), [run, published, audio, budget, timing, latency, local, mode, diarization]); // eslint-disable-line react-hooks/exhaustive-deps
  const loading = src.status === "loading" || state.status === "loading";

  return (
    <>
      <PageHeader
        eyebrow="Pick a provider"
        title="Which speech-to-text for my app?"
        description="Describe your clips and your limits. You get a short list ranked by error on audio like yours, batch or streaming, with the numbers behind each model, and we say so when the top two are a statistical tie."
      />
      <div className="frame py-8">
        {(src.status === "error" || state.status === "error") && <ErrorState title="Could not load the run data." body="The results files did not load. Check your connection and try again." onRetry={() => location.reload()} />}
        {loading && (
          <div className="grid gap-8 lg:grid-cols-[360px_minmax(0,1fr)]">
            <Skeleton className="h-[520px]" />
            <Skeleton className="h-[520px]" />
          </div>
        )}
        {!loading && state.status === "ready" && (
          <div className="grid items-start gap-8 lg:grid-cols-[360px_minmax(0,1fr)] xl:grid-cols-[380px_minmax(0,1fr)] xl:gap-10">
            <form onSubmit={(e) => e.preventDefault()} aria-label="Your app" className="grid overflow-hidden rounded-md border border-line bg-surface shadow-card lg:sticky lg:top-20">
              <Step n={1} title="Language" hint={published ? "Published" : "Not published yet"}>
                <div className="flex flex-wrap gap-1.5">
                  {LANGUAGES.map((l) => {
                    const st = progress?.find((x) => x.lang === l);
                    const ok = Boolean(st?.published);
                    return (
                      <Chip key={l} pressed={l === lang} disabled={!ok && l !== lang} title={ok ? undefined : "Not published yet"} onClick={() => set({ lang: l })}>
                        {LANGUAGE_NAME[l]}
                      </Chip>
                    );
                  })}
                </div>
                <p className="mt-2 text-[12px] text-ink-3">A language opens here once its results are published.</p>
              </Step>
              <Step n={2} title="Is your audio music-heavy?">
                <Segmented
                  label="Music-heavy audio"
                  value={audio}
                  onChange={(v) => set({ audio: v === "any" ? null : v })}
                  options={[
                    { value: "any", label: "A mix" },
                    { value: "music", label: "Mostly music" },
                    { value: "no_music", label: "Mostly speech" },
                  ]}
                />
              </Step>
              <Step n={3} title="Batch or streaming?" hint={mode === "streaming" ? "Live captions" : "Files"}>
                <Segmented
                  label="Batch or streaming"
                  value={mode}
                  onChange={(v) => set({ mode: v === "batch" ? null : v, lat: null })}
                  options={[
                    { value: "batch", label: "Batch files" },
                    { value: "streaming", label: "Streaming" },
                  ]}
                />
                {mode === "batch" ? (
                  <div className="mt-3">
                    <Segmented
                      label="Latency need"
                      value={latency}
                      onChange={(v) => set({ lat: v === "any" ? null : v })}
                      options={[
                        { value: "any", label: "Any wait" },
                        { value: "3000", label: "Under 3 s" },
                        { value: "1500", label: "Under 1.5 s" },
                      ]}
                    />
                  </div>
                ) : (
                  <p className="mt-2 text-[12px] text-ink-3">Ranked on the streaming board: final error rate, with time to final text.</p>
                )}
              </Step>
              <Step n={4} title="Budget per hour of audio" hint={budget == null ? "No limit" : `Up to ${usdPerHour(budget)}`} >
                {run ? <Budget run={run} value={budget} onChange={(v) => set({ budget: v == null ? null : v.toFixed(2) })} /> : null}
              </Step>
              <Step n={5} title="Do you need word timings?" hint="Word-by-word captions">
                <Segmented
                  label="Need word timings"
                  value={timing ? "yes" : "no"}
                  onChange={(v) => set({ timing: v === "yes" ? "1" : null })}
                  options={[
                    { value: "no", label: "No" },
                    { value: "yes", label: "Yes" },
                  ]}
                />
              </Step>
              <Step n={6} title="Do you need speaker labels?" hint="Who said what">
                <Segmented
                  label="Need speaker labels"
                  value={diarization ? "yes" : "no"}
                  onChange={(v) => set({ diar: v === "yes" ? "1" : null })}
                  options={[
                    { value: "no", label: "No" },
                    { value: "yes", label: "Yes" },
                  ]}
                />
              </Step>
              <Step n={7} title="Can audio leave your machine?" last>
                <Segmented
                  label="Can audio leave your machine"
                  value={local ? "no" : "yes"}
                  onChange={(v) => set({ local: v === "no" ? "1" : null })}
                  options={[
                    { value: "yes", label: "Yes" },
                    { value: "no", label: "No, keep it local" },
                  ]}
                />
              </Step>
              {(audio !== "any" || budget != null || timing || latency !== "any" || local || mode !== "batch" || diarization) && (
                <div className="border-t border-line px-5 py-3">
                  <button type="button" onClick={() => setParams(new URLSearchParams(params.get("lang") ? { lang } : {}), { replace: true })} className="text-[12.5px] text-ink-3 underline decoration-line-strong underline-offset-4 hover:text-ink">
                    Reset all
                  </button>
                </div>
              )}
            </form>

            <div className="grid min-w-0 gap-8">
              {!published ? (
                <Gated run={run} lang={lang} what="recommendation">
                  {null}
                </Gated>
              ) : (
                run &&
                result && (
                  <>
                    <TierNote tier={result.tier} />
                    {mode === "streaming" && !v3(run).streaming ? (
                      <EmptyState icon={<Radio size={16} />} title="No streaming results for this language yet." body="The streaming track runs separately. Switch to batch files for a pick from the batch board." />
                    ) : (
                    <>
                    {result.mode === "streaming" && !result.paired && (
                      <p className="rounded-md border border-line bg-sunken/60 px-4 py-2.5 text-[13px] text-ink-2">The streaming export has no paired test, so close results are not called ties here. Compare the intervals.</p>
                    )}
                    {result.fellBack && audio !== "any" && (
                      <p className="rounded-md border border-line bg-sunken/60 px-4 py-2.5 text-[13px] text-ink-2">This run has no music or speech-only slices yet, so the list uses all clips.</p>
                    )}
                    <Recommendation run={run} result={result} input={input} lang={lang} onRelax={set} />
                    <Ranked run={run} result={result} input={input} lang={lang} />
                    <NotRanked run={run} />
                    </>
                    )}
                  </>
                )
              )}
            </div>
          </div>
        )}
      </div>
    </>
  );
}

function Step({ n, title, hint, children, last }: { n: number; title: string; hint?: string; children: ReactNode; last?: boolean }) {
  return (
    <fieldset className={cn("px-5 py-5", !last && "border-b border-line")}>
      <legend className="sr-only">{title}</legend>
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <span className="flex items-baseline gap-2.5">
          <span className="num text-[11.5px] text-signal-ink">{String(n).padStart(2, "0")}</span>
          <span aria-hidden className="text-[14px] font-medium text-ink">
            {title}
          </span>
        </span>
        {hint && <span className="num text-[11.5px] text-ink-3">{hint}</span>}
      </div>
      {children}
    </fieldset>
  );
}

// ---------------------------------------------------------------- budget slider

function Budget({ run, value, onChange }: { run: Run; value: number | null; onChange: (v: number | null) => void }) {
  const prices = run.providers.map((p) => p.usd_per_hour);
  if (prices.every((x) => x === 0)) return <p className="text-[13px] text-ink-3">Every model in this run is free, so budget does not change the pick.</p>;
  const top = Math.max(0.05, Math.ceil((maxOf(prices) + 0.01) * 20) / 20);
  const end = Math.round(top * 1.35 * 100) / 100; // the stretch past the top price means "no limit"
  const v = value == null ? end : Math.min(value, top);
  const fill = `${(v / end) * 100}%`;
  return (
    <div>
      <div className="relative">
        <input
          type="range"
          className="range relative z-10"
          min={0}
          max={end}
          step={0.01}
          value={v}
          style={{ ["--fill" as string]: fill }}
          onChange={(e) => {
            const x = Number(e.target.value);
            onChange(x > top + 0.001 ? null : x);
          }}
          aria-label="Budget per hour of audio"
          aria-valuetext={value == null ? "No limit" : `${usdPerHour(value)} per hour`}
        />
        <div aria-hidden className="relative mx-[9px] mt-1 h-3">
          {run.providers.map((p) => (
            <span
              key={p.id}
              title={`${p.label}: ${usdPerHour(p.usd_per_hour)}`}
              className={cn("absolute top-0 size-2 -translate-x-1/2 rounded-full ring-2 ring-surface transition-opacity", value != null && p.usd_per_hour > value && "opacity-30")}
              style={{ left: `${(p.usd_per_hour / end) * 100}%`, background: providerColor(run, p.id) }}
            />
          ))}
        </div>
      </div>
      <div className="relative mt-1 h-4 font-mono text-[11px] text-ink-3">
        <span className="absolute left-0">$0</span>
        <span className="absolute -translate-x-1/2" style={{ left: `calc(9px + (100% - 18px) * ${top / end})` }}>
          {usdPerHour(top)}
        </span>
        <span className="absolute right-0">No limit</span>
      </div>
      <p className="mt-2 text-[12px] text-ink-3">Dots are each model's list price per hour of audio; a dot at $0 is free to run.</p>
    </div>
  );
}

// ---------------------------------------------------------------- recommendation

type Result = ReturnType<typeof pick>;

/** "all clips", "clips with music", "clips without music". */
function whereText(r: Result) {
  return r.kind === "all" ? "all clips" : `clips ${sliceLabel(r.kind, r.slice).toLowerCase()}`;
}

function evidence(lang: string, r: Result, input: PickInput) {
  const q = new URLSearchParams({ lang });
  if (r.mode === "streaming") {
    if (r.kind !== "all") {
      q.set("slice_kind", r.kind);
      q.set("slice", r.slice);
    }
    return `/streaming?${q}`;
  }
  if (input.diarization) {
    q.set("view", "speakers");
    return `/leaderboard?${q}`;
  }
  if (r.kind !== "all") {
    q.set("slice_kind", r.kind);
    q.set("slice", r.slice);
  }
  if (input.timing) q.set("sort", "timing");
  return `/leaderboard?${q}`;
}

function Recommendation({ run, result, input, lang, onRelax }: { run: Run; result: Result; input: PickInput; lang: string; onRelax: (p: Record<string, string | null>) => void }) {
  const reduce = useReducedMotion();
  const { best, tied, cheaperTie, betterExcluded } = result;
  const where = whereText(result);

  if (!best)
    return (
      <div className="relative rounded-lg border border-line bg-surface p-6 shadow-card sm:p-8">
        <Corners className="pointer-events-none absolute inset-0" />
        <div className="label">No model fits all of that</div>
        <h2 className="mt-3 text-[24px] font-semibold tracking-[-0.02em] text-ink">Loosen one limit to see a pick.</h2>
        <p className="mt-2 max-w-[56ch] text-[14.5px] text-ink-2">Every model in this run is ruled out by at least one of your limits. Each button below lifts one of them.</p>
        <div className="mt-5 flex flex-wrap gap-2">
          {input.budget != null && (
            <Button variant="secondary" size="sm" onClick={() => onRelax({ budget: null })}>
              <Wallet size={14} /> Remove the budget limit
            </Button>
          )}
          {input.timing && (
            <Button variant="secondary" size="sm" onClick={() => onRelax({ timing: null })}>
              <Timer size={14} /> Drop word timings
            </Button>
          )}
          {input.latency !== "any" && (
            <Button variant="secondary" size="sm" onClick={() => onRelax({ lat: null })}>
              <Clock size={14} /> Allow any latency
            </Button>
          )}
          {input.diarization && (
            <Button variant="secondary" size="sm" onClick={() => onRelax({ diar: null })}>
              <Users size={14} /> Drop speaker labels
            </Button>
          )}
          {input.mode === "streaming" && (
            <Button variant="secondary" size="sm" onClick={() => onRelax({ mode: null })}>
              <Radio size={14} /> Allow batch
            </Button>
          )}
          {input.local && (
            <Button variant="secondary" size="sm" onClick={() => onRelax({ local: null })}>
              <Cpu size={14} /> Allow uploads
            </Button>
          )}
        </div>
      </div>
    );

  const p = providerById(run, best.provider)!;
  const tie = cheaperTie ?? tied[0];
  const reasons: { ok: boolean; text: ReactNode }[] = [
    {
      ok: true,
      text: (
        <>
          Lowest {result.mode === "streaming" ? "final streaming error" : "error"} on {where}: <span className="num text-ink">{pct(best.agg.error_rate)}</span> (95% interval {pct(best.agg.ci_low)} to {pct(best.agg.ci_high)}, {best.agg.n_clips} clips).
        </>
      ),
    },
    ...(input.timing || best.timing != null
      ? [
          {
            ok: best.timing != null,
            text:
              best.timing != null ? (
                <>
                  <span className="num text-ink">{pct(best.timing)}</span> of correctly heard words start within 200 ms of when they are said.
                </>
              ) : (
                <>Returns no word timings.</>
              ),
          },
        ]
      : []),
    ...(input.diarization
      ? [
          {
            ok: best.der != null,
            text:
              best.der != null ? (
                <>
                  Labels speakers with a diarization error rate of <span className="num text-ink">{pct(best.der)}</span>.
                </>
              ) : (
                <>Offers speaker labels; not measured in this run.</>
              ),
          },
        ]
      : []),
    ...(input.audio === "music" && best.halluc != null
      ? [
          {
            ok: true,
            text: (
              <>
                Invents <span className="num text-ink">{best.halluc.toFixed(2)}</span> words per minute of music or silence.
              </>
            ),
          },
        ]
      : []),
    {
      ok: true,
      text: p.paid ? (
        <>
          Costs <span className="num text-ink">{usdPerHour(p.usd_per_hour)}</span> per hour of audio{input.budget != null ? `, inside your ${usdPerHour(input.budget)} budget` : ""}.
        </>
      ) : (
        <>Free: it runs on your machine.</>
      ),
    },
    {
      ok: true,
      text: (
        <>
          {result.mode === "streaming" ? "Final text" : "Median latency"} <span className="num text-ink">{ms(best.p50)}</span> {result.mode === "streaming" ? "after each segment ends (median)." : "per clip."} {p.uploads_audio ? "Sends audio to the provider." : "Audio never leaves your machine."}
        </>
      ),
    },
  ];

  return (
    <section aria-live="polite" aria-labelledby="pick-title" className="relative overflow-hidden rounded-lg border border-line bg-surface shadow-card">
      <Corners className="pointer-events-none absolute inset-0" />
      <div className="grid gap-6 p-6 sm:p-8 md:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] md:gap-10">
        <div>
          <div className="label flex items-center gap-2">
            <SeriesDot color={providerColor(run, best.provider)} />
            <span className="text-signal-ink">Top pick</span> / for {LANGUAGE_NAME[lang] ?? lang}, {where}
          </div>
          <AnimatePresence mode="wait" initial={false}>
            <m.div key={best.provider} initial={reduce ? false : { opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={reduce ? undefined : { opacity: 0, y: -8 }} transition={{ duration: 0.35, ease }}>
              <h2 id="pick-title" className={cn("display mt-4 text-ink", p.label.length > 14 ? "text-[34px] sm:text-[40px]" : "text-[44px] sm:text-[56px]")}>
                {p.label}
              </h2>
              <p className="mt-2 font-mono text-[12px] text-ink-3">{p.model_id}</p>
              <div className="mt-6 grid grid-cols-3 gap-4">
                <Stat label="Error rate" value={pct(best.agg.error_rate)} />
                <Stat label={input.timing ? "Words on time" : "Per hour"} value={input.timing ? (best.timing == null ? "n/a" : pct(best.timing)) : usdPerHour(p.usd_per_hour)} />
                <Stat
                  label={input.diarization ? "Speaker error" : result.mode === "streaming" ? "Final text" : input.audio === "music" ? "Invented / min" : "Median latency"}
                  value={input.diarization ? (best.der == null ? "n/a" : pct(best.der)) : result.mode === "streaming" ? ms(best.ttfs) : input.audio === "music" ? (best.halluc == null ? "n/a" : best.halluc.toFixed(2)) : ms(best.p50)}
                />
              </div>
              <div className="mt-7 flex flex-wrap gap-2">
                <Link to={`/providers/${best.provider}`} className={buttonClass("primary", "sm", "group")}>
                  Open {p.label} <ArrowRight size={14} className="transition-transform group-hover:translate-x-0.5" />
                </Link>
                <Link to={evidence(lang, result, input)} className={buttonClass("secondary", "sm")}>
                  See the evidence
                </Link>
              </div>
            </m.div>
          </AnimatePresence>
        </div>
        <div className="md:border-l md:border-line md:pl-10">
          <div className="label mb-3">Why</div>
          <ul className="grid gap-2.5">
            {reasons.map((r, i) => (
              <li key={i} className="flex gap-2.5 text-[13.5px] leading-snug text-ink-2">
                <span className={cn("mt-[1px] grid size-4 shrink-0 place-items-center rounded-full", r.ok ? "bg-good-soft text-good" : "bg-sunken text-ink-3")}>
                  {r.ok ? <Check size={10} strokeWidth={3} aria-hidden /> : <Minus size={10} strokeWidth={3} aria-hidden />}
                </span>
                <span>{r.text}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
      {(tie || betterExcluded.length > 0) && (
        <div className="grid gap-3 border-t border-line bg-sunken/50 px-6 py-4 sm:px-8">
          {tie && tie.vsPick && (
            <p className="flex gap-3 text-[13.5px] text-ink-2">
              <Scale size={16} strokeWidth={1.75} className="mt-0.5 shrink-0 text-ink-3" aria-hidden />
              <span>
                <span className="font-medium text-ink">Statistically tied with {providerLabel(run, tie.provider)}</span>
                {tied.length > 1 ? ` and ${tied.length - 1} more` : ""}: {signedPts(tie.vsPick.diff)} on these clips ({pValue(tie.vsPick.p)}, interval crosses zero).
                {cheaperTie ? ` It costs ${usdPerHour(cheaperTie.price)} per hour, so if price matters it is just as good a choice.` : " Either is a sound choice."}
              </span>
            </p>
          )}
          {betterExcluded.slice(0, 1).map((b) => (
            <p key={b.provider} className="flex gap-3 text-[13.5px] text-ink-2">
              <X size={16} strokeWidth={1.75} className="mt-0.5 shrink-0 text-ink-3" aria-hidden />
              <span>
                {providerLabel(run, b.provider)} makes fewer errors on these clips ({pct(b.agg.error_rate)}) but is ruled out: {b.excluded.join(", ").toLowerCase()}.
              </span>
            </p>
          ))}
        </div>
      )}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className="mt-1.5 text-[26px] leading-none font-medium tracking-[-0.03em] text-ink" style={{ fontStretch: "95%" }}>
        {value}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- short list

function Ranked({ run, result, input, lang }: { run: Run; result: Result; input: PickInput; lang: string }) {
  const { best } = result;
  const rows = [...result.rows.filter((r) => !r.excluded.length), ...result.rows.filter((r) => r.excluded.length)];
  const max = maxOf(rows.map((r) => r.agg.ci_high), 0.1) * 1.06 || 0.1;
  const where = whereText(result);
  return (
    <Figure
      n={1}
      title={`Short list${result.mode === "streaming" ? ", streaming" : ""}, ${where}`}
      caption="Error rate with its 95% interval on the matching audio, then the numbers your inputs care about. Ruled-out models stay listed, greyed, with the reason."
      source={
        <span className="flex flex-wrap items-center justify-between gap-2">
          <span>
            source: {result.mode === "streaming" ? "streaming" : "aggregates + paired"}{input.diarization ? " + diarization" : ""}, run={run.run_id}, tier={result.tier}, metric={result.metric}, slice_kind={result.kind}, slice={result.slice}
          </span>
          <Link to={evidence(lang, result, input)} className="inline-flex items-center gap-1 text-ink-2 hover:text-ink">
            Evidence <ArrowRight size={12} />
          </Link>
        </span>
      }
      bodyClassName="p-0 sm:p-0"
    >
      <ol>
        {rows.map((r, i) => (
          <m.li layout transition={{ type: "spring", stiffness: 400, damping: 38 }} key={r.provider} className={cn("grid gap-x-4 gap-y-2 border-b border-line px-4 py-3.5 last:border-b-0 sm:px-5 md:grid-cols-[28px_120px_minmax(140px,1fr)_minmax(0,230px)_130px] md:items-center", r.excluded.length && "bg-sunken/40")}>
            <span className={cn("num hidden text-[12.5px] md:block", r === best ? "text-signal-ink" : "text-ink-3")}>{r.excluded.length ? "" : String(i + 1).padStart(2, "0")}</span>
            <Link to={`/providers/${r.provider}`} className={cn("flex min-w-0 items-center gap-2 hover:underline", r.excluded.length ? "text-ink-3" : "text-ink")}>
              <SeriesDot color={providerColor(run, r.provider)} className={r.excluded.length ? "opacity-40" : ""} />
              <span className="truncate text-[13.5px] font-medium">{providerLabel(run, r.provider)}</span>
            </Link>
            <span className="flex items-center gap-3">
              <ScoreBar r={r} max={max} color={providerColor(run, r.provider)} muted={r.excluded.length > 0} />
              <span className={cn("num w-12 shrink-0 text-right text-[13px]", r.excluded.length ? "text-ink-3" : "text-ink")}>{pct(r.agg.error_rate)}</span>
            </span>
            <span className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-ink-3">
              <Num label="per hour" v={usdPerHour(r.price)} />
              {(input.timing || r.timing != null) && <Num label="on time" v={r.timing == null ? "n/a" : pct(r.timing)} />}
              {input.audio === "music" && <Num label="invented / min" v={r.halluc == null ? "n/a" : r.halluc.toFixed(2)} />}
              {input.latency !== "any" && <Num label="p50" v={ms(r.p50)} />}
              {result.mode === "streaming" && <Num label="final text" v={ms(r.ttfs)} />}
              {input.diarization && <Num label="speaker error" v={r.der == null ? "n/a" : pct(r.der)} />}
            </span>
            <span className="flex justify-start md:justify-end">
              <Status r={r} best={best} />
            </span>
          </m.li>
        ))}
      </ol>
    </Figure>
  );
}

function Num({ label, v }: { label: string; v: string }) {
  return (
    <span className="whitespace-nowrap">
      <span className="num text-ink-2">{v}</span> {label}
    </span>
  );
}

function ScoreBar({ r, max, color, muted }: { r: PickRow; max: number; color: string; muted: boolean }) {
  const w = (v: number) => `${(v / max) * 100}%`;
  return (
    <span className={cn("relative h-5 flex-1", muted && "opacity-35")} role="img" aria-label={`${pct(r.agg.error_rate)}, interval ${pct(r.agg.ci_low)} to ${pct(r.agg.ci_high)}`}>
      <span className="absolute inset-y-0 left-0 w-px bg-line-strong" />
      <m.span className="absolute top-[3px] bottom-[3px] left-0 rounded-r-[4px]" style={{ background: color }} initial={false} animate={{ width: w(r.agg.error_rate) }} transition={{ duration: 0.6, ease }} />
      <m.span className="absolute top-1/2 h-px -translate-y-1/2 bg-ink" initial={false} animate={{ left: w(r.agg.ci_low), width: `calc(${w(r.agg.ci_high)} - ${w(r.agg.ci_low)})` }} transition={{ duration: 0.6, ease }}>
        <span className="absolute -top-[3px] left-0 h-[7px] w-px bg-ink" />
        <span className="absolute -top-[3px] right-0 h-[7px] w-px bg-ink" />
      </m.span>
    </span>
  );
}

function Status({ r, best }: { r: PickRow; best?: PickRow }) {
  if (r === best) return <Badge tone="good">Top pick</Badge>;
  if (r.excluded.length)
    return (
      <span className="flex flex-wrap gap-1">
        {r.excluded.map((x) => (
          <Badge key={x} tone="outline">
            {x}
          </Badge>
        ))}
      </span>
    );
  if (r.vsPick?.tied) return <Badge tone="ins">Tied with top pick</Badge>;
  return <span className="num text-[12px] text-ink-3">{r.vsPick ? signedPts(r.vsPick.diff) : ""}</span>;
}

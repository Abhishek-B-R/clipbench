import { useMemo, type ReactNode } from "react";
import { maxOf } from "@/lib/scale";
import { Link, useSearchParams } from "react-router";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ArrowRight, Check, Clock, Cpu, Minus, Scale, Wallet, X } from "lucide-react";
import type { Run, Tier } from "@/lib/types";
import { useRun, useSource } from "@/lib/data";
import { LANGUAGE_NAME, TAG_LABEL, TAG_PHRASE, defaultTier, providerById, providerColor, providerLabel, recommend, tagsInRun, type LatencyNeed, type PickerInput, type PickerRow } from "@/lib/analysis";
import { ms, pct, pValue, signedPts, usdPerHour } from "@/lib/format";
import { cn } from "@/lib/cn";
import { PageHeader } from "@/components/layout/PageHeader";
import { Figure, Corners } from "@/components/ui/Card";
import { Segmented } from "@/components/ui/Segmented";
import { Chip } from "@/components/ui/Chip";
import { Badge, SeriesDot } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { Button, buttonClass } from "@/components/ui/Button";
import { ErrorState } from "@/components/ui/State";
import { TierNote } from "@/components/ui/TierControl";

const ease = [0.16, 1, 0.3, 1] as const;
const ALL_LANGS = ["ja", "es", "ko", "en"];

/**
 * "Which model for my clips?" Inputs on the left, a live recommendation on the right. Constraints exclude models,
 * they never reweight them; the pick is then paired-tested against every eligible model on exactly the clips you
 * described, so a statistical tie is said out loud. All inputs live in the URL.
 */
export default function Picker() {
  const state = useRun();
  const src = useSource();
  const run = state.data?.run;
  const [params, setParams] = useSearchParams();
  const tags = (params.get("tags") ?? "").split(",").filter(Boolean);
  const budgetRaw = params.get("budget");
  const budget = budgetRaw == null || budgetRaw === "" ? null : Number(budgetRaw);
  const latency = (params.get("lat") as LatencyNeed) || "any";
  const local = params.get("local") === "1";
  const set = (patch: Record<string, string | null>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) v == null ? p.delete(k) : p.set(k, v);
    setParams(p, { replace: true });
  };
  const input: PickerInput = { tags, budget, latency, local };
  const result = useMemo(() => (run ? recommend(run, input) : null), [run, tags.join(), budget, latency, local]); // eslint-disable-line react-hooks/exhaustive-deps
  const languages = new Set(src.data?.index.runs.map((r) => r.language) ?? []);

  return (
    <>
      <PageHeader
        eyebrow="Model picker"
        title="Which model for my clips?"
        description="Describe your audio and your limits. The pick is the lowest error on clips like yours, and we tell you when the runner-up is a statistical tie."
      />
      <div className="frame py-8">
        {state.status === "error" && <ErrorState title="Could not load the run data." body="The results files did not load. Check your connection and try again." onRetry={() => location.reload()} />}
        {!run && state.status !== "error" && (
          <div className="grid gap-8 lg:grid-cols-[360px_minmax(0,1fr)]">
            <Skeleton className="h-[520px]" />
            <Skeleton className="h-[520px]" />
          </div>
        )}
        {run && result && (
          <div className="grid items-start gap-8 lg:grid-cols-[360px_minmax(0,1fr)] xl:grid-cols-[380px_minmax(0,1fr)] xl:gap-10">
            <form onSubmit={(e) => e.preventDefault()} aria-label="Your clips" className="grid gap-0 overflow-hidden rounded-md border border-line bg-surface shadow-card lg:sticky lg:top-20">
              <Step n={1} title="Language">
                <div className="flex flex-wrap gap-1.5">
                  {ALL_LANGS.map((l) => {
                    const has = languages.has(l);
                    return (
                      <Chip key={l} pressed={l === run.language} disabled={!has} aria-disabled={!has} title={has ? undefined : "No published run for this language yet"}>
                        {LANGUAGE_NAME[l]}
                      </Chip>
                    );
                  })}
                </div>
                <p className="mt-2 text-[12px] text-ink-3">Other languages open when their first run is published.</p>
              </Step>
              <Step n={2} title="Clip types that matter" hint={tags.length ? `${tags.length} picked` : "Any clip"}>
                {tagsInRun(run).length ? (
                  <div className="flex flex-wrap gap-1.5">
                    {tagsInRun(run).map((t) => (
                      <Chip key={t} pressed={tags.includes(t)} onClick={() => set({ tags: (tags.includes(t) ? tags.filter((x) => x !== t) : [...tags, t]).join(",") || null })}>
                        {TAG_LABEL[t]}
                      </Chip>
                    ))}
                  </div>
                ) : (
                  <p className="text-[13px] text-ink-3">No clip types are tagged in this run yet, so the pick uses every clip.</p>
                )}
              </Step>
              <Step n={3} title="Budget per hour of audio" hint={budget == null ? "No limit" : `Up to ${usdPerHour(budget)}`}>
                <Budget run={run} value={budget} onChange={(v) => set({ budget: v == null ? null : v.toFixed(2) })} />
              </Step>
              <Step n={4} title="Latency" hint="Median per clip">
                <Segmented
                  label="Latency need"
                  value={latency}
                  onChange={(v) => set({ lat: v === "any" ? null : v })}
                  options={[
                    { value: "any", label: "Batch is fine" },
                    { value: "3000", label: "Under 3 s" },
                    { value: "1500", label: "Under 1.5 s" },
                  ]}
                />
              </Step>
              <Step n={5} title="Can audio leave your machine?" last>
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
              {(tags.length > 0 || budget != null || latency !== "any" || local) && (
                <div className="border-t border-line px-5 py-3">
                  <button type="button" onClick={() => setParams(new URLSearchParams(), { replace: true })} className="text-[12.5px] text-ink-3 underline decoration-line-strong underline-offset-4 hover:text-ink">
                    Reset all
                  </button>
                </div>
              )}
            </form>

            <div className="grid min-w-0 gap-8">
              <TierNote tier={defaultTier(run)} />
              <Recommendation run={run} result={result} input={input} onRelax={set} />
              <Ranked run={run} result={result} />
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
      <p className="mt-2 text-[12px] text-ink-3">Dots are each model's list price. Free models run on your own machine.</p>
    </div>
  );
}

// ---------------------------------------------------------------- recommendation

type Result = ReturnType<typeof recommend>;

function Recommendation({ run, result, input, onRelax }: { run: Run; result: Result; input: PickerInput; onRelax: (p: Record<string, string | null>) => void }) {
  const reduce = useReducedMotion();
  const { pick, tied, cheaperTie, betterExcluded, nClips } = result;
  const where = input.tags.length ? `${nClips} clips with ${list(input.tags.map((t) => TAG_PHRASE[t] ?? t), "or")}` : `all ${nClips} clips`;

  if (!pick)
    return (
      <div className="relative rounded-lg border border-line bg-surface p-6 shadow-card sm:p-8">
        <Corners />
        <div className="label">No model fits all of that</div>
        <h2 className="mt-3 text-[24px] font-semibold tracking-[-0.02em] text-ink">Loosen one limit to see a pick.</h2>
        <p className="mt-2 max-w-[56ch] text-[14.5px] text-ink-2">Every model in this run is ruled out by at least one of your limits. Each button below lifts one of them.</p>
        <div className="mt-5 flex flex-wrap gap-2">
          {input.budget != null && (
            <Button variant="secondary" size="sm" onClick={() => onRelax({ budget: null })}>
              <Wallet size={14} /> Remove the budget limit
            </Button>
          )}
          {input.latency !== "any" && (
            <Button variant="secondary" size="sm" onClick={() => onRelax({ lat: null })}>
              <Clock size={14} /> Allow any latency
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

  const p = providerById(run, pick.provider)!;
  const tie = cheaperTie ?? tied[0];
  const reasons: { ok: boolean; text: ReactNode }[] = [
    {
      ok: true,
      text: (
        <>
          Lowest error on {where}: <span className="num text-ink">{pct(pick.score)}</span> (95% interval {pct(pick.ci_low)} to {pct(pick.ci_high)}).
        </>
      ),
    },
    ...pick.tagRanks
      .filter((r) => r.rank > 0)
      .map((r) => ({
        ok: r.rank === 1,
        text: (
          <>
            {r.rank === 1 ? "Ranked first" : `Ranked ${r.rank} of ${r.of}`} on {TAG_PHRASE[r.tag]} alone{r.tooSmall ? " (too few clips to conclude)" : ""}.
          </>
        ),
      })),
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
          Median latency <span className="num text-ink">{ms(pick.p50)}</span> per clip{input.latency !== "any" ? `, under your ${ms(Number(input.latency))} limit` : ""}.
        </>
      ),
    },
    { ok: true, text: p.uploads_audio ? <>Sends audio to the provider's servers.</> : <>Audio never leaves your machine.</> },
  ];

  return (
    <section aria-live="polite" aria-labelledby="pick-title" className="relative overflow-hidden rounded-lg border border-line bg-surface shadow-card">
      <Corners />
      <div className="grid gap-6 p-6 sm:p-8 md:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] md:gap-10">
        <div>
          <div className="label flex items-center gap-2">
            <SeriesDot color={providerColor(run, pick.provider)} />
            <span className="text-signal-ink">Our pick</span> / for your clips
          </div>
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={pick.provider}
              initial={reduce ? false : { opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduce ? undefined : { opacity: 0, y: -8 }}
              transition={{ duration: 0.35, ease }}
            >
              <h2 id="pick-title" className={cn("display mt-4 text-ink", p.label.length > 14 ? "text-[34px] sm:text-[40px]" : "text-[44px] sm:text-[56px]")}>
                {p.label}
              </h2>
              <p className="mt-2 font-mono text-[12px] text-ink-3">{p.model_id}</p>
              <div className="mt-6 grid grid-cols-3 gap-4">
                <Stat label="Error rate" value={pct(pick.score)} />
                <Stat label="Per hour" value={usdPerHour(p.usd_per_hour)} />
                <Stat label="Median latency" value={ms(pick.p50)} />
              </div>
              <div className="mt-7 flex flex-wrap gap-2">
                <Link to={`/providers/${pick.provider}`} className={buttonClass("primary", "sm", "group")}>
                  Open {p.label} <ArrowRight size={14} className="transition-transform group-hover:translate-x-0.5" />
                </Link>
                {tie && (
                  <Link to={`/compare?m=${[pick.provider, tie.provider].join(",")}${input.tags.length ? `&tags=${input.tags.join(",")}` : ""}`} className={buttonClass("secondary", "sm")}>
                    Compare with {providerLabel(run, tie.provider)}
                  </Link>
                )}
              </div>
            </motion.div>
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
                {tied.length > 1 ? ` and ${tied.length - 1} more` : ""}: it is {signedPts(tie.vsPick.diff)} on these clips ({pValue(tie.vsPick.p)}, interval crosses zero).
                {cheaperTie ? ` It costs ${usdPerHour(cheaperTie.price)} per hour, so if price matters it is just as good a choice.` : " Either is a sound choice."}
              </span>
            </p>
          )}
          {betterExcluded.slice(0, 1).map((b) => (
            <p key={b.provider} className="flex gap-3 text-[13.5px] text-ink-2">
              <X size={16} strokeWidth={1.75} className="mt-0.5 shrink-0 text-ink-3" aria-hidden />
              <span>
                {providerLabel(run, b.provider)} makes fewer errors on these clips ({pct(b.score)}) but is ruled out: {b.excluded.join(", ").toLowerCase()}.
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

function list(xs: string[], word: string) {
  if (xs.length <= 1) return xs.join("");
  return `${xs.slice(0, -1).join(", ")} ${word} ${xs[xs.length - 1]}`;
}

// ---------------------------------------------------------------- ranked list

function Ranked({ run, result }: { run: Run; result: Result }) {
  const { pick, nClips } = result;
  const rows = [...result.rows.filter((r) => !r.excluded.length), ...result.rows.filter((r) => r.excluded.length)];
  const max = Math.max(0.01, ...rows.map((r) => r.ci_high)) * 1.06;
  const tier: Tier = defaultTier(run);
  return (
    <Figure
      n={1}
      title="Every model, ranked for your clips"
      caption={`Mean per-clip error on the ${nClips} matching clips, with a 95% bootstrap interval. Ruled-out models stay listed, greyed, with the reason.`}
      source={`source: per_clip, tier=${tier}, metric=cer; paired bootstrap vs the pick, 1000 resamples`}
      bodyClassName="p-0 sm:p-0"
    >
      <ol>
        {rows.map((r, i) => (
          <motion.li layout transition={{ type: "spring", stiffness: 400, damping: 38 }} key={r.provider} className={cn("grid grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 border-b border-line px-4 py-3 last:border-b-0 sm:grid-cols-[28px_170px_minmax(0,1fr)_150px] sm:px-5", r.excluded.length && "bg-sunken/40")}>
            <span className={cn("num text-[12.5px]", r === pick ? "text-signal-ink" : "text-ink-3")}>{r.excluded.length ? <span className="sr-only">Ruled out</span> : String(i + 1).padStart(2, "0")}</span>
            <Link to={`/providers/${r.provider}`} className={cn("flex min-w-0 items-center gap-2 hover:underline", r.excluded.length ? "text-ink-3" : "text-ink")}>
              <SeriesDot color={providerColor(run, r.provider)} className={r.excluded.length ? "opacity-40" : ""} />
              <span className="truncate text-[13.5px] font-medium">{providerLabel(run, r.provider)}</span>
            </Link>
            <span className="col-span-3 row-start-2 flex items-center gap-3 sm:col-span-1 sm:row-start-auto">
              <ScoreBar r={r} max={max} color={providerColor(run, r.provider)} muted={r.excluded.length > 0} />
              <span className={cn("num w-12 shrink-0 text-right text-[13px]", r.excluded.length ? "text-ink-3" : "text-ink")}>{pct(r.score)}</span>
            </span>
            <span className="col-start-3 row-start-1 flex justify-end sm:col-start-auto sm:row-start-auto">
              <Status r={r} pick={pick} />
            </span>
          </motion.li>
        ))}
      </ol>
    </Figure>
  );
}

function ScoreBar({ r, max, color, muted }: { r: PickerRow; max: number; color: string; muted: boolean }) {
  const w = (v: number) => `${(v / max) * 100}%`;
  return (
    <span className={cn("relative h-5 flex-1", muted && "opacity-35")} role="img" aria-label={`${pct(r.score)}, interval ${pct(r.ci_low)} to ${pct(r.ci_high)}`}>
      <span className="absolute inset-y-0 left-0 w-px bg-line-strong" />
      <motion.span className="absolute top-[3px] bottom-[3px] left-0 rounded-r-[4px]" style={{ background: color }} initial={false} animate={{ width: w(r.score) }} transition={{ duration: 0.6, ease }} />
      <motion.span className="absolute top-1/2 h-px -translate-y-1/2 bg-ink" initial={false} animate={{ left: w(r.ci_low), width: `calc(${w(r.ci_high)} - ${w(r.ci_low)})` }} transition={{ duration: 0.6, ease }}>
        <span className="absolute -top-[3px] left-0 h-[7px] w-px bg-ink" />
        <span className="absolute -top-[3px] right-0 h-[7px] w-px bg-ink" />
      </motion.span>
    </span>
  );
}

function Status({ r, pick }: { r: PickerRow; pick?: PickerRow }) {
  if (r === pick) return <Badge tone="good">Pick</Badge>;
  if (r.excluded.length)
    return (
      <span className="flex flex-wrap justify-end gap-1">
        {r.excluded.map((x) => (
          <Badge key={x} tone="outline">
            {x}
          </Badge>
        ))}
      </span>
    );
  if (r.vsPick?.tied) return <Badge tone="ins">Tied with pick</Badge>;
  return <span className="num text-[12px] text-ink-3">{r.vsPick ? `${signedPts(r.vsPick.diff)} vs pick` : ""}</span>;
}

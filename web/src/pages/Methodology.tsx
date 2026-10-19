import { useEffect, useMemo, useState, type ReactNode } from "react";
import { extent, maxOf } from "@/lib/scale";
import { ArrowUpRight, ChevronDown } from "lucide-react";
import source from "../../../METHODOLOGY.md?raw";
import type { Run } from "@/lib/types";
import { useRun } from "@/lib/data";
import { bootstrapMean, defaultTier, overall, pairedBootstrap, perClipMatrix, primaryMetric, providerColor, providerLabel } from "@/lib/analysis";
import { pct, signedPts } from "@/lib/format";
import { inline, parse, type Block } from "@/lib/markdown";
import { useWidth } from "@/lib/useWidth";
import { useActiveHeading } from "@/lib/useActiveHeading";
import { GITHUB_URL } from "@/lib/config";
import { cn } from "@/lib/cn";
import { Rule } from "@/components/layout/Section";
import { Figure } from "@/components/ui/Card";
import { Segmented } from "@/components/ui/Segmented";
import { Skeleton } from "@/components/ui/Skeleton";
import { JudgeCalibration } from "@/components/v3/JudgeCalibration";

/**
 * Long-form methodology, rendered from the repository's METHODOLOGY.md (one source of truth) with a sticky
 * table of contents and three figures placed into the text: normalisation, scoring one clip, and the bootstrap.
 */
export default function Methodology() {
  const blocks = useMemo(() => parse(source), []);
  const title = blocks.find((b) => b.type === "h" && b.level === 1);
  const lead = blocks.find((b) => b.type === "p");
  const body = blocks.filter((b) => b !== title && b !== lead);
  const toc = body.filter((b): b is Extract<Block, { type: "h" }> => b.type === "h" && (b.level === 2 || b.level === 3));
  const active = useActiveHeading(toc.map((t) => t.id));
  useEffect(() => {
    document.title = "Methodology | clipbench";
    // The page is lazy-loaded, so the browser's own jump to #anchor happens before the target exists.
    const id = decodeURIComponent(location.hash.slice(1));
    if (id) requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView());
  }, []);

  let h2 = 0;
  return (
    <>
      <header className="frame frame-lines pt-12 pb-12 sm:pt-20 sm:pb-16">
        <div className="label mb-5 flex items-center gap-2">
          <span className="text-signal-ink">Docs</span> / Methodology
        </div>
        <h1 className="display text-[48px] text-ink sm:text-[72px]">How a number gets made.</h1>
        {lead?.type === "p" && <p className="mt-6 max-w-[62ch] text-[17px] leading-relaxed text-ink-2">{inline(lead.text)}</p>}
        <a href={`${GITHUB_URL}/blob/main/METHODOLOGY.md`} target="_blank" rel="noreferrer" className="mt-6 inline-flex items-center gap-1.5 font-mono text-[12px] text-ink-3 hover:text-ink">
          METHODOLOGY.md on GitHub <ArrowUpRight size={12} />
        </a>
      </header>
      <Rule />
      <div className="frame frame-lines grid gap-10 pt-10 pb-24 lg:grid-cols-[220px_minmax(0,1fr)] lg:gap-16 lg:pt-14">
        <Toc toc={toc} active={active} />
        <article className="min-w-0 max-w-[72ch]">
          {body.map((b, i) => {
            const before = b.type === "h" ? FIGURES_BEFORE[b.id] : undefined;
            return (
              <div key={i}>
                {before}
                <BlockView b={b} n={b.type === "h" && b.level === 2 ? ++h2 : undefined} />
              </div>
            );
          })}
        </article>
      </div>
    </>
  );
}

/** Figures placed at the end of a section, keyed by the id of the heading that follows it. */
const FIGURES_BEFORE: Record<string, ReactNode> = {
  "korean-ko": <NormFigure />,
  hallucination: <ScoreFigure />,
  "which-rows-are-scored": <BootFigure />,
  "commands-and-published-data": <SemanticFigure />,
};

/** A2: the semantic judge's calibration against human labels, from the latest export. */
function SemanticFigure() {
  const { data } = useRun();
  return (
    <div id="semantic-judge" className="my-8 scroll-mt-24">
      <JudgeCalibration run={data?.run} n={4} />
    </div>
  );
}

function Toc({ toc, active }: { toc: Extract<Block, { type: "h" }>[]; active: string | null }) {
  const [open, setOpen] = useState(false);
  const list = (
    <ol className="grid gap-0.5">
      {toc.map((t) => (
        <li key={t.id}>
          <a
            href={`#${t.id}`}
            onClick={() => setOpen(false)}
            className={cn(
              "relative block rounded-sm py-1 text-[13px] leading-snug transition-colors",
              t.level === 3 ? "pl-6 text-ink-3" : "pl-3 text-ink-2",
              active === t.id ? "text-ink" : "hover:text-ink",
            )}
          >
            {active === t.id && <span aria-hidden className="absolute top-1.5 bottom-1.5 left-0 w-[2px] rounded-full bg-signal" />}
            {t.text.replace(/`/g, "")}
          </a>
        </li>
      ))}
    </ol>
  );
  return (
    <nav aria-label="On this page" className="lg:sticky lg:top-20 lg:max-h-[calc(100dvh-100px)] lg:self-start lg:overflow-y-auto">
      <div className="hidden lg:block">
        <div className="label mb-3 pl-3">On this page</div>
        {list}
      </div>
      <div className="rounded-md border border-line bg-surface lg:hidden">
        <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="flex h-11 w-full items-center justify-between px-4 text-[13.5px] text-ink">
          On this page
          <ChevronDown size={16} className={cn("text-ink-3 transition-transform", open && "rotate-180")} />
        </button>
        {open && <div className="border-t border-line px-1 py-2">{list}</div>}
      </div>
    </nav>
  );
}

function BlockView({ b, n }: { b: Block; n?: number }) {
  switch (b.type) {
    case "h":
      if (b.level === 2)
        return (
          <h2 id={b.id} className={cn("mb-4 scroll-mt-24 text-[26px] leading-tight font-semibold tracking-[-0.02em] text-ink", n === 1 ? "mt-0" : "mt-16 border-t border-line pt-8")} style={{ fontStretch: "94%" }}>
            <span className="label mb-2 block text-signal-ink">{String(n).padStart(2, "0")}</span>
            {inline(b.text)}
          </h2>
        );
      if (b.level === 3)
        return (
          <h3 id={b.id} className="mt-10 mb-3 scroll-mt-24 text-[19px] font-semibold tracking-[-0.015em] text-ink">
            {inline(b.text)}
          </h3>
        );
      return (
        <h4 id={b.id} className="mt-7 mb-2 scroll-mt-24 text-[15px] font-semibold text-ink">
          {inline(b.text)}
        </h4>
      );
    case "p":
      return <p className="my-3.5 text-[15.5px] leading-[1.7] text-ink-2">{inline(b.text)}</p>;
    case "list": {
      const Tag = b.ordered ? "ol" : "ul";
      return (
        <Tag className={cn("my-3.5 grid gap-2 text-[15.5px] leading-[1.65] text-ink-2", b.ordered ? "list-decimal pl-6 marker:font-mono marker:text-[12.5px] marker:text-ink-3" : "pl-5")}>
          {b.items.map((it, i) => (
            <li key={i} className={cn(!b.ordered && "relative before:absolute before:top-[0.72em] before:-left-4 before:size-[5px] before:rounded-full before:bg-line-strong")}>
              {inline(it.text)}
              {it.children.map((c, j) => (
                <BlockView key={j} b={c} />
              ))}
            </li>
          ))}
        </Tag>
      );
    }
    case "table":
      return (
        <div className="my-5 overflow-x-auto rounded-md border border-line bg-surface">
          <table className="w-full border-separate border-spacing-0 text-[13.5px]">
            <thead>
              <tr>
                {b.head.map((h, i) => (
                  <th key={i} scope="col" className="border-b border-line px-4 py-2.5 text-left font-normal">
                    <span className="label">{h}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {b.rows.map((r, i) => (
                <tr key={i}>
                  {r.map((c, j) => (
                    <td key={j} className={cn("border-b border-line px-4 py-2.5 align-top text-ink-2", i === b.rows.length - 1 && "border-b-0", j === 0 && "whitespace-nowrap")}>
                      {inline(c)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
  }
}

// ---------------------------------------------------------------- FIG: normalisation

type Seg = [string, "keep" | "del" | "new"];
const NORM: { step: string; rule: string; segs: Seg[] }[] = [
  { step: "Caption", rule: "input", segs: [["［音楽］今日は、ＡＢＣカフェに行った！（笑）　２０２４年", "keep"]] },
  { step: "NFKC", rule: "unicodedata.normalize", segs: [["[音楽]今日は、", "keep"], ["ABC", "new"], ["カフェに行った", "keep"], ["!(", "new"], ["笑", "keep"], [") 2024", "new"], ["年", "keep"]] },
  { step: "Lowercase", rule: "str.lower", segs: [["[音楽]今日は、", "keep"], ["abc", "new"], ["カフェに行った!(笑) 2024年", "keep"]] },
  { step: "Bracket tags", rule: "[...] and (...) removed", segs: [["[音楽]", "del"], ["今日は、abcカフェに行った!", "keep"], ["(笑)", "del"], [" 2024年", "keep"]] },
  { step: "Punctuation", rule: "Unicode P* and S*", segs: [["今日は", "keep"], ["、", "del"], ["abcカフェに行った", "keep"], ["!", "del"], [" 2024年", "keep"]] },
  { step: "Whitespace", rule: "removed for ja", segs: [["今日はabcカフェに行った", "keep"], [" ", "del"], ["2024年", "keep"]] },
];
const READING: { step: string; rule: string; segs: Seg[] }[] = [
  { step: "Digits to reading", rule: "built-in converter", segs: [["今日はabcカフェに行った", "keep"], ["にせんにじゅうよん", "new"], ["年", "keep"]] },
  { step: "Reading, hiragana", rule: "fugashi + unidic kana", segs: [["きょうは", "new"], ["abc", "keep"], ["かふぇにいった", "new"], ["にせんにじゅうよん", "keep"], ["ねん", "new"]] },
];

function SegText({ segs }: { segs: Seg[] }) {
  return (
    <span lang="ja" className="font-mono text-[13px] break-all">
      {segs.map(([t, k], i) => (
        <span
          key={i}
          className={cn(
            k === "del" && "rounded-[3px] bg-signal-soft text-signal-ink line-through decoration-signal",
            k === "new" && "rounded-[3px] bg-ins-soft text-ins",
            k === "keep" && "text-ink",
          )}
        >
          {t}
        </span>
      ))}
    </span>
  );
}

function NormFigure() {
  const [metric, setMetric] = useState<"cer" | "cer_reading">("cer");
  const steps = metric === "cer" ? NORM : [...NORM, ...READING];
  return (
    <Figure
      n={1}
      className="my-8"
      title="One Japanese caption through the normaliser"
      caption="An illustrative caption, not from the clip set. Removed characters are struck through; changed or added ones are tinted."
      controls={
        <Segmented
          label="Metric"
          value={metric}
          onChange={setMetric}
          options={[
            { value: "cer", label: "cer" },
            { value: "cer_reading", label: "cer_reading" },
          ]}
        />
      }
      source="source: clipbench/scoring/normalize.py, tests/test_normalize.py"
      bodyClassName="p-0 sm:p-0"
    >
      <ol>
        {steps.map((s, i) => (
          <li key={s.step} className="grid gap-1 border-b border-line px-4 py-3 last:border-b-0 sm:grid-cols-[180px_minmax(0,1fr)] sm:gap-4 sm:px-5">
            <span>
              <span className="flex items-baseline gap-2 text-[13px] text-ink">
                <span className="num text-[11px] text-ink-3">{String(i).padStart(2, "0")}</span>
                {s.step}
              </span>
              <span className="block pl-6 font-mono text-[11px] text-ink-3">{s.rule}</span>
            </span>
            <SegText segs={s.segs} />
          </li>
        ))}
      </ol>
    </Figure>
  );
}

// ---------------------------------------------------------------- FIG: one clip

const REF = ["今", "日", "は", "カ", "フ", "ェ", "に", "行", "っ", "た"];
const ALIGN: { r: string | null; h: string | null; op: "H" | "S" | "D" | "I" }[] = [
  { r: "今", h: "今", op: "H" },
  { r: "日", h: "日", op: "H" },
  { r: "は", h: "わ", op: "S" },
  { r: "カ", h: "カ", op: "H" },
  { r: "フ", h: "フ", op: "H" },
  { r: "ェ", h: "ェ", op: "H" },
  { r: "に", h: null, op: "D" },
  { r: "行", h: "行", op: "H" },
  { r: "っ", h: "っ", op: "H" },
  { r: "た", h: "た", op: "H" },
  { r: null, h: "よ", op: "I" },
];

function ScoreFigure() {
  const tone = { H: "", S: "bg-signal-soft text-signal-ink", D: "bg-signal-soft text-signal-ink", I: "bg-ins-soft text-ins" };
  return (
    <Figure n={2} className="my-8" title="Scoring one clip" caption="Reference and hypothesis after normalisation, aligned character by character. CER = (S + D + I) / (H + S + D)." source="source: clipbench/scoring/metrics.py (jiwer alignment)">
      <div className="overflow-x-auto">
        <table className="mx-auto border-separate border-spacing-x-1 border-spacing-y-1.5" lang="ja">
          <tbody>
            {(["ref", "hyp", "op"] as const).map((row) => (
              <tr key={row}>
                <th scope="row" className="pr-3 text-left font-normal">
                  <span className="label">{row === "ref" ? "Reference" : row === "hyp" ? "Heard" : "Edit"}</span>
                </th>
                {ALIGN.map((a, i) => (
                  <td key={i} className="p-0 text-center">
                    {row === "op" ? (
                      <span className={cn("num text-[11px]", a.op === "H" ? "text-ink-3" : a.op === "I" ? "text-ins" : "text-signal-ink")}>{a.op}</span>
                    ) : (
                      <span className={cn("grid size-8 place-items-center rounded-[5px] border font-mono text-[15px]", (row === "ref" ? a.r : a.h) == null ? "border-dashed border-line-strong text-ink-3" : a.op === "H" ? "border-line bg-surface text-ink" : cn("border-transparent", tone[a.op]))}>
                        {(row === "ref" ? a.r : a.h) ?? ""}
                      </span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <dl className="mt-5 grid grid-cols-2 gap-px overflow-hidden rounded-sm border border-line bg-line sm:grid-cols-5">
        {[
          ["Hits", "8"],
          ["Substitutions", "1"],
          ["Deletions", "1"],
          ["Insertions", "1"],
          ["CER", `3 / ${REF.length} = ${pct(3 / REF.length)}`],
        ].map(([k, v]) => (
          <div key={k} className="bg-surface px-3 py-2.5">
            <dt className="label">{k}</dt>
            <dd className="num mt-1 text-[14px] text-ink">{v}</dd>
          </div>
        ))}
      </dl>
    </Figure>
  );
}

// ---------------------------------------------------------------- FIG: bootstrap

function BootFigure() {
  const run = useRun().data?.run;
  const [mode, setMode] = useState<"one" | "pair">("one");
  return (
    <Figure
      n={3}
      className="my-8"
      title={mode === "one" ? "Bootstrap interval for one model" : "Paired bootstrap for a pair of models"}
      caption={
        mode === "one"
          ? "1,000 resamples of the clips, drawn with replacement. The middle 95% of the resampled error rates is the interval."
          : "Each resample applies the same clip draw to both models. If the middle 95% of the differences crosses zero, there is no detectable difference."
      }
      controls={
        <Segmented
          label="Bootstrap"
          value={mode}
          onChange={setMode}
          options={[
            { value: "one", label: "One model" },
            { value: "pair", label: "Paired" },
          ]}
        />
      }
      source={run ? `source: per_clip of run ${run.run_id}, tier=${defaultTier(run)}; mean of per-clip rates, seeded, for illustration` : "source: per_clip"}
    >
      {run ? <BootChart run={run} mode={mode} /> : <Skeleton className="h-[220px] w-full" />}
    </Figure>
  );
}

function BootChart({ run, mode }: { run: Run; mode: "one" | "pair" }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const tier = defaultTier(run);
  const rows = overall(run, tier);
  const a = rows[0]?.provider;
  const b = rows[1]?.provider;
  const data = useMemo(() => {
    if (!a) return null;
    const m = perClipMatrix(run, tier, primaryMetric(run).name);
    const ids = [...m.keys()].filter((id) => m.get(id)!.has(a) && (!b || m.get(id)!.has(b)));
    const xa = ids.map((id) => m.get(id)!.get(a)!);
    if (mode === "one" || !b) {
      const r = bootstrapMean(xa, 7, 1000);
      return { samples: r.samples, lo: r.ci_low, hi: r.ci_high, obs: r.mean, zero: false, n: ids.length };
    }
    const xb = ids.map((id) => m.get(id)!.get(b)!);
    const r = pairedBootstrap(xa, xb, 7, 1000);
    return { samples: r.samples, lo: r.ci_low, hi: r.ci_high, obs: r.diff, zero: true, n: ids.length, p: r.p };
  }, [run, tier, a, b, mode]);
  if (!data || !a) return <p className="text-[13px] text-ink-3">No per-clip data in this run.</p>;
  const H = 220;
  const pad = { l: 12, r: 12, t: 26, b: 30 };
  const [min, max] = extent(data.zero ? [...data.samples, 0] : data.samples);
  const span = max - min || 1;
  const lo0 = min - span * 0.05;
  const hi0 = max + span * 0.05;
  const BINS = 44;
  const counts = new Array(BINS).fill(0) as number[];
  for (const s of data.samples) counts[Math.min(BINS - 1, Math.floor(((s - lo0) / (hi0 - lo0)) * BINS))]!++;
  const cmax = maxOf(counts, 1) || 1;
  const x = (v: number) => pad.l + ((v - lo0) / (hi0 - lo0)) * (w - pad.l - pad.r);
  const bw = (w - pad.l - pad.r) / BINS;
  const y = (c: number) => H - pad.b - (c / cmax) * (H - pad.t - pad.b);
  const fmt = (v: number) => (data.zero ? signedPts(v) : pct(v));
  const color = providerColor(run, a);
  return (
    <div ref={ref}>
      <svg width={w} height={H} role="img" aria-label={`Histogram of 1000 resampled ${data.zero ? "differences" : "error rates"}; 95% interval ${fmt(data.lo)} to ${fmt(data.hi)}`}>
        <rect x={x(data.lo)} y={pad.t - 8} width={Math.max(0, x(data.hi) - x(data.lo))} height={H - pad.t - pad.b + 8} fill="var(--sunken)" />
        {counts.map((c, i) => {
          const mid = lo0 + ((i + 0.5) / BINS) * (hi0 - lo0);
          const inside = mid >= data.lo && mid <= data.hi;
          return <rect key={i} x={pad.l + i * bw + 0.5} y={y(c)} width={Math.max(1, bw - 1.5)} height={H - pad.b - y(c)} rx={1.5} fill={data.zero ? "var(--ink-2)" : color} opacity={inside ? 0.9 : 0.3} />;
        })}
        <line x1={pad.l} x2={w - pad.r} y1={H - pad.b} y2={H - pad.b} stroke="var(--line-strong)" />
        {[data.lo, data.hi].map((v, i) => (
          <g key={i}>
            <line x1={x(v)} x2={x(v)} y1={pad.t - 8} y2={H - pad.b} stroke="var(--ink)" strokeDasharray="3 3" />
            <text x={x(v)} y={H - 10} textAnchor={i ? "start" : "end"} dx={i ? 4 : -4} className="fill-[var(--ink-2)] font-mono text-[11px]">
              {i ? "97.5%" : "2.5%"} {fmt(v)}
            </text>
          </g>
        ))}
        {data.zero && (
          <g>
            <line x1={x(0)} x2={x(0)} y1={pad.t - 14} y2={H - pad.b} stroke="var(--signal)" strokeWidth={1.5} />
            <text x={x(0)} y={pad.t - 16} textAnchor="middle" className="fill-[var(--signal-ink)] font-mono text-[11px]">
              zero
            </text>
          </g>
        )}
        <text x={(x(data.lo) + x(data.hi)) / 2} y={pad.t - 12} textAnchor="middle" className="fill-[var(--ink)] font-mono text-[11px]">
          95% of resamples
        </text>
      </svg>
      <p className="mt-3 text-[13px] text-ink-2">
        {data.zero ? (
          <>
            {providerLabel(run, a)} minus {providerLabel(run, b!)} on {data.n} shared clips: <span className="num text-ink">{fmt(data.obs)}</span>, interval {fmt(data.lo)} to {fmt(data.hi)}.{" "}
            {data.lo <= 0 && data.hi >= 0 ? "The interval crosses zero: no detectable difference." : "The interval excludes zero: a real difference on these clips."}
          </>
        ) : (
          <>
            {providerLabel(run, a)} on {data.n} clips: <span className="num text-ink">{fmt(data.obs)}</span>, 95% interval {fmt(data.lo)} to {fmt(data.hi)}.
          </>
        )}
      </p>
    </div>
  );
}

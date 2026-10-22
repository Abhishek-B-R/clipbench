import type { ReactNode } from "react";
import { Link } from "react-router";
import { ArrowRight } from "lucide-react";
import type { Run } from "@/lib/types";
import { README_FIRST_RUN } from "@/lib/config";
import { earlyLook, flaggedClips, runSpend, scoredTranscriptions } from "@/lib/early";
import { LANGUAGE_NAME } from "@/lib/analysis";
import { int, pct } from "@/lib/format";
import { Reveal, Rule } from "@/components/layout/Section";
import { EarlyBoardTable, EarlyLabel } from "@/components/early/EarlyBoard";

/** Four numbers from the latest run, under the hero: clips, systems, scored transcriptions, metered cost. */
export function EarlyStatStrip({ run }: { run: Run }) {
  const look = earlyLook(run);
  const stats = [
    { value: int(look.total), label: "Clips scored", note: `${LANGUAGE_NAME[run.language] ?? run.language}, from ${int(look.channels)} channels. Creator captions, none checked by ear yet.` },
    { value: int(look.rows.length), label: "Systems on the same clips", note: "APIs, open models on a laptop, and YouTube's own auto captions." },
    { value: int(scoredTranscriptions(run, look.tier)), label: "Scored transcriptions", note: "One main pass per system and clip. Stability reruns are on top." },
    { value: `$${runSpend(run, look.tier).toFixed(2)}`, label: "Cost of the main pass", note: "Every system together, at list prices." },
  ];
  return (
    <>
      <Rule />
      <div className="frame frame-lines">
        <dl className="grid grid-cols-2 lg:grid-cols-4">
          {stats.map((s, i) => (
            <div key={s.label} className={`py-7 pr-4 sm:py-9 ${i % 2 ? "border-l border-line pl-4 sm:pl-6" : ""} ${i === 2 ? "lg:border-l lg:border-line lg:pl-6" : ""} ${i > 1 ? "border-t border-line lg:border-t-0" : ""}`}>
              <dt className="label">{s.label}</dt>
              <dd className="mt-3 text-[32px] leading-none font-medium tracking-[-0.03em] text-ink sm:text-[40px]" style={{ fontStretch: "95%" }}>
                {s.value}
              </dd>
              <dd className="mt-3 max-w-[30ch] text-[13px] text-ink-2">{s.note}</dd>
            </div>
          ))}
        </dl>
      </div>
    </>
  );
}

function Card({ i, kicker, value, unit, title, children, source, link }: { i: number; kicker: string; value: ReactNode; unit?: ReactNode; title: string; children: ReactNode; source: string; link?: { to: string; label: string } }) {
  return (
    <Reveal delay={i * 0.05} className="h-full">
      <article className="flex h-full flex-col rounded-md border border-line bg-surface shadow-card">
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <span className="label">
            <span className="text-ink-2">{String(i + 1).padStart(2, "0")}</span> / {kicker}
          </span>
        </div>
        <div className="flex flex-1 flex-col px-5 pt-5 pb-4">
          <p className="flex flex-wrap items-baseline gap-x-2 text-[34px] leading-none font-medium tracking-[-0.03em] text-ink sm:text-[38px]" style={{ fontStretch: "95%" }}>
            {value}
            {unit && <span className="text-[14px] font-normal tracking-normal text-ink-3">{unit}</span>}
          </p>
          <h3 className="mt-4 text-[16px] font-semibold tracking-[-0.01em] text-ink">{title}</h3>
          <p className="mt-1.5 flex-1 text-[14px] leading-relaxed text-ink-2">{children}</p>
          {link && (
            <Link to={link.to} className="mt-4 inline-flex items-center gap-1 self-start text-[13px] text-ink-2 underline decoration-line-strong underline-offset-4 hover:text-ink">
              {link.label} <ArrowRight size={12} />
            </Link>
          )}
        </div>
        <div className="border-t border-line px-5 py-2.5 font-mono text-[11px] text-ink-3">{source}</div>
      </article>
    </Reveal>
  );
}

/**
 * What the first real run surfaced (README, "First run"), as four cards. Every number is computed from the export,
 * except the contention example, which only the README states (README_FIRST_RUN, with its source line).
 */
export function FirstRun({ run }: { run: Run }) {
  const look = earlyLook(run);
  const yt = look.rows.find((r) => r.provider === "youtube-auto");
  const closest = look.rows[0];
  const hall = [...look.rows].sort((a, b) => b.hallucination_rate - a.hallucination_rate)[0];
  const othersZero = hall ? look.rows.filter((r) => r.provider !== hall.provider && r.hallucination_rate === 0).length : 0;
  const flagged = flaggedClips(run, look.tier);
  const lm = look.latency;
  const src = `run=${run.run_id}`;
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      <Card
        i={0}
        kicker="Latency"
        value={`${README_FIRST_RUN.contendedLatencyS} s`}
        unit={`vs ${README_FIRST_RUN.aloneLatencyS} s alone`}
        title="Latency is easy to measure wrong."
        source={`source: ${README_FIRST_RUN.source}; run_meta.latency_mode`}
        link={{ to: "/early", label: "Latency in the early look" }}
      >
        With every provider uploading at once, one provider took {README_FIRST_RUN.contendedLatencyS} s for a clip it returns in {README_FIRST_RUN.aloneLatencyS} s alone. clipbench now times one provider at a time and records the mode{lm.short ? `: ${lm.short}` : ""}.
      </Card>
      {yt && closest ? (
        <Card
          i={1}
          kicker="References"
          value={pct(yt.error_rate)}
          unit={`CER, ${int(yt.n_clips)} clips`}
          title="Creator captions favour YouTube."
          source={`source: aggregates, ${src}, tier=${look.tier}`}
          link={{ to: "/methodology", label: "Why verified references" }}
        >
          {closest.provider === yt.provider ? `${yt.label} scored closest to the creators' own captions` : `${yt.label} scored ${pct(yt.error_rate)} against the creators' own captions`}, most likely because many creators start from them. Only references corrected by ear are fair ground truth.
        </Card>
      ) : null}
      {hall && (
        <Card
          i={2}
          kicker="Hallucination"
          value={pct(hall.hallucination_rate)}
          unit="hallucination rate"
          title="Hallucination is real."
          source={`source: aggregates (hallucination_rate), ${src}`}
          link={{ to: `/providers/${encodeURIComponent(hall.provider)}`, label: hall.label }}
        >
          {hall.label} invented text worth {pct(hall.hallucination_rate, 0)} of the reference length, mostly repetition loops.
          {othersZero === look.rows.length - 1 ? ` The other ${int(othersZero)} systems stayed at ${pct(0)}.` : ""}
        </Card>
      )}
      <Card
        i={3}
        kicker="Bad references"
        value={int(flagged.ids.size)}
        unit={flagged.ids.size === 1 ? "clip flagged" : "clips flagged"}
        title="References break."
        source={`source: ${flagged.source === "export" ? "errors.all_fail" : "per_clip"}, ${src}`}
        link={{ to: "/clips?flagged=1", label: "See the flagged clips" }}
      >
        Every system scored above {pct(flagged.threshold, 0)} error on these, which points at the caption, not the systems. At least one is Korean speech under a Japanese caption track. They go back to the review queue automatically.
      </Card>
    </div>
  );
}

/** The top of the early-look board, with the label, linking to /early. */
export function EarlySnapshot({ run, limit = 5 }: { run: Run; limit?: number }) {
  const look = earlyLook(run);
  return (
    <div className="grid gap-4">
      <EarlyLabel compact />
      <div className="overflow-hidden rounded-md border border-line bg-surface shadow-card">
        <EarlyBoardTable look={look} limit={limit} compact />
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-3 sm:px-5">
          <span className="font-mono text-[11px] text-ink-3">
            source: aggregates, run={run.run_id}, tier={look.tier}, metric={look.metric}
          </span>
          <Link to="/early" className="inline-flex items-center gap-1 text-[13px] font-medium text-ink hover:underline">
            All {int(look.rows.length)} systems, charts and per-clip heat <ArrowRight size={13} />
          </Link>
        </div>
      </div>
    </div>
  );
}

import { useEffect } from "react";
import { useRun } from "@/lib/data";
import { Section, Rule } from "@/components/layout/Section";
import { Skeleton } from "@/components/ui/Skeleton";
import { Hero } from "@/landing/Hero";
import { MiniLeaderboard } from "@/landing/MiniLeaderboard";
import { ByClipType } from "@/landing/ByClipType";
import { Heatmap } from "@/landing/Heatmap";
import { CostSection } from "@/landing/CostSection";
import { Breaks } from "@/landing/Breaks";
import { Reproduce } from "@/landing/Reproduce";
import { Method } from "@/landing/Method";
import { RequestCTA } from "@/landing/RequestCTA";
import { FAQ } from "@/landing/FAQ";

/**
 * The landing page. Order: the finding (hero + clip card), the ranking, where each model wins, the full
 * matrix, what accuracy costs, what breaks each model, how to rerun it, why to trust it, ask for more, FAQ.
 */
export default function Landing() {
  const state = useRun();
  const run = state.data?.run;
  useEffect(() => {
    document.title = "clipbench: speech-to-text on real short-form video";
  }, []);

  return (
    <>
      <Hero run={run} />

      <Section
        id="leaderboard"
        index="01"
        kicker="Leaderboard"
        title="One clip set. Every model. Intervals on every number."
        intro="Ranked by character error rate on clips whose references a person checked by ear. A lead counts only when the paired test backs it."
      >
        {run ? <MiniLeaderboard run={run} /> : <FigureSkeleton />}
      </Section>

      <Section
        id="clip-types"
        index="02"
        kicker="By clip type"
        title="The best model overall is not the best on every clip."
        intro="Short-form video is not one kind of audio. Pick a kind of clip to see who handles it, and whether the lead survives a paired test."
      >
        {run ? <ByClipType run={run} /> : <FigureSkeleton />}
      </Section>

      <Section id="matrix" index="03" kicker="Matrix" title="Where each model struggles, at a glance." intro="The same numbers as a table you can scan. Hatched cells have too few clips to say anything.">
        {run ? <Heatmap run={run} /> : <FigureSkeleton />}
      </Section>

      <Section id="cost" index="04" kicker="Cost" title="What accuracy costs per hour of audio." intro="List prices, checked on the run date, against measured error. Some of the best value is not at the top of the table.">
        {run ? <CostSection run={run} /> : <FigureSkeleton />}
      </Section>

      <Section id="breaks" index="05" kicker="Failure modes" title="What breaks each model." intro="Each model's error rate on its worst kind of clip, against its own average, and the slips it makes most often.">
        {run ? <Breaks run={run} /> : <FigureSkeleton />}
      </Section>

      <Section
        id="reproduce"
        index="06"
        kicker="Reproduce"
        title="Don't take our word for it. Rerun it."
        intro="The whole benchmark is one repository and five commands. Runs are cached and stop at a budget cap, so checking our numbers is cheap."
      >
        <Reproduce run={run} />
      </Section>

      <Section id="method" index="07" kicker="Method" title="Built so a number means something.">
        <Method run={run} />
      </Section>

      <Rule />
      <section aria-labelledby="request-title" className="frame frame-lines py-20 sm:py-28">
        <RequestCTA />
      </section>

      <Section id="faq" index="08" kicker="FAQ">
        <FAQ />
      </Section>
      {state.status === "error" && (
        <p role="alert" className="frame py-6 text-[14px] text-signal-ink">
          Could not load run data: {state.error.message}
        </p>
      )}
    </>
  );
}

function FigureSkeleton() {
  return (
    <div className="grid gap-3 rounded-md border border-line bg-surface p-5">
      <Skeleton className="h-4 w-56" />
      <Skeleton className="h-48 w-full" />
    </div>
  );
}

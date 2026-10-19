import { useEffect } from "react";
import { useRun, useSource } from "@/lib/data";
import { isPublished, languageProgress } from "@/lib/analysis";
import { ACCOUNTS } from "@/lib/flags";
import { POSITIONING } from "@/lib/config";
import { Section, Rule } from "@/components/layout/Section";
import { Skeleton } from "@/components/ui/Skeleton";
import { LanguageBoard } from "@/components/ui/Gate";
import { ErrorState } from "@/components/ui/State";
import { Hero } from "@/landing/Hero";
import { Measures } from "@/landing/Measures";
import { Dataset } from "@/landing/Dataset";
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
 * The homepage. Leads with the positioning question. Rankings appear only for a published language
 * (docs/V2.md publication gate); before that the page is the method, the dataset spec and live progress.
 */
export default function Landing() {
  const state = useRun();
  const src = useSource();
  const run = state.data?.run;
  const progress = src.data ? languageProgress(src.data.index) : src.status === "error" ? languageProgress(undefined) : undefined;
  const published = Boolean(run && isPublished(run));
  useEffect(() => {
    document.title = `clipbench: ${POSITIONING.question}`;
  }, []);

  let i = 0;
  const idx = () => String(++i).padStart(2, "0");

  return (
    <>
      <Hero run={run} progress={progress} published={published} />

      {published && run ? (
        <>
          <Section id="leaderboard" index={idx()} kicker="Leaderboard" title="One clip set. Every model. Intervals on every number." intro="Ranked by error rate on clips whose references a person checked by ear. A lead counts only when the paired test backs it.">
            <MiniLeaderboard run={run} />
          </Section>
          <Section id="clip-types" index={idx()} kicker="By clip type" title="The best model overall is not the best on every clip." intro="Short-form video is not one kind of audio. Pick a kind of clip to see who handles it, and whether the lead survives a paired test.">
            <ByClipType run={run} />
          </Section>
          <Section id="matrix" index={idx()} kicker="Matrix" title="Where each model struggles, at a glance." intro="The same numbers as a table you can scan. Hatched cells have too few clips to say anything.">
            <Heatmap run={run} />
          </Section>
          <Section id="cost" index={idx()} kicker="Cost" title="What accuracy costs per hour of audio." intro="List prices, checked on the run date, against measured error.">
            <CostSection run={run} />
          </Section>
          <Section id="breaks" index={idx()} kicker="Failure modes" title="What breaks each model." intro="Each model's error rate on its worst kind of clip, against its own average, and the slips it makes most often.">
            <Breaks run={run} />
          </Section>
        </>
      ) : (
        <>
          <Section
            id="measures"
            index={idx()}
            kicker="What we measure"
            title="Scored on what a captioning product ships."
            intro="Public speech-to-text boards measure read speech and phone calls, mostly in English. Video apps live on music beds, fast edits, word timing and more than one language."
          >
            <Measures />
          </Section>
          <Section
            id="progress"
            index={idx()}
            kicker="Progress"
            title="First results: in progress."
            intro="No ranking is published until a language passes the gate below. These counts come straight from the latest export and update with every run."
          >
            {progress ? <LanguageBoard rows={progress} /> : <Skeleton className="h-80 w-full" />}
            {src.status === "error" && <ErrorState className="mt-4" title="Could not load the run index." body="Progress counts show once the data files load." onRetry={() => location.reload()} />}
          </Section>
          <Section id="dataset" index={idx()} kicker="Dataset" title="Any public short-form clip, by id." intro="The clip set is a plain CSV per language. Any public Short, Reel or TikTok id can go in; rules in code keep it from leaning on a few channels.">
            <Dataset />
          </Section>
        </>
      )}

      <Section id="reproduce" index={idx()} kicker="Reproduce" title="Don't take our word for it. Rerun it." intro="The whole benchmark is one repository and a handful of commands. Runs are cached and stop at a budget cap, so checking our numbers is cheap.">
        <Reproduce run={run} />
      </Section>

      <Section id="method" index={idx()} kicker="Method" title="Built so a number means something.">
        <Method run={run} />
      </Section>

      {ACCOUNTS && (
        <>
          <Rule />
          <section aria-labelledby="request-title" className="frame frame-lines py-20 sm:py-28">
            <RequestCTA />
          </section>
        </>
      )}

      <Section id="faq" index={idx()} kicker="FAQ">
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

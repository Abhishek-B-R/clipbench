import type { Run } from "@/lib/types";
import { Section } from "@/components/layout/Section";
import { MiniLeaderboard } from "./MiniLeaderboard";
import { ByClipType } from "./ByClipType";
import { Heatmap } from "./Heatmap";
import { CostSection } from "./CostSection";
import { Breaks } from "./Breaks";

/**
 * Homepage sections that show a ranking. Rendered only for a published language (docs/V2.md publication gate) and
 * lazy-loaded by pages/Landing.tsx, so an unpublished site never ships their chart code on first load.
 * `index` carries the five section numbers so the page's numbering stays continuous.
 */
export default function PublishedSections({ run, index }: { run: Run; index: string[] }) {
  return (
    <>
      <Section id="leaderboard" index={index[0]!} kicker="Leaderboard" title="One clip set. Every model. Intervals on every number." intro="Ranked by error rate on clips whose references a person checked by ear. A lead counts only when the paired test backs it.">
        <MiniLeaderboard run={run} />
      </Section>
      <Section id="clip-types" index={index[1]!} kicker="By clip type" title="The best model overall is not the best on every clip." intro="Short-form video is not one kind of audio. Pick a kind of clip to see who handles it, and whether the lead survives a paired test.">
        <ByClipType run={run} />
      </Section>
      <Section id="matrix" index={index[2]!} kicker="Matrix" title="Where each model struggles, at a glance." intro="The same numbers as a table you can scan. Hatched cells have too few clips to say anything.">
        <Heatmap run={run} />
      </Section>
      <Section id="cost" index={index[3]!} kicker="Cost" title="What accuracy costs per hour of audio." intro="List prices, checked on the run date, against measured error.">
        <CostSection run={run} />
      </Section>
      <Section id="breaks" index={index[4]!} kicker="Failure modes" title="What breaks each model." intro="Each model's error rate on its worst kind of clip, against its own average, and the slips it makes most often.">
        <Breaks run={run} />
      </Section>
    </>
  );
}

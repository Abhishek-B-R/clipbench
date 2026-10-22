import { lazy, Suspense, useEffect } from "react";
import { useRun, useSource } from "@/lib/data";
import { LANGUAGE_NAME, isPublished, languageProgress } from "@/lib/analysis";
import { ACCOUNTS } from "@/lib/flags";
import { POSITIONING } from "@/lib/config";
import { Section, Rule } from "@/components/layout/Section";
import { Skeleton } from "@/components/ui/Skeleton";
import { LanguageBoard } from "@/components/ui/Gate";
import { ErrorState } from "@/components/ui/State";
import { Hero } from "@/landing/Hero";
import { Measures } from "@/landing/Measures";
import { Dataset } from "@/landing/Dataset";
import { Reproduce } from "@/landing/Reproduce";
import { Method } from "@/landing/Method";
import { RequestCTA } from "@/landing/RequestCTA";
import { FAQ } from "@/landing/FAQ";
import { EarlySnapshot, EarlyStatStrip, FirstRun } from "@/landing/FirstRun";
import { YOUTUBE_NOTE, earlyLook } from "@/lib/early";
import { int } from "@/lib/format";

// The ranking sections (board, clip types, matrix, cost chart, failure modes) only render for a published language,
// so their code loads as its own chunk instead of weighing on every first visit while nothing is published.
const PublishedSections = lazy(() => import("@/landing/PublishedSections"));

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
      <Hero run={run} published={published} />
      {!published && run && earlyLook(run).rows.length > 0 && <EarlyStatStrip run={run} />}

      {published && run ? (
        <>
          <Suspense fallback={<div className="frame py-20"><Skeleton className="h-96 w-full" /></div>}>
            <PublishedSections run={run} index={[idx(), idx(), idx(), idx(), idx()]} />
          </Suspense>
        </>
      ) : (
        <>
          {run && earlyLook(run).rows.length > 0 && (
            <>
              <Section
                id="first-run"
                index={idx()}
                kicker="First run"
                title="What the first real run already showed."
                intro={`${int(earlyLook(run).total)} ${LANGUAGE_NAME[run.language] ?? run.language} clips from ${int(earlyLook(run).channels)} channels, ${int(earlyLook(run).rows.length)} systems, scored against the creators' own captions. A pipeline check, not a ranking, and it already surfaced four things.`}
              >
                <FirstRun run={run} />
              </Section>
              <Section
                id="early"
                index={idx()}
                kicker="Early look"
                title="Every system on the same clips."
                intro={YOUTUBE_NOTE}
              >
                <EarlySnapshot run={run} />
              </Section>
            </>
          )}
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
            title="The road to a ranking."
            intro="A language is ranked only once its references are corrected by ear and enough systems cover them. These counts come straight from the latest export."
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

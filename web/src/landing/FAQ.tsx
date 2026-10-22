import * as A from "@radix-ui/react-accordion";
import { Plus } from "lucide-react";
import { Link } from "react-router";
import { GITHUB_URL, RULES } from "@/lib/config";
import { ACCOUNTS } from "@/lib/flags";

const QA: { q: string; a: React.ReactNode }[] = [
  {
    q: "Which languages, and when?",
    a: "English, Japanese, Korean, Spanish, Hindi with Hinglish, and French. The progress board on this page shows where each one stands. A language gets a ranking only when it passes the publication gate: enough clips verified by ear, enough providers with results on nearly all of them, and clips spread across channels and content types. Until then this site shows progress, not a ranking.",
  },
  {
    q: "How is this different from other speech-to-text leaderboards?",
    a: "Most public boards score read speech, meetings or phone calls, mostly in English. clipbench scores real short-form video with music, edits and code-switching, and measures what a caption product needs: word timing, invented text over music, punctuation, and cost.",
  },
  {
    q: "What is CER, and why not WER?",
    a: "Character error rate counts the characters a model substituted, dropped or added, divided by the length of the reference. Japanese has no spaces, so words depend on a tokenizer; characters do not. We also report a reading-normalised CER that forgives kanji versus kana spelling differences.",
  },
  {
    q: "What does verified mean?",
    a: "A person listened to each clip and corrected the caption text by ear. Creator captions are often edited or summarised, so scores against them are shown separately and labelled as agreement, not accuracy.",
  },
  {
    q: "Why should I trust a claim that one model makes fewer errors?",
    a: (
      <>
        Every model runs on the same clips, so we compare them clip by clip with a paired bootstrap. A lead is reported only when the 95% interval of the difference excludes zero; otherwise the site says there is no detectable difference. Slices under {RULES.minSliceClips} clips are marked too small. The details are on the{" "}
        <Link to="/methodology" className="text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink">
          methodology page
        </Link>
        .
      </>
    ),
  },
  {
    q: "What does a run cost, and who pays?",
    a: "The maintainer pays for the published runs. A full run costs a few dollars at list prices, and a hard budget cap stops it before it spends more than the configured amount. If you rerun it, you pay your own providers.",
  },
  {
    q: "Is any audio or caption text published?",
    a: "No. The site publishes clip ids, tags, durations and scores. Error examples are limited to a few characters. Anyone can rebuild the clip set from the ids.",
  },
  {
    q: "Is clipbench paid by any provider?",
    a: "No. It is not affiliated with or funded by any provider tested, and every model goes through the same code path with its own list price.",
  },
  {
    q: "Can I add a model?",
    a: (
      <>
        Yes. Each provider is one small adapter in the repository. Open a pull request, or{" "}
        {ACCOUNTS ? (
          <Link to="/requests" className="text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink">
            request it
          </Link>
        ) : (
          <a href={`${GITHUB_URL}/issues`} target="_blank" rel="noreferrer" className="text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink">
            open an issue
          </a>
        )}
        .
      </>
    ),
  },
];

/** FAQ: heading and help link left, accordion right (Wix pattern). Radix accordion: keyboard and aria built in. */
export function FAQ() {
  return (
    <div className="grid gap-10 md:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
      <div>
        <h2 id="faq-title" className="display text-[40px] sm:text-[48px]">
          Questions
        </h2>
        <p className="mt-4 max-w-[34ch] text-[15px] text-ink-2">
          Something not covered here? The{" "}
          <Link to="/methodology" className="text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink">
            methodology
          </Link>{" "}
          has the long answers, and the code has the exact ones.
        </p>
      </div>
      <A.Root type="multiple" className="border-t border-line">
        {QA.map((x, i) => (
          <A.Item key={x.q} value={`q${i}`} className="border-b border-line">
            <A.Header>
              <A.Trigger className="group flex w-full items-center justify-between gap-6 py-5 text-left text-[16px] text-ink hover:text-ink-2">
                {x.q}
                <Plus size={18} strokeWidth={1.5} className="shrink-0 text-ink-3 transition-transform duration-300 group-data-[state=open]:rotate-45" />
              </A.Trigger>
            </A.Header>
            <A.Content className="overflow-hidden data-[state=closed]:animate-[acc-up_220ms_ease-out] data-[state=open]:animate-[acc-down_260ms_cubic-bezier(0.16,1,0.3,1)]">
              <div className="max-w-[62ch] pb-5 text-[14.5px] leading-relaxed text-ink-2">{x.a}</div>
            </A.Content>
          </A.Item>
        ))}
      </A.Root>
    </div>
  );
}

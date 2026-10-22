import { AudioLines, Captions, Clock3, Coins, Languages } from "lucide-react";
import { Reveal } from "@/components/layout/Section";

/**
 * What clipbench measures that other speech-to-text boards do not (docs/V2.md).
 */
const ITEMS = [
  {
    icon: AudioLines,
    title: "Accuracy on edited, noisy video",
    body: "Character or word error rate on real Shorts with music beds, sound effects, jump cuts and fast delivery, reported per content category and with or without music.",
  },
  {
    icon: Clock3,
    title: "Word timing",
    body: "Does each word light up when it is said? Median and p90 timing error per word against aligned references. A caption app lives or dies on this.",
  },
  {
    icon: Captions,
    title: "Hallucination over music and silence",
    body: "Words a model invents where nobody speaks, per minute of non-speech audio. Some models are known to write lyrics or sign-offs over music.",
  },
  {
    icon: Languages,
    title: "Non-English and code-switching",
    body: "Commercial APIs and open models on the same clips in English, Japanese, Korean, Spanish, Hindi and French, with the English spans inside other languages scored separately.",
  },
  {
    icon: Coins,
    title: "Cost and speed",
    body: "List price per hour of audio and wall-clock latency per clip, next to the error rate, with paired tests so a lead only counts when it is real.",
  },
];

export function Measures() {
  return (
    <div className="grid gap-px overflow-hidden rounded-md border border-line bg-line sm:grid-cols-2 lg:grid-cols-3">
      {ITEMS.map((it, i) => (
        <Reveal key={it.title} delay={(i % 3) * 0.05} y={8} className="bg-surface">
          <article className="flex h-full flex-col p-5 sm:p-6">
            <div className="flex items-center">
              <span className="grid size-9 place-items-center rounded-sm border border-line bg-sunken text-ink-2">
                <it.icon size={17} strokeWidth={1.6} aria-hidden />
              </span>
            </div>
            <h3 className="mt-5 text-[16px] font-medium text-ink">{it.title}</h3>
            <p className="mt-2 text-[13.5px] leading-relaxed text-ink-2">{it.body}</p>
          </article>
        </Reveal>
      ))}
      <div className="flex flex-col justify-between bg-sunken/50 p-5 sm:p-6">
        <p className="text-[13.5px] leading-relaxed text-ink-2">
          Built for teams choosing a vendor for captions, clipping, dubbing and editing apps, who today pick on English call-centre numbers.
        </p>
        <p className="label mt-6">Same clips, same code path, every model</p>
      </div>
    </div>
  );
}

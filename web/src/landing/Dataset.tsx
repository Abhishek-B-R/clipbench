import { ArrowUpRight } from "lucide-react";
import { CATEGORIES, GITHUB_URL, RULES } from "@/lib/config";
import { int, pct } from "@/lib/format";
import { Figure } from "@/components/ui/Card";

/** The dataset spec (docs/V2.md, "Dataset spec"): source-agnostic, any public short-form video id. */
const COLUMNS: [string, string][] = [
  ["platform", "youtube, tiktok or instagram"],
  ["video_id", "the public id on that platform"],
  ["start_ms, end_ms", "a window inside a longer video; blank for the whole clip"],
  ["channel_id", "for the diversity rules"],
  ["category", "one of the content categories below"],
  ["license_note", "how the clip may be used"],
  ["source", "which importer added it"],
];

export function Dataset() {
  const rules = [
    { k: "Channels per language", v: `at least ${int(RULES.minChannels)}` },
    { k: "Largest channel", v: `no more than ${pct(RULES.maxChannelShare, 0)} of the set` },
    { k: "Content categories", v: `at least ${int(RULES.minCategories)}` },
    { k: "Verified clips to publish", v: `at least ${int(RULES.publishMinVerified)}` },
    { k: "Providers to publish", v: `at least ${int(RULES.publishMinProviders)}` },
  ];
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] lg:gap-8">
      <Figure n="DS" title="datasets/<lang>/clips.csv" caption="One row per clip. Ids only: anyone can rebuild the set, and no audio or caption text is ever published." source="source: docs/V2.md, Dataset spec" bodyClassName="p-0 sm:p-0">
        <dl>
          {COLUMNS.map(([k, v]) => (
            <div key={k} className="grid grid-cols-[minmax(0,150px)_minmax(0,1fr)] gap-4 border-b border-line px-4 py-2.5 last:border-b-0 sm:px-5">
              <dt className="font-mono text-[12.5px] text-ink">{k}</dt>
              <dd className="text-[13.5px] text-ink-2">{v}</dd>
            </div>
          ))}
        </dl>
      </Figure>
      <div className="grid content-start gap-6">
        <Figure n="DR" title="Diversity rules, checked in code" source="source: clipbench data check" bodyClassName="p-0 sm:p-0">
          <dl>
            {rules.map((r) => (
              <div key={r.k} className="flex items-baseline justify-between gap-4 border-b border-line px-4 py-2.5 last:border-b-0 sm:px-5">
                <dt className="text-[13.5px] text-ink-2">{r.k}</dt>
                <dd className="num text-right text-[13px] text-ink">{r.v}</dd>
              </div>
            ))}
          </dl>
        </Figure>
        <div>
          <div className="label mb-2.5">Content categories</div>
          <ul className="flex flex-wrap gap-1.5">
            {Object.values(CATEGORIES).map((c) => (
              <li key={c} className="inline-flex h-8 items-center rounded-full border border-line-strong px-3 text-[13px] text-ink-2">
                {c}
              </li>
            ))}
          </ul>
          <a href={`${GITHUB_URL}/blob/main/docs/V2.md`} target="_blank" rel="noreferrer" className="mt-5 inline-flex items-center gap-1 text-[13.5px] text-ink-2 underline decoration-line-strong underline-offset-4 hover:text-ink">
            Full dataset spec <ArrowUpRight size={13} />
          </a>
        </div>
      </div>
    </div>
  );
}

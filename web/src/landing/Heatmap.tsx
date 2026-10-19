import { useState } from "react";
import { extent } from "@/lib/scale";
import type { Run } from "@/lib/types";
import { TAG_LABEL, overall, primaryMetric, providerColor, providerLabel, tagsInRun, defaultTier } from "@/lib/analysis";
import { Figure } from "@/components/ui/Card";
import { Segmented } from "@/components/ui/Segmented";
import { HeatCell, HeatLegend } from "@/components/ui/HeatCell";
import { SeriesDot } from "@/components/ui/Badge";

const BUCKETS = ["<15s", "15-30s", "30-60s", "60-90s"];

/** Model x clip type matrix (Profound "Matrix View"). A real table: values printed, lowest per column ringed. */
export function Heatmap({ run }: { run: Run }) {
  const [mode, setMode] = useState<"tag" | "duration">("tag");
  const metric = primaryMetric(run).name;
  const providers = overall(run, defaultTier(run), metric).map((a) => a.provider);
  const cols = mode === "tag" ? tagsInRun(run) : BUCKETS.filter((b) => run.aggregates.some((a) => a.slice_kind === "duration" && a.slice === b));
  const get = (p: string, c: string) =>
    run.aggregates.find((a) => a.provider === p && a.tier === defaultTier(run) && a.metric === metric && a.slice_kind === mode && a.slice === c);
  const all = (p: string) => run.aggregates.find((a) => a.provider === p && a.tier === defaultTier(run) && a.metric === metric && a.slice_kind === "all");
  const values = providers.flatMap((p) => cols.map((c) => get(p, c)).filter((a) => a && !a.too_small).map((a) => a!.error_rate));
  const domain = extent(values);
  const bestIn = (c: string) => {
    const xs = providers.map((p) => get(p, c)).filter((a) => a && !a.too_small);
    return xs.sort((a, b) => a!.error_rate - b!.error_rate)[0]?.provider;
  };

  return (
    <Figure
      n={3}
      title="Every model on every kind of clip"
      caption="Character error rate per slice. More colour means more errors. The ring marks the lowest value in each column, not a tested win."
      controls={
        <Segmented
          label="Slice by"
          value={mode}
          onChange={setMode}
          options={[
            { value: "tag", label: "Clip type" },
            { value: "duration", label: "Duration" },
          ]}
        />
      }
      source={
        <span className="flex flex-wrap items-center justify-between gap-3">
          <span>source: aggregates, tier={defaultTier(run)}, metric={metric}, slice_kind={mode}</span>
          <HeatLegend domain={values.length ? domain : null} />
        </span>
      }
    >
      <div className="-mx-1 overflow-x-auto">
        <table className="w-full min-w-[640px] border-separate border-spacing-0">
          <caption className="sr-only">Error rate by model and {mode === "tag" ? "clip type" : "duration"}</caption>
          <thead>
            <tr>
              <th scope="col" className="label px-2 pb-2 text-left font-normal">Model</th>
              <th scope="col" className="label px-1 pb-2 text-center font-normal">All clips</th>
              {cols.map((c) => (
                <th key={c} scope="col" className="label px-1 pb-2 text-center font-normal">
                  {mode === "tag" ? TAG_LABEL[c] : c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {providers.map((p) => {
              const base = all(p);
              return (
                <tr key={p}>
                  <th scope="row" className="pr-3 pl-2 text-left font-normal whitespace-nowrap">
                    <span className="flex items-center gap-2 text-[13px] text-ink">
                      <SeriesDot color={providerColor(run, p)} />
                      {providerLabel(run, p)}
                    </span>
                  </th>
                  <td className="p-0.5">
                    <div className="grid h-11 place-items-center rounded-xs border border-line bg-surface">
                      <span className="num text-[12.5px] text-ink">{base ? `${(base.error_rate * 100).toFixed(1)}%` : "n/a"}</span>
                    </div>
                  </td>
                  {cols.map((c) => {
                    const a = get(p, c);
                    return (
                      <HeatCell
                        key={c}
                        value={a?.error_rate ?? null}
                        n={a?.n_clips}
                        tooSmall={a?.too_small}
                        best={bestIn(c) === p}
                        domain={domain}
                        label={`${providerLabel(run, p)}, ${mode === "tag" ? TAG_LABEL[c] : c}`}
                      />
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Figure>
  );
}

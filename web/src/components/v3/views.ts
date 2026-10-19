import type { Run } from "@/lib/types";
import { v3 } from "@/lib/v3";

/**
 * Leaderboard views beyond accuracy (v3). Listed here, without the chart code, so the page can show the tabs
 * before the lazy chunk with the views loads. A view appears only when the run carries its section.
 */
export type LeaderView = "accuracy" | "formatting" | "captions" | "speakers" | "lid" | "entities" | "style" | "sweeps" | "longform" | "speed";

export const VIEW_LABEL: Record<LeaderView, string> = {
  accuracy: "Accuracy",
  sweeps: "Noise sweep",
  formatting: "Formatting",
  captions: "Caption files",
  speakers: "Speakers",
  lid: "Language ID",
  entities: "Names and brands",
  style: "Style",
  longform: "Long-form",
  speed: "Speed and cost",
};

export function viewsIn(run: Run): LeaderView[] {
  const x = v3(run);
  const has: Record<LeaderView, boolean> = {
    accuracy: true,
    sweeps: Boolean(x.sweeps?.points.length),
    formatting: Boolean(x.formatting?.length),
    captions: Boolean(x.captions?.rows.length),
    speakers: Boolean(x.diarization?.rows.length),
    lid: Boolean(x.lid?.rows.length),
    entities: Boolean(x.entities?.rows.length),
    style: Boolean(x.style?.length),
    longform: Boolean(x.longform),
    // Price and speed come from the core export, so this view is always available.
    speed: run.providers.length > 0,
  };
  return (Object.keys(VIEW_LABEL) as LeaderView[]).filter((k) => has[k]);
}

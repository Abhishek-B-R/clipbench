import { Info } from "lucide-react";
import type { Run, Tier } from "@/lib/types";
import { tiersInRun } from "@/lib/analysis";
import { cn } from "@/lib/cn";
import { Segmented } from "./Segmented";

/**
 * Reference-tier switch. Shows the Segmented control only when the run has both tiers; an early run with only
 * creator captions gets a plain label instead of a switch with a dead option.
 */
export function TierControl({ run, value, onChange }: { run?: Run; value: Tier; onChange: (t: Tier) => void }) {
  const tiers = run ? tiersInRun(run) : (["verified", "creator"] as Tier[]);
  if (tiers.length === 1)
    return (
      <span className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-line bg-sunken px-2.5 text-[12.5px] text-ink-2">
        <Info size={13} className="text-ink-3" aria-hidden />
        {tiers[0] === "creator" ? "Creator captions only. No verified clips in this run yet." : "Verified clips only"}
      </span>
    );
  return (
    <Segmented
      label="Reference tier"
      value={value}
      onChange={onChange}
      options={[
        { value: "verified", label: "Verified" },
        { value: "creator", label: "Creator captions" },
      ]}
    />
  );
}

/** The "unverified reference" note every creator-tier view carries. */
export function TierNote({ tier, className }: { tier: Tier; className?: string }) {
  if (tier !== "creator") return null;
  return (
    <p className={cn("rounded-md border border-line bg-warn-soft/60 px-4 py-2.5 text-[13px] text-ink-2", className)}>
      <span className="font-medium text-ink">Unverified reference.</span> Scored against the creators' own captions, which are often not verbatim. Read as agreement with the creator, not accuracy.
    </p>
  );
}

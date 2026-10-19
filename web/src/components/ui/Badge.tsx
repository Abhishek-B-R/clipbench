import type { HTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export type BadgeTone = "neutral" | "signal" | "good" | "warn" | "ins" | "outline";

const tones: Record<BadgeTone, string> = {
  neutral: "bg-sunken text-ink-2 border-line",
  signal: "bg-signal-soft text-signal-ink border-transparent",
  good: "bg-good-soft text-good border-transparent",
  warn: "bg-warn-soft text-ink-2 border-transparent",
  ins: "bg-ins-soft text-ins border-transparent",
  outline: "bg-transparent text-ink-2 border-line-strong",
};

/** Small label. Mono caps by default (`mono={false}` for sentence case). Never carries meaning by colour alone. */
export function Badge({ tone = "neutral", mono = true, className, ...props }: HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone; mono?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center gap-1 rounded-xs border px-1.5 whitespace-nowrap",
        mono ? "font-mono text-[10.5px] tracking-[0.05em] uppercase" : "text-[12px] font-medium",
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}

/** Coloured dot that identifies a provider. Always next to the provider's name. */
export function SeriesDot({ color, className, size = 8 }: { color: string; className?: string; size?: number }) {
  return <span aria-hidden className={cn("inline-block shrink-0 rounded-full", className)} style={{ background: color, width: size, height: size }} />;
}

import { cn } from "@/lib/cn";

/**
 * The mark: five waveform bars, one of them in signal vermilion (the error we are looking for).
 * The wordmark is lowercase Mona Sans, semibold, slightly condensed.
 */
export function LogoMark({ size = 18, className }: { size?: number; className?: string }) {
  const bars = [0.42, 0.78, 1, 0.62, 0.34];
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden className={className}>
      {bars.map((h, i) => {
        const height = 16 * h;
        return (
          <rect
            key={i}
            x={1.5 + i * 3.6}
            y={10 - height / 2}
            width={2.2}
            height={height}
            rx={1.1}
            fill={i === 3 ? "var(--signal)" : "currentColor"}
          />
        );
      })}
    </svg>
  );
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2 text-ink", className)}>
      <LogoMark />
      <span className="text-[16px] font-semibold tracking-[-0.02em]" style={{ fontStretch: "92%" }}>
        clipbench
      </span>
    </span>
  );
}

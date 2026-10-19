import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Check } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * Toggle chip for multi-select filters (clip types, models). aria-pressed carries the state; a check mark and
 * an ink outline show it, so colour is never the only cue. `dot` puts a SeriesDot (or any node) first.
 */
export function Chip({
  pressed,
  dot,
  children,
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { pressed: boolean; dot?: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      className={cn(
        "inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[13px] whitespace-nowrap transition-[background-color,border-color,color] duration-150",
        "disabled:cursor-not-allowed disabled:opacity-40",
        pressed ? "border-ink bg-surface text-ink shadow-card" : "border-line-strong bg-transparent text-ink-2 hover:border-ink-3 hover:text-ink",
        className,
      )}
      {...props}
    >
      {pressed ? <Check size={13} strokeWidth={2} className="-ml-0.5 text-ink" aria-hidden /> : dot}
      {pressed && dot}
      {children}
    </button>
  );
}

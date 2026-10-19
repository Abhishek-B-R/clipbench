import type { HTMLAttributes } from "react";
import { cn } from "@/lib/cn";

/** Keyboard key. `<Kbd>⌘</Kbd><Kbd>K</Kbd>` */
export function Kbd({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return (
    <kbd
      className={cn(
        "inline-grid h-[18px] min-w-[18px] place-items-center rounded-[4px] border border-line-strong bg-surface px-1",
        "font-mono text-[10.5px] leading-none font-medium text-ink-2 shadow-[0_1px_0_var(--line-strong)]",
        className,
      )}
      {...props}
    />
  );
}

export const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
export const modKey = isMac ? "⌘" : "Ctrl";

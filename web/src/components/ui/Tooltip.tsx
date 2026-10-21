import * as T from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

const DELAY = { delayDuration: 150, skipDelayDuration: 300 };

/**
 * Optional wrapper for a subtree of tooltips. Each Tooltip also carries its own provider (below), so the Radix popper
 * code loads only with pages that show a tooltip instead of on every first visit. Short delay so dense tables feel responsive.
 */
export function TooltipProvider({ children }: { children: ReactNode }) {
  return <T.Provider {...DELAY}>{children}</T.Provider>;
}

/** Text tooltip on hover and keyboard focus. The trigger must be focusable (a button or link). */
export function Tooltip({ content, children, side = "top", className }: { content: ReactNode; children: ReactNode; side?: "top" | "bottom" | "left" | "right"; className?: string }) {
  return (
    <T.Provider {...DELAY}>
      <T.Root>
        <T.Trigger asChild>{children}</T.Trigger>
        <T.Portal>
          <T.Content
            side={side}
            sideOffset={6}
            collisionPadding={12}
            className={cn(
              "z-50 max-w-72 rounded-sm border border-line bg-surface px-2.5 py-1.5 text-[12.5px] leading-snug text-ink shadow-pop",
              "data-[state=delayed-open]:animate-fade-up",
              className,
            )}
          >
            {content}
          </T.Content>
        </T.Portal>
      </T.Root>
    </T.Provider>
  );
}

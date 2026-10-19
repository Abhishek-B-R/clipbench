import type { ReactNode } from "react";
import { AlertTriangle, RotateCw } from "lucide-react";
import { cn } from "@/lib/cn";
import { Button } from "./Button";

/**
 * Empty state: a dashed well with a small icon, one plain sentence, and an optional action.
 * Used inside figures and tabs when there is nothing to show yet.
 */
export function EmptyState({ icon, title, body, action, className }: { icon?: ReactNode; title: string; body?: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <div className={cn("grid justify-items-start gap-2 rounded-md border border-dashed border-line-strong bg-sunken/40 px-5 py-8 sm:px-6", className)}>
      {icon && <span className="mb-1 grid size-9 place-items-center rounded-sm border border-line bg-surface text-ink-3">{icon}</span>}
      <p className="text-[14.5px] font-medium text-ink">{title}</p>
      {body && <p className="max-w-[56ch] text-[13.5px] text-ink-2">{body}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/** Error state for a failed load (API down, missing data): role=alert, one sentence, a retry when possible. */
export function ErrorState({ title = "Could not load this.", body, onRetry, className }: { title?: string; body?: ReactNode; onRetry?: () => void; className?: string }) {
  return (
    <div role="alert" className={cn("flex flex-wrap items-start gap-3 rounded-md border border-line bg-surface px-4 py-4 sm:px-5", className)}>
      <AlertTriangle size={16} strokeWidth={1.75} className="mt-0.5 shrink-0 text-signal-ink" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-[14px] font-medium text-ink">{title}</p>
        {body && <p className="mt-0.5 text-[13px] text-ink-2">{body}</p>}
      </div>
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          <RotateCw size={13} /> Try again
        </Button>
      )}
    </div>
  );
}

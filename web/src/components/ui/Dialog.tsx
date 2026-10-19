import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * Modal dialog (Radix): focus trap, Esc to close, returns focus to the trigger, aria-labelledby wired.
 * Usage: <Dialog open onOpenChange title="..." description="...">body</Dialog>
 */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  className,
  trigger,
}: {
  open?: boolean;
  onOpenChange?: (o: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  className?: string;
  trigger?: ReactNode;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      {trigger && <D.Trigger asChild>{trigger}</D.Trigger>}
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-50 bg-[var(--scrim)] backdrop-blur-[2px] data-[state=open]:animate-[fade-up_200ms_ease-out]" />
        <D.Content
          className={cn(
            "fixed top-[12vh] left-1/2 z-50 w-[calc(100vw-32px)] max-w-md -translate-x-1/2 rounded-md border border-line bg-surface shadow-pop",
            "data-[state=open]:animate-fade-up focus:outline-none",
            className,
          )}
        >
          <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
            <div>
              <D.Title className="text-[15px] font-semibold text-ink">{title}</D.Title>
              {description && <D.Description className="mt-1 text-[13px] text-ink-2">{description}</D.Description>}
            </div>
            <D.Close className="-mr-1 grid size-7 place-items-center rounded-sm text-ink-3 hover:bg-sunken hover:text-ink" aria-label="Close">
              <X size={16} />
            </D.Close>
          </div>
          <div className="px-5 py-4">{children}</div>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}

export const DialogClose = D.Close;

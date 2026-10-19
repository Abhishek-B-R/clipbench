import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/cn";
import { toast } from "@/components/ui/Toast";

/** Inline command pill with a `$` prompt and a copy button (Vercel pattern). */
export function CopyCommand({ command, display, className }: { command: string; display?: string; className?: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setDone(true);
      toast("Copied to clipboard");
      window.setTimeout(() => setDone(false), 1600);
    } catch {
      toast.error("Could not copy. Select the text instead.");
    }
  };
  return (
    <div className={cn("inline-flex h-11 max-w-full items-center gap-3 rounded-md border border-line bg-surface pr-1.5 pl-3.5 font-mono text-[13px] text-ink-2", className)}>
      <span aria-hidden className="text-ink-3 select-none">$</span>
      <code className="truncate text-ink">{display ?? command}</code>
      <button
        type="button"
        onClick={copy}
        aria-label="Copy command"
        className="grid size-8 shrink-0 place-items-center rounded-sm text-ink-3 transition-colors hover:bg-sunken hover:text-ink"
      >
        {done ? <Check size={15} className="text-good" /> : <Copy size={15} />}
      </button>
    </div>
  );
}

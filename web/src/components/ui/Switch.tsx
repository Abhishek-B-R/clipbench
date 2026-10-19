import { cn } from "@/lib/cn";

/** On/off switch. role="switch", Space/Enter toggle (native button). Label lives next to it via aria-labelledby or children. */
export function Switch({
  checked,
  onChange,
  disabled,
  id,
  labelledBy,
  className,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  id?: string;
  labelledBy?: string;
  className?: string;
}) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-labelledby={labelledBy}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-[22px] w-[38px] shrink-0 items-center rounded-full border transition-colors duration-150 disabled:opacity-45",
        checked ? "border-ink bg-ink" : "border-line-strong bg-sunken hover:border-ink-3",
        className,
      )}
    >
      <span
        className={cn(
          "absolute top-1/2 size-4 -translate-y-1/2 rounded-full shadow-card transition-[left,background-color] duration-200 ease-[cubic-bezier(0.16,1,0.3,1)]",
          checked ? "left-[18px] bg-paper" : "left-[2px] bg-surface ring-1 ring-line-strong",
        )}
      />
    </button>
  );
}

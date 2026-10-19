import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from "react";
import { m } from "motion/react";
import { cn } from "@/lib/cn";

/**
 * Underline tabs (page-level sections such as Account). role="tablist", arrow keys move and select,
 * the active underline slides. Render the panel yourself with `tabPanelProps(id, value)`.
 */
export function Tabs<T extends string>({
  value,
  onChange,
  tabs,
  label,
  className,
}: {
  value: T;
  onChange: (v: T) => void;
  tabs: { value: T; label: ReactNode; count?: number }[];
  label: string;
  className?: string;
}) {
  const id = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const list = useRef<HTMLDivElement>(null);
  // Keep the active tab visible when the strip scrolls sideways (phones).
  useEffect(() => {
    const el = refs.current[tabs.findIndex((t) => t.value === value)];
    const box = list.current;
    if (el && box && (el.offsetLeft < box.scrollLeft || el.offsetLeft + el.offsetWidth > box.scrollLeft + box.clientWidth)) box.scrollTo({ left: el.offsetLeft - 8 });
  }, [value, tabs]);
  const onKey = (e: KeyboardEvent) => {
    const i = tabs.findIndex((t) => t.value === value);
    let j = i;
    if (e.key === "ArrowRight") j = (i + 1) % tabs.length;
    else if (e.key === "ArrowLeft") j = (i - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") j = 0;
    else if (e.key === "End") j = tabs.length - 1;
    else return;
    e.preventDefault();
    onChange(tabs[j]!.value);
    refs.current[j]?.focus();
  };
  return (
    <div ref={list} role="tablist" aria-label={label} onKeyDown={onKey} className={cn("scrollbar-none -mb-px flex gap-1 overflow-x-auto", className)}>
      {tabs.map((t, i) => {
        const on = t.value === value;
        return (
          <button
            key={t.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            id={`tab-${t.value}`}
            role="tab"
            type="button"
            aria-selected={on}
            aria-controls={`panel-${t.value}`}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(t.value)}
            className={cn(
              "relative inline-flex h-11 shrink-0 items-center gap-2 px-2.5 text-[13.5px] whitespace-nowrap transition-colors",
              on ? "text-ink" : "text-ink-3 hover:text-ink-2",
            )}
          >
            {t.label}
            {t.count != null && <span className="num rounded-xs bg-sunken px-1.5 text-[11px] text-ink-3">{t.count}</span>}
            {on && <m.span layoutId={`tabs-${id}`} transition={{ type: "spring", stiffness: 500, damping: 40 }} className="absolute right-2 bottom-0 left-2 h-[2px] rounded-full bg-ink" />}
          </button>
        );
      })}
    </div>
  );
}

export function tabPanelProps(value: string) {
  return { id: `panel-${value}`, role: "tabpanel" as const, "aria-labelledby": `tab-${value}`, tabIndex: 0 };
}

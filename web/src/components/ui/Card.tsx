import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

/** Plain raised surface with a hairline. Use for grouped content on dashboard pages. */
export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-md border border-line bg-surface shadow-card", className)} {...props} />;
}

/**
 * A numbered figure: the core unit of every chart and table on the site.
 * Header: mono `FIG. 02` + title, optional controls on the right. Footer: source line in mono.
 * Pattern from Intercom (Fin) figure captions; see docs/design/research.md.
 */
export function Figure({
  n,
  title,
  caption,
  controls,
  source,
  children,
  className,
  bodyClassName,
  corners = false,
}: {
  n?: string | number;
  title: ReactNode;
  caption?: ReactNode;
  controls?: ReactNode;
  source?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  corners?: boolean;
}) {
  return (
    <figure className={cn("relative min-w-0 rounded-md border border-line bg-surface shadow-card", className)}>
      {corners && <Corners />}
      <figcaption className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3 sm:px-5">
        <div className="min-w-0">
          <div className="label flex items-baseline gap-2">
            {n != null && <span className="shrink-0 whitespace-nowrap text-ink-2">FIG. {String(n).padStart(2, "0")}</span>}
            {n != null && <span aria-hidden className="h-px w-3 shrink-0 translate-y-[-3px] bg-line-strong" />}
            <span className="min-w-0">{title}</span>
          </div>
          {caption && <p className="mt-1 text-[13px] text-ink-2">{caption}</p>}
        </div>
        {controls && <div className="flex shrink-0 flex-wrap items-center gap-2">{controls}</div>}
      </figcaption>
      {/* cn() does not merge Tailwind classes, so a given bodyClassName replaces the default padding instead of fighting it. */}
      <div className={bodyClassName ?? "p-4 sm:p-5"}>{children}</div>
      {source && <div className="border-t border-line px-4 py-2.5 font-mono text-[11px] text-ink-3 sm:px-5">{source}</div>}
    </figure>
  );
}

/** Four small corner ticks (Spade, Heron). Purely decorative. */
export function Corners({ className }: { className?: string }) {
  const c = "pointer-events-none absolute size-2 border-ink-3/70";
  return (
    <span aria-hidden className={className}>
      <span className={cn(c, "-top-px -left-px border-t border-l rounded-tl-md")} />
      <span className={cn(c, "-top-px -right-px border-t border-r rounded-tr-md")} />
      <span className={cn(c, "-bottom-px -left-px border-b border-l rounded-bl-md")} />
      <span className={cn(c, "-right-px -bottom-px border-r border-b rounded-br-md")} />
    </span>
  );
}

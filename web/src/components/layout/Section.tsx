import type { ReactNode } from "react";
import { m, useReducedMotion } from "motion/react";
import { cn } from "@/lib/cn";

/**
 * A landing-page section inside the framed grid. Starts with a full-width hairline that carries small
 * crosshair marks where it meets the frame edges, then a mono index ("02 / By clip type").
 */
export function Section({
  id,
  index,
  kicker,
  title,
  intro,
  aside,
  children,
  className,
}: {
  id?: string;
  index?: string;
  kicker?: string;
  title?: ReactNode;
  intro?: ReactNode;
  aside?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <section id={id} aria-labelledby={id ? `${id}-title` : undefined} className={cn("relative", className)}>
      <Rule />
      <div className="frame frame-lines pt-14 pb-16 sm:pt-20 sm:pb-24">
        {(index || kicker) && (
          <div className="label mb-5 flex items-center gap-2">
            {index && <span className="text-signal-ink">{index}</span>}
            {index && kicker && <span aria-hidden>/</span>}
            {kicker && <span>{kicker}</span>}
          </div>
        )}
        {(title || aside) && (
          <div className="mb-10 grid gap-6 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] md:items-end">
            <div>
              {title && (
                <Reveal>
                  <h2 id={id ? `${id}-title` : undefined} className="display max-w-[22ch] text-[34px] sm:text-[44px]">
                    {title}
                  </h2>
                </Reveal>
              )}
              {intro && (
                <Reveal delay={0.06}>
                  <p className="mt-4 max-w-[58ch] text-[15.5px] text-ink-2">{intro}</p>
                </Reveal>
              )}
            </div>
            {aside && <div className="md:justify-self-end">{aside}</div>}
          </div>
        )}
        {children}
      </div>
    </section>
  );
}

/** Full-width hairline with crosshairs at the frame edges (desktop). */
export function Rule() {
  return (
    <div aria-hidden className="relative h-px bg-line">
      <div className="frame relative hidden h-px xl:block">
        <Cross className="-left-[5px]" />
        <Cross className="-right-[5px]" />
      </div>
    </div>
  );
}

function Cross({ className }: { className?: string }) {
  return (
    <span className={cn("absolute -top-[5px] size-[11px]", className)} style={{ left: undefined }}>
      <span className="absolute top-[5px] left-0 h-px w-[11px] bg-ink-3" />
      <span className="absolute top-0 left-[5px] h-[11px] w-px bg-ink-3" />
    </span>
  );
}

/** Fade and rise once when scrolled into view. Instant for reduced-motion users. */
export function Reveal({ children, delay = 0, className, y = 12 }: { children: ReactNode; delay?: number; className?: string; y?: number }) {
  const reduce = useReducedMotion();
  if (reduce) return <div className={className}>{children}</div>;
  return (
    <m.div
      className={className}
      initial={{ opacity: 0, y }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "0px 0px -10% 0px" }}
      transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1], delay }}
    >
      {children}
    </m.div>
  );
}

import { useEffect, type ReactNode } from "react";

/**
 * Header for dashboard pages. Calm on purpose: sans h1 (the display serif is for the landing page and a few
 * big moments), a one-line description, actions on the right, and an optional row of filters below.
 * Sets document.title.
 */
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  children,
}: {
  eyebrow?: ReactNode;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  useEffect(() => {
    document.title = `${title} | clipbench`;
  }, [title]);
  return (
    <div className="border-b border-line">
      <div className="frame pt-10 pb-6 sm:pt-14">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0">
            {eyebrow && <div className="label mb-3">{eyebrow}</div>}
            <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.025em] text-ink sm:text-[34px]" style={{ fontStretch: "94%" }}>
              {title}
            </h1>
            {description && <p className="mt-2 max-w-[68ch] text-[15px] text-ink-2">{description}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
        {children && <div className="mt-6 flex flex-wrap items-center gap-2">{children}</div>}
      </div>
    </div>
  );
}

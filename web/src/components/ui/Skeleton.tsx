import type { HTMLAttributes } from "react";
import { cn } from "@/lib/cn";

/** Loading placeholder with a slow scan highlight. Size it like the content it replaces. */
export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div aria-hidden className={cn("relative overflow-hidden rounded-sm bg-sunken", className)} {...props}>
      <div className="absolute inset-0 animate-scan bg-gradient-to-r from-transparent via-[var(--line)] to-transparent opacity-60" />
    </div>
  );
}

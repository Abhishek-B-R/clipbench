import { useState } from "react";
import { cn } from "@/lib/cn";

/** Round avatar: image when it loads, otherwise initials on a neutral disc. */
export function Avatar({ src, name, size = 28, className }: { src?: string | null; name?: string | null; size?: number; className?: string }) {
  const [failed, setFailed] = useState(false);
  const initials = (name ?? "?")
    .split(/\s+/)
    .map((s) => s[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
  return (
    <span
      className={cn("inline-grid shrink-0 place-items-center overflow-hidden rounded-full border border-line bg-sunken font-mono text-ink-2", className)}
      style={{ width: size, height: size, fontSize: Math.max(10, size * 0.38) }}
    >
      {src && !failed ? <img src={src} alt="" className="size-full object-cover" onError={() => setFailed(true)} /> : <span aria-hidden>{initials}</span>}
      <span className="sr-only">{name}</span>
    </span>
  );
}

import { useEffect, useState } from "react";

/** The last of `ids` whose element has scrolled past `offset` px from the top. For sticky tables of contents. */
export function useActiveHeading(ids: string[], offset = 140) {
  const [active, setActive] = useState<string | null>(ids[0] ?? null);
  useEffect(() => {
    const on = () => {
      let cur = ids[0] ?? null;
      for (const id of ids) {
        const el = document.getElementById(id);
        if (el && el.getBoundingClientRect().top < offset) cur = id;
      }
      setActive(cur);
    };
    on();
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, [ids.join(), offset]); // eslint-disable-line react-hooks/exhaustive-deps
  return active;
}

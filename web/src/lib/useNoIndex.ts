import { useEffect } from "react";

/**
 * Adds `<meta name="robots" content="noindex">` while `active`. The Worker answers unknown paths with the SPA
 * (status 200, `not_found_handling: single-page-application`), so pages that render "not found" say so to crawlers here.
 */
export function useNoIndex(active = true) {
  useEffect(() => {
    if (!active) return;
    const robots = document.createElement("meta");
    robots.name = "robots";
    robots.content = "noindex";
    document.head.appendChild(robots);
    return () => robots.remove();
  }, [active]);
}

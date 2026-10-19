import { useEffect } from "react";
import { Link, useLocation } from "react-router";
import { buttonClass } from "@/components/ui/Button";
import { Kbd, modKey } from "@/components/ui/Kbd";
import { setCommandMenuOpen } from "@/components/ui/commandMenuStore";

/** 404, told as a transcript diff: the page you asked for was dropped. */
export default function NotFound() {
  const { pathname } = useLocation();
  useEffect(() => {
    document.title = "Not found | clipbench";
    // The Worker answers unknown paths with the SPA (status 200), so tell crawlers not to index this one.
    const robots = document.createElement("meta");
    robots.name = "robots";
    robots.content = "noindex";
    document.head.appendChild(robots);
    return () => robots.remove();
  }, []);
  return (
    <div className="frame frame-lines grid min-h-[60vh] content-center py-20">
      <div className="label mb-6">Error 404 / CER 100%</div>
      <div className="grid gap-2">
        <div className="label">You asked for</div>
        <p className="font-mono text-[15px] break-all text-ink-3 line-through decoration-signal decoration-2">{pathname}</p>
        <div className="label mt-4">We heard</div>
        <h1 className="display text-[44px] text-ink sm:text-[72px]">
          <span className="rounded-[6px] bg-signal-soft px-2 text-signal-ink">nothing at all.</span>
        </h1>
      </div>
      <p className="mt-6 max-w-[46ch] text-[15px] text-ink-2">This page does not exist, or it moved. The results are one click away.</p>
      <div className="mt-8 flex flex-wrap items-center gap-3">
        <Link to="/" className={buttonClass("primary", "lg")}>
          Back to the overview
        </Link>
        <Link to="/leaderboard" className={buttonClass("secondary", "lg")}>
          Leaderboard
        </Link>
        <button type="button" onClick={() => setCommandMenuOpen(true)} className="ml-1 inline-flex items-center gap-2 text-[13px] text-ink-3 hover:text-ink">
          or search <Kbd>{modKey}</Kbd>
          <Kbd>K</Kbd>
        </button>
      </div>
    </div>
  );
}

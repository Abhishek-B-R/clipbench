import { useEffect, useState } from "react";
import { Link } from "react-router";
import { ArrowRight, BellRing, ChevronUp, KeyRound, Star } from "lucide-react";
import { buttonClass } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";

type Req = { id: string; kind: "provider" | "language"; title: string; votes: number };

/** Top requests from the Worker (GET /api/requests). Returns null when the API is unavailable. */
function useTopRequests() {
  const [rows, setRows] = useState<Req[] | null>(null);
  useEffect(() => {
    let alive = true;
    fetch("/api/requests", { headers: { accept: "application/json" } })
      .then(async (r) => {
        if (!r.ok || !(r.headers.get("content-type") ?? "").includes("json")) throw new Error(String(r.status));
        const j = (await r.json()) as { requests?: Req[] } | Req[];
        const list = Array.isArray(j) ? j : (j.requests ?? []);
        if (alive) setRows(list.slice(0, 4));
      })
      .catch(() => alive && setRows(null));
    return () => {
      alive = false;
    };
  }, []);
  return rows;
}

/**
 * Closing band: request a model or language, and what an account gets you. Shows the live vote board when
 * the API answers; otherwise a plain list of account features. Never shows invented vote counts.
 */
export function RequestCTA() {
  const top = useTopRequests();
  return (
    <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,460px)] lg:items-center">
      <div>
        <h2 id="request-title" className="display max-w-[16ch] text-[40px] sm:text-[56px]">
          Missing a model, or a language?
        </h2>
        <p className="mt-5 max-w-[50ch] text-[16px] text-ink-2">
          The next run adds what people ask for most. Request a model or a language, vote on others, and get an email when a run changes the ranking.
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <Link to="/requests" className={buttonClass("primary", "lg", "group")}>
            Request a model
            <ArrowRight size={16} className="transition-transform group-hover:translate-x-0.5" />
          </Link>
          <Link to="/sign-in" className={buttonClass("secondary", "lg")}>
            Get run alerts
          </Link>
        </div>
      </div>
      <div className="relative rounded-lg border border-line bg-surface shadow-card">
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <span className="label">{top && top.length > 0 ? "Most requested, from the request board" : "With an account"}</span>
        </div>
        {top && top.length > 0 ? (
          <ul>
            {top.map((r) => (
              <li key={r.id} className="flex items-center gap-4 border-b border-line px-5 py-3 last:border-b-0">
                <span className="grid w-11 shrink-0 place-items-center rounded-sm border border-line py-1">
                  <ChevronUp size={14} className="text-ink-3" />
                  <span className="num text-[12.5px] text-ink">{r.votes}</span>
                </span>
                <span className="min-w-0 flex-1 truncate text-[14px] text-ink">{r.title}</span>
                <Badge tone="neutral">{r.kind}</Badge>
              </li>
            ))}
          </ul>
        ) : (
          <ul>
            {[
              { icon: Star, title: "Watchlist", body: "Follow the models you use and see them first." },
              { icon: BellRing, title: "Run alerts", body: "An email when a new run lands or a rank changes." },
              { icon: KeyRound, title: "API keys", body: "Higher rate limits on the public data API." },
            ].map((f) => (
              <li key={f.title} className="flex gap-4 border-b border-line px-5 py-4 last:border-b-0">
                <span className="grid size-8 shrink-0 place-items-center rounded-sm border border-line bg-sunken text-ink-2">
                  <f.icon size={15} strokeWidth={1.75} />
                </span>
                <span>
                  <span className="block text-[14px] font-medium text-ink">{f.title}</span>
                  <span className="block text-[13px] text-ink-2">{f.body}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

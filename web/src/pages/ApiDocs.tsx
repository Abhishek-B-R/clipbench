import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { ChevronDown } from "lucide-react";
import { useActiveHeading } from "@/lib/useActiveHeading";
import { useSession } from "@/lib/auth";
import { useRun, useSource } from "@/lib/data";
import { cn } from "@/lib/cn";
import { Badge } from "@/components/ui/Badge";
import { buttonClass } from "@/components/ui/Button";
import { CodeBlock, type CodeSample } from "@/components/ui/CodeBlock";
import { Rule } from "@/components/layout/Section";

const NAV = [
  { group: "Overview", items: [
    { id: "introduction", label: "Introduction" },
    { id: "authentication", label: "Authentication" },
    { id: "rate-limits", label: "Rate limits" },
    { id: "errors", label: "Errors" },
  ] },
  { group: "Endpoints", items: [
    { id: "list-runs", label: "List runs", method: "GET" },
    { id: "get-run", label: "Get a run", method: "GET" },
  ] },
  { group: "Objects", items: [
    { id: "run-object", label: "The run object" },
    { id: "aggregate-object", label: "Aggregates and paired" },
  ] },
];

/**
 * Data API reference in the Stripe / Resend shape: a sticky section nav, prose and field tables on the left,
 * a code panel with curl / JavaScript / Python tabs and the response on the right.
 */
export default function ApiDocs() {
  const origin = typeof window === "undefined" ? "https://clipbench.dev" : window.location.origin;
  const active = useActiveHeading(NAV.flatMap((g) => g.items.map((i) => i.id)), 160);
  const { data } = useSession();
  useEffect(() => {
    document.title = "Data API | clipbench";
  }, []);
  const live = useExamples();
  const ex = examples(origin, live.latest);
  return (
    <>
      <header className="frame frame-lines pt-12 pb-10 sm:pt-16">
        <div className="label mb-4 flex items-center gap-2">
          <span className="text-signal-ink">Docs</span> / API reference
        </div>
        <h1 className="text-[32px] leading-tight font-semibold tracking-[-0.025em] text-ink sm:text-[40px]" style={{ fontStretch: "94%" }}>
          Data API
        </h1>
        <p className="mt-3 max-w-[64ch] text-[16px] text-ink-2">Every published run as JSON: the same numbers the site shows, with intervals, paired tests and per-clip scores. Read-only, no account needed.</p>
        <div className="mt-6 inline-flex max-w-full items-center gap-3 rounded-md border border-line bg-surface py-2 pr-3 pl-3 font-mono text-[12.5px] shadow-card">
          <span className="text-ink-3">Base URL</span>
          <span className="truncate text-ink">{origin}/api/v1</span>
        </div>
      </header>
      <Rule />
      <div className="frame frame-lines grid gap-10 pt-10 pb-24 lg:grid-cols-[200px_minmax(0,1fr)] lg:gap-12">
        <SideNav active={active} />
        <div className="grid min-w-0 gap-0">
          <Section id="introduction" title="Introduction" code={<CodeBlock title="Quick start" samples={ex.quick} />}>
            <p>
              The API serves the files written by <Mono>clipbench export</Mono>: an index of runs and one JSON document per run. It is the source for every chart on this site, so anything you see here you can rebuild. Documents are cached for five minutes and carry an <Mono>ETag</Mono>.
            </p>
            <p>CORS is open to every origin for <Mono>GET</Mono>. No cookies are read. Caption text is never included beyond error snippets of a few characters; video ids are, so the clip set can be rebuilt.</p>
          </Section>
          <Section id="authentication" title="Authentication" code={<CodeBlock samples={ex.auth} />}>
            <p>
              Requests work without a key at a low rate limit. Send a key as a bearer token to raise it. Keys start with <Mono>cb_</Mono>, are shown once when created, and are stored only as a hash.
            </p>
            <p>
              {data?.user ? (
                <Link to="/account#keys" className={buttonClass("secondary", "sm")}>
                  Manage your keys
                </Link>
              ) : (
                <Link to="/sign-in?next=/account%23keys" className={buttonClass("secondary", "sm")}>
                  Sign in to create a key
                </Link>
              )}
            </p>
          </Section>
          <Section id="rate-limits" title="Rate limits" code={<CodeBlock samples={ex.limit} />}>
            <p>Limits are per minute. Every response carries <Mono>X-RateLimit-Limit</Mono>; going over returns <Mono>429</Mono> with <Mono>Retry-After: 60</Mono>.</p>
            <Fields
              head={["Caller", "Limit", "Counted per"]}
              rows={[
                ["Anonymous", "30 requests / min", "IP address"],
                ["With an API key", "600 requests / min", "key"],
              ]}
            />
            <p>A run document changes only when a new run is published, so cache it. The index tells you when there is something new.</p>
          </Section>
          <Section id="errors" title="Errors" code={<CodeBlock samples={ex.error} />}>
            <p>
              Errors are JSON with a stable <Mono>code</Mono> and a sentence you can show to a person.
            </p>
            <Fields
              head={["Status", "code", "When"]}
              rows={[
                ["400", "invalid_request", "A path parameter is malformed."],
                ["401", "invalid_api_key", "The Authorization header is not a known, unrevoked key."],
                ["404", "not_found", "No run with that id, or nothing published yet."],
                ["429", "rate_limited", "Too many requests this minute. Wait for Retry-After."],
              ]}
            />
          </Section>
          <Section id="list-runs" title="List runs" method="GET" path="/api/v1/runs" code={<CodeBlock samples={ex.list} />} response={<CodeBlock title="Response, live" samples={[{ label: "200", lang: "json", code: live.list }]} />}>
            <p>
              The run index: which run is latest and a summary of every published run, newest first. The example on the right is the live index, trimmed.
            </p>
            <Fields
              head={["Field", "Type", "Description"]}
              rows={[
                ["latest", "string", "Id of the newest run."],
                                ["runs[].run_id", "string", "Run id, used in Get a run."],
                ["runs[].created_at", "ISO 8601", "When the run was scored."],
                ["runs[].language", "string", "ISO 639-1 code, for example ja."],
                ["runs[].tiers", "string[]", "Reference tiers present: verified, creator."],
                ["runs[].n_clips", "object", "Clips per tier."],
                ["runs[].providers", "string[]", "Provider ids in the run."],
              ]}
            />
          </Section>
          <Section id="get-run" title="Get a run" method="GET" path="/api/v1/runs/:runId" code={<CodeBlock samples={ex.get} />} response={<CodeBlock title="Response, live" samples={[{ label: "200", lang: "json", code: live.one }]} />}>
            <p>One run in full. Use the index's <Mono>latest</Mono> for the current numbers, or pin a run id for reproducible work.</p>
            <Fields head={["Parameter", "In", "Description"]} rows={[["runId", "path", "A run id from List runs. Letters, digits, dot, dash and underscore."]]} />
          </Section>
          <Section id="run-object" title="The run object">
            <p>Tiers are never mixed: every row says which tier it belongs to, and creator-tier numbers measure agreement with the creator's captions, not accuracy.</p>
            <Fields
              head={["Field", "Type", "Description"]}
              rows={[
                ["run_id, created_at, language", "string", "Identity of the run."],
                ["git_commit, sample_seed", "string, number", "The code and seed that produced it. Rerun with these to get the same numbers."],
                ["metrics[]", "object[]", "name, label, primary. ja uses cer (primary) and cer_reading."],
                ["providers[]", "object[]", "id, model_id, label, usd_per_hour, pricing_checked_on, paid, uploads_audio, plus data policy fields when known."],
                ["aggregates[]", "object[]", "One row per provider, tier, metric and slice. See below."],
                ["paired[]", "object[]", "Paired bootstrap per pair of providers and slice. See below."],
                ["stability[]", "object[]", "Run-to-run disagreement on a repeated subset."],
                ["findings[]", "object[]", "Plain-language claims with the table they come from."],
                ["errors", "object", "Top substitutions and worst clips per provider; snippets of a few characters."],
                ["clips[]", "object[]", "video_id, tier, duration_s, channel, tags, kind (short or window), start_s."],
                ["per_clip[]", "object[]", "Error, hallucination and latency per provider and clip."],
                ["limitations[]", "string[]", "What this run cannot tell you."],
              ]}
            />
          </Section>
          <Section id="aggregate-object" title="Aggregates and paired" last>
            <Fields
              head={["aggregates[] field", "Type", "Description"]}
              rows={[
                ["provider, model_id, tier, metric", "string", "What the row is about."],
                ["slice_kind, slice", "string", "all/all, tag/<tag>, or duration/<bucket>."],
                ["n_clips, too_small", "number, boolean", "too_small is true under 15 clips: do not rank on it."],
                ["error_rate, ci_low, ci_high", "number", "Corpus rate with a 95% percentile bootstrap interval (0 to 1)."],
                ["hallucination_rate, long_output_rate", "number", "See the methodology."],
                ["cost_per_hour, latency_p50, latency_p95, rtf", "number or null", "USD per audio hour at list price; milliseconds; real-time factor."],
              ]}
            />
            <Fields
              head={["paired[] field", "Type", "Description"]}
              rows={[
                ["provider_a, provider_b", "string", "Alphabetical, so provider_a < provider_b."],
                ["diff, ci_low, ci_high", "number", "a minus b on shared clips; negative means a makes fewer errors."],
                ["p_value", "number", "Two-sided, from 1000 paired resamples."],
                ["verdict", "string", "a_better, b_better or no_difference (interval crosses zero)."],
              ]}
            />
            <p>
              The full method is in the <Link to="/methodology" className="text-ink underline decoration-line-strong underline-offset-4">methodology</Link>.
            </p>
          </Section>
        </div>
      </div>
    </>
  );
}

function SideNav({ active }: { active: string | null }) {
  const [open, setOpen] = useState(false);
  const list = (
    <div className="grid gap-5">
      {NAV.map((g) => (
        <div key={g.group}>
          <div className="label mb-2 pl-3">{g.group}</div>
          <ul className="grid gap-0.5">
            {g.items.map((it) => (
              <li key={it.id}>
                <a
                  href={`#${it.id}`}
                  onClick={() => setOpen(false)}
                  className={cn("relative flex items-center gap-2 rounded-sm py-1 pl-3 text-[13px] transition-colors", active === it.id ? "text-ink" : "text-ink-2 hover:text-ink")}
                >
                  {active === it.id && <span aria-hidden className="absolute top-1.5 bottom-1.5 left-0 w-[2px] rounded-full bg-signal" />}
                  {"method" in it && <span className="font-mono text-[10px] text-good">{it.method}</span>}
                  {it.label}
                </a>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
  return (
    <nav aria-label="API reference" className="lg:sticky lg:top-20 lg:self-start">
      <div className="hidden lg:block">{list}</div>
      <div className="rounded-md border border-line bg-surface lg:hidden">
        <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="flex h-11 w-full items-center justify-between px-4 text-[13.5px] text-ink">
          Sections
          <ChevronDown size={16} className={cn("text-ink-3 transition-transform", open && "rotate-180")} />
        </button>
        {open && <div className="border-t border-line px-1 py-3">{list}</div>}
      </div>
    </nav>
  );
}

function Section({ id, title, method, path, children, code, response, last }: { id: string; title: string; method?: string; path?: string; children: ReactNode; code?: ReactNode; response?: ReactNode; last?: boolean }) {
  return (
    <section id={id} aria-labelledby={`${id}-h`} className={cn("grid scroll-mt-20 grid-cols-1 gap-8 py-10 first:pt-0 xl:gap-10", (code || response) && "xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]", !last && "border-b border-line")}>
      <div className="min-w-0">
        <h2 id={`${id}-h`} className="text-[22px] font-semibold tracking-[-0.02em] text-ink">
          {title}
        </h2>
        {path && (
          <div className="mt-3 inline-flex max-w-full items-center gap-2 rounded-sm border border-line bg-surface px-2 py-1">
            <Badge tone="good">{method}</Badge>
            <code className="truncate font-mono text-[13px] text-ink">{path}</code>
          </div>
        )}
        <div className="mt-4 grid gap-4 text-[14.5px] leading-[1.7] text-ink-2">{children}</div>
      </div>
      {(code || response) && (
        <div className="grid min-w-0 content-start gap-4 xl:sticky xl:top-20 xl:self-start">
          {code}
          {response}
        </div>
      )}
    </section>
  );
}

function Mono({ children }: { children: ReactNode }) {
  return <code className="rounded-[4px] border border-line bg-sunken px-1 py-px font-mono text-[0.86em] text-ink">{children}</code>;
}

function Fields({ head, rows }: { head: string[]; rows: string[][] }) {
  return (
    <div className="overflow-x-auto rounded-md border border-line bg-surface">
      <table className="w-full min-w-[440px] border-separate border-spacing-0 text-[13px]">
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h} scope="col" className="border-b border-line px-3.5 py-2 text-left font-normal">
                <span className="label">{h}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j} className={cn("border-b border-line px-3.5 py-2.5 align-top leading-snug", i === rows.length - 1 && "border-b-0", j === 0 ? "font-mono text-[12.5px] text-ink" : j === 1 ? "font-mono text-[12px] text-ink-3" : "text-ink-2")}>
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function examples(origin: string, runId: string) {
  const base = `${origin}/api/v1`;
  const out: Record<"quick" | "auth" | "limit" | "error" | "list" | "get", CodeSample[]> = {
    quick: [
      { label: "curl", lang: "shell", code: `# The latest run, no key needed\ncurl -s ${base}/runs | jq -r .latest` },
      { label: "JavaScript", lang: "js", code: `const index = await fetch("${base}/runs").then((r) => r.json());\nconst run = await fetch(\`${base}/runs/\${index.latest}\`).then((r) => r.json());\nconsole.log(run.providers.length, "models");` },
      { label: "Python", lang: "python", code: `import requests\n\nindex = requests.get("${base}/runs").json()\nrun = requests.get(f"${base}/runs/{index['latest']}").json()\nprint(len(run["providers"]), "models")` },
    ],
    auth: [
      { label: "curl", lang: "shell", code: `curl ${base}/runs \\\n  -H "Authorization: Bearer $CLIPBENCH_KEY"` },
      { label: "JavaScript", lang: "js", code: `const res = await fetch("${base}/runs", {\n  headers: { Authorization: \`Bearer \${process.env.CLIPBENCH_KEY}\` },\n});` },
      { label: "Python", lang: "python", code: `import os, requests\n\nr = requests.get(\n    "${base}/runs",\n    headers={"Authorization": f"Bearer {os.environ['CLIPBENCH_KEY']}"},\n)` },
    ],
    limit: [{ label: "429", lang: "shell", code: `HTTP/1.1 429 Too Many Requests\nRetry-After: 60\nX-RateLimit-Limit: 30\n\n{"error": {"code": "rate_limited", "message": "Rate limit is 30 requests per minute without an API key. Create a key on your account page for 600."}}` }],
    error: [{ label: "401", lang: "json", code: `{\n  "error": {\n    "code": "invalid_api_key",\n    "message": "Unknown or revoked API key."\n  }\n}` }],
    list: [
      { label: "curl", lang: "shell", code: `curl ${base}/runs \\\n  -H "Authorization: Bearer $CLIPBENCH_KEY"` },
      { label: "JavaScript", lang: "js", code: `const res = await fetch("${base}/runs", {\n  headers: { Authorization: \`Bearer \${process.env.CLIPBENCH_KEY}\` },\n});\nconst { latest, runs } = await res.json();` },
      { label: "Python", lang: "python", code: `import os, requests\n\nr = requests.get(\n    "${base}/runs",\n    headers={"Authorization": f"Bearer {os.environ['CLIPBENCH_KEY']}"},\n)\nr.raise_for_status()\nlatest = r.json()["latest"]` },
    ],
    get: [
      { label: "curl", lang: "shell", code: `curl ${base}/runs/${runId} \\\n  -H "Authorization: Bearer $CLIPBENCH_KEY"` },
      { label: "JavaScript", lang: "js", code: `const run = await fetch("${base}/runs/${runId}", {\n  headers: { Authorization: \`Bearer \${process.env.CLIPBENCH_KEY}\` },\n}).then((r) => r.json());\n\n// Overall ranking on verified clips\nconst rows = run.aggregates\n  .filter((a) => a.tier === "verified" && a.slice_kind === "all" && a.metric === "cer")\n  .sort((a, b) => a.error_rate - b.error_rate);` },
      { label: "Python", lang: "python", code: `import os, requests\n\nrun = requests.get(\n    "${base}/runs/${runId}",\n    headers={"Authorization": f"Bearer {os.environ['CLIPBENCH_KEY']}"},\n).json()\n\nrows = sorted(\n    (a for a in run["aggregates"]\n     if a["tier"] == "verified" and a["slice_kind"] == "all" and a["metric"] == "cer"),\n    key=lambda a: a["error_rate"],\n)` },
    ],
  };
  return out;
}

/** Response examples are the live export, trimmed: never hand-written numbers. */
function useExamples() {
  const src = useSource();
  const run = useRun().data?.run;
  const latest = src.data?.index.latest ?? "<run_id>";
  const list = src.data ? JSON.stringify({ ...src.data.index, runs: src.data.index.runs.slice(0, 2) }, null, 2) : "// Loading the live index";
  const one = run
    ? JSON.stringify(
        {
          run_id: run.run_id,
          created_at: run.created_at,
          language: run.language,
          git_commit: run.git_commit,
          metrics: run.metrics.slice(0, 1),
          providers: run.providers.slice(0, 1),
          aggregates: run.aggregates.slice(0, 1),
          paired: run.paired.slice(0, 1),
          "...": "clips, per_clip, errors, findings, limitations",
        },
        null,
        2,
      )
    : "// Loading the live run";
  return { latest, list, one };
}

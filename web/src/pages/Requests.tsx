import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { ChevronUp, Inbox, Loader2, Plus } from "lucide-react";
import { api, ApiError, useApi, type RequestItem } from "@/lib/api";
import { useSession } from "@/lib/auth";
import { ago } from "@/lib/format";
import { cn } from "@/lib/cn";
import { PageHeader } from "@/components/layout/PageHeader";
import { Segmented } from "@/components/ui/Segmented";
import { Badge } from "@/components/ui/Badge";
import { Button, buttonClass } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Field, Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/State";
import { Tooltip } from "@/components/ui/Tooltip";
import { toast } from "@/components/ui/Toast";

type Kind = "all" | "provider" | "language";

/**
 * Vote board (Featurebase / Canny pattern, as a list). Public counts; signing in lets you vote (one per person,
 * toggled) and add requests. Votes are optimistic and reconcile with the server's count.
 */
export default function Requests() {
  const { data: session } = useSession();
  const user = session?.user;
  const [params, setParams] = useSearchParams();
  const kind = (params.get("kind") as Kind) || "all";
  const res = useApi<{ requests: RequestItem[] }>("/api/requests");
  const [order, setOrder] = useState<string[]>([]);
  // Keep the order from load time so rows do not jump under the cursor while people vote.
  useEffect(() => {
    if (res.data) setOrder((o) => (o.length && res.data!.requests.every((r) => o.includes(r.id)) ? o : [...res.data!.requests].sort((a, b) => b.votes - a.votes || b.created_at - a.created_at).map((r) => r.id)));
  }, [res.data]);
  const rows = useMemo(() => {
    const all = res.data?.requests ?? [];
    const byId = new Map(all.map((r) => [r.id, r]));
    return order.map((id) => byId.get(id)).filter((r): r is RequestItem => Boolean(r) && (kind === "all" || r!.kind === kind));
  }, [res.data, order, kind]);

  const vote = async (r: RequestItem) => {
    const next = !r.voted;
    res.mutate((d) => ({ requests: d.requests.map((x) => (x.id === r.id ? { ...x, voted: next, votes: x.votes + (next ? 1 : -1) } : x)) }));
    try {
      const out = await api<{ id: string; voted: boolean; votes: number }>(`/api/requests/${encodeURIComponent(r.id)}/vote`, { method: "POST" });
      res.mutate((d) => ({ requests: d.requests.map((x) => (x.id === r.id ? { ...x, voted: out.voted, votes: out.votes } : x)) }));
    } catch (e) {
      res.mutate((d) => ({ requests: d.requests.map((x) => (x.id === r.id ? { ...x, voted: r.voted, votes: r.votes } : x)) }));
      toast.error("Your vote did not count", { description: e instanceof ApiError && !e.unavailable ? e.message : "The server did not answer. Try again in a minute." });
    }
  };

  const added = (r: RequestItem) => {
    res.mutate((d) => ({ requests: [r, ...d.requests] }));
    setOrder((o) => [r.id, ...o]);
  };

  return (
    <>
      <PageHeader
        eyebrow="Requests"
        title="What should we test next?"
        description="Ask for a model or a language and vote on what others asked for. The next run adds what has the most votes, as long as it has a public API or runs locally."
        actions={
          user ? (
            <NewRequest onAdded={added} />
          ) : (
            <Link to="/sign-in?next=/requests" className={buttonClass("primary", "sm")}>
              <Plus size={14} /> New request
            </Link>
          )
        }
      >
        <Segmented
          label="Kind"
          value={kind}
          onChange={(v) => {
            const p = new URLSearchParams(params);
            v === "all" ? p.delete("kind") : p.set("kind", v);
            setParams(p, { replace: true });
          }}
          options={[
            { value: "all", label: "All" },
            { value: "provider", label: "Models" },
            { value: "language", label: "Languages" },
          ]}
        />
      </PageHeader>
      <div className="frame grid max-w-[920px] gap-5 py-8 lg:max-w-[1240px] lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-10">
        <div className="grid content-start gap-4">
          {!user && res.status !== "error" && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-line bg-sunken/60 px-4 py-3">
              <p className="text-[13.5px] text-ink-2">Counts are public. Sign in to vote or add a request; one vote per person.</p>
              <Link to="/sign-in?next=/requests" className={buttonClass("secondary", "sm")}>
                Sign in to vote
              </Link>
            </div>
          )}
          {res.status === "error" && <ErrorState title="The request board is not available right now." body="The server did not answer. Results pages still work; try this one again in a minute." onRetry={res.reload} />}
          {res.status === "loading" && (
            <div className="grid gap-px overflow-hidden rounded-md border border-line bg-line">
              {[0, 1, 2, 3, 4].map((i) => (
                <div key={i} className="flex gap-4 bg-surface p-4">
                  <Skeleton className="h-12 w-11" />
                  <div className="grid flex-1 gap-2">
                    <Skeleton className="h-4 w-2/3" />
                    <Skeleton className="h-3 w-1/3" />
                  </div>
                </div>
              ))}
            </div>
          )}
          {res.status === "ready" &&
            (rows.length === 0 ? (
              <EmptyState
                icon={<Inbox size={16} />}
                title={kind === "all" ? "No requests yet." : `No ${kind === "provider" ? "model" : "language"} requests yet.`}
                body="Be the first: name a model or a language you want measured."
                action={
                  user ? (
                    <NewRequest onAdded={added} />
                  ) : (
                    <Link to="/sign-in?next=/requests" className={buttonClass("secondary", "sm")}>
                      Sign in to add one
                    </Link>
                  )
                }
              />
            ) : (
              <ol className="overflow-hidden rounded-md border border-line bg-surface shadow-card">
                {rows.map((r, i) => (
                  <li key={r.id} className="flex gap-4 border-b border-line px-4 py-4 last:border-b-0 sm:px-5">
                    <VoteBox r={r} signedIn={Boolean(user)} onVote={() => vote(r)} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="num text-[11px] text-ink-3">{String(i + 1).padStart(2, "0")}</span>
                        <h2 className="text-[15px] font-medium text-ink">{r.title}</h2>
                      </div>
                      {r.body && <p className="mt-1 line-clamp-2 text-[13.5px] text-ink-2">{r.body}</p>}
                      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[12px] text-ink-3">
                        <Badge>{r.kind === "provider" ? "Model" : "Language"}</Badge>
                        {r.status !== "open" && <Badge tone={r.status === "done" ? "good" : "ins"}>{r.status}</Badge>}
                        {r.mine && <Badge tone="outline">Yours</Badge>}
                        <span className="ml-1">{ago(r.created_at)}</span>
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
            ))}
        </div>
        <aside className="grid content-start gap-4 text-[13.5px] text-ink-2">
          <div className="rounded-md border border-line bg-surface p-5">
            <div className="label mb-3">How it works</div>
            <ol className="grid gap-3">
              {[
                "Each person gets one vote per request. Click again to take it back.",
                "Before each run we add the top requests that have a public API or run locally, within the run budget.",
                "When a request ships, it is marked done and the model appears on the leaderboard.",
              ].map((t, i) => (
                <li key={i} className="flex gap-3">
                  <span className="num text-[11.5px] text-signal-ink">{String(i + 1).padStart(2, "0")}</span>
                  <span>{t}</span>
                </li>
              ))}
            </ol>
          </div>
          <p className="px-1 text-[12.5px] text-ink-3">Requests are public. Please do not include personal data or private links.</p>
        </aside>
      </div>
    </>
  );
}

function VoteBox({ r, signedIn, onVote }: { r: RequestItem; signedIn: boolean; onVote: () => void }) {
  const box = cn(
    "grid h-14 w-12 shrink-0 place-items-center content-center gap-0.5 rounded-sm border transition-[background-color,border-color,color] duration-150",
    r.voted ? "border-ink bg-ink text-paper" : "border-line-strong bg-surface text-ink hover:border-ink-3 hover:bg-sunken",
  );
  const inner = (
    <>
      <ChevronUp size={15} strokeWidth={2} className={r.voted ? "" : "text-ink-3"} aria-hidden />
      <span className="num text-[13px] leading-none">{r.votes}</span>
    </>
  );
  if (!signedIn)
    return (
      <Tooltip content="Sign in to vote">
        <Link to="/sign-in?next=/requests" className={box} aria-label={`${r.votes} votes. Sign in to vote for ${r.title}`}>
          {inner}
        </Link>
      </Tooltip>
    );
  return (
    <button type="button" onClick={onVote} aria-pressed={r.voted} aria-label={`${r.voted ? "Remove your vote for" : "Vote for"} ${r.title}, ${r.votes} votes`} className={box}>
      {inner}
    </button>
  );
}

function NewRequest({ onAdded }: { onAdded: (r: RequestItem) => void }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<"provider" | "language">("provider");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (title.trim().length < 3) return setErr("Give it a name of at least 3 characters.");
    setErr(null);
    setBusy(true);
    try {
      const out = await api<{ request: RequestItem }>("/api/requests", { method: "POST", body: { kind, title: title.trim(), body: body.trim() } });
      onAdded(out.request);
      toast.success("Request added", { description: "Your vote is counted on it already." });
      setOpen(false);
      setTitle("");
      setBody("");
    } catch (e) {
      toast.error("Could not add the request", { description: e instanceof ApiError && !e.unavailable ? e.message : "The server did not answer. Try again in a minute." });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title="New request"
      description="One model or one language per request. Check the list first; voting beats a duplicate."
      trigger={
        <Button size="sm">
          <Plus size={14} /> New request
        </Button>
      }
    >
      <form onSubmit={submit} noValidate className="grid gap-4">
        <div className="grid gap-1.5">
          <span className="text-[13px] font-medium text-ink">Kind</span>
          <Segmented
            label="Kind"
            value={kind}
            onChange={setKind}
            options={[
              { value: "provider", label: "Model" },
              { value: "language", label: "Language" },
            ]}
          />
        </div>
        <Field label={kind === "provider" ? "Model" : "Language"} htmlFor="req-title" error={err ?? undefined} hint={kind === "provider" ? "Provider and model name, as the provider writes it." : "For example: Korean, Brazilian Portuguese."}>
          <Input id="req-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} invalid={Boolean(err)} autoFocus />
        </Field>
        <div className="grid gap-1.5">
          <label htmlFor="req-body" className="text-[13px] font-medium text-ink">
            Why <span className="font-normal text-ink-3">(optional)</span>
          </label>
          <textarea
            id="req-body"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            maxLength={2000}
            rows={3}
            className="w-full resize-y rounded-sm border border-line-strong bg-surface px-3 py-2 text-[14px] text-ink placeholder:text-ink-3 hover:border-ink-3 focus:border-ink focus:ring-[3px] focus:ring-signal/20 focus:outline-none"
            placeholder="What kind of clips do you need it for?"
          />
        </div>
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button type="submit" disabled={busy}>
            {busy && <Loader2 size={14} className="animate-spin" />} Add request
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

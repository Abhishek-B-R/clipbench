import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router";
import { AlertTriangle, Check, Copy, KeyRound, Link2, Loader2, LogOut, Plus, Star, Trash2 } from "lucide-react";
import { api, ApiError, useApi, type AlertPrefs, type ApiKey, type SavedComparison } from "@/lib/api";
import { signOut, useSession } from "@/lib/auth";
import { useRun } from "@/lib/data";
import { TAG_LABEL, TIER_LABEL, overall, providerColor, providerLabel } from "@/lib/analysis";
import { ago, dateMs, pct, usdPerHour } from "@/lib/format";
import { cn } from "@/lib/cn";
import { PageHeader } from "@/components/layout/PageHeader";
import { Tabs, tabPanelProps } from "@/components/ui/Tabs";
import { Avatar } from "@/components/ui/Avatar";
import { Badge, SeriesDot } from "@/components/ui/Badge";
import { Button, buttonClass } from "@/components/ui/Button";
import { Chip } from "@/components/ui/Chip";
import { Dialog } from "@/components/ui/Dialog";
import { Field, Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";
import { Switch } from "@/components/ui/Switch";
import { EmptyState, ErrorState } from "@/components/ui/State";
import { toast } from "@/components/ui/Toast";

type Tab = "profile" | "watchlist" | "alerts" | "comparisons" | "keys";
const TABS: { value: Tab; label: string }[] = [
  { value: "profile", label: "Profile" },
  { value: "watchlist", label: "Watchlist" },
  { value: "alerts", label: "Alerts" },
  { value: "comparisons", label: "Saved comparisons" },
  { value: "keys", label: "API keys" },
];

const fail = (e: unknown, fallback: string) =>
  toast.error(fallback, { description: e instanceof ApiError && !e.unavailable ? e.message : "The account service did not answer. Try again in a minute." });

/** Account: profile, watchlist, alerts, saved comparisons and API keys. The tab follows the URL hash (#keys). */
export default function Account() {
  const { data, isPending } = useSession();
  const loc = useLocation();
  const navigate = useNavigate();
  const fromHash = loc.hash.slice(1) as Tab;
  const tab: Tab = TABS.some((t) => t.value === fromHash) ? fromHash : "profile";
  useEffect(() => {
    document.title = "Account | clipbench";
  }, []);
  if (isPending)
    return (
      <div className="frame grid gap-4 py-14">
        <Skeleton className="h-9 w-56" />
        <Skeleton className="h-11 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  const user = data?.user;
  if (!user) return <Navigate to={`/sign-in?next=${encodeURIComponent("/account" + loc.hash)}`} replace />;

  return (
    <>
      <PageHeader
        eyebrow={user.email}
        title="Account"
        description="Models you watch, the emails you get, comparisons you saved and keys for the data API."
        actions={
          <Button variant="secondary" size="sm" onClick={() => void signOut().then(() => navigate("/"))}>
            <LogOut size={14} /> Sign out
          </Button>
        }
      />
      <div className="border-b border-line">
        <div className="frame">
          <Tabs label="Account sections" value={tab} onChange={(v) => navigate({ hash: v === "profile" ? "" : v }, { replace: true })} tabs={TABS} />
        </div>
      </div>
      <div className="frame py-8">
        <section {...tabPanelProps(tab)} className="max-w-[880px] focus:outline-none">
          {tab === "profile" && <Profile user={user} />}
          {tab === "watchlist" && <Watchlist />}
          {tab === "alerts" && <Alerts />}
          {tab === "comparisons" && <Comparisons />}
          {tab === "keys" && <Keys />}
        </section>
      </div>
    </>
  );
}

function Panel({ title, description, actions, children }: { title: string; description?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="grid gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-[18px] font-semibold tracking-[-0.015em] text-ink">{title}</h2>
          {description && <p className="mt-1 max-w-[62ch] text-[13.5px] text-ink-2">{description}</p>}
        </div>
        {actions}
      </div>
      {children}
    </div>
  );
}

function ListSkeleton() {
  return (
    <div className="grid gap-px overflow-hidden rounded-md border border-line bg-line">
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex items-center gap-4 bg-surface px-5 py-4">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="ml-auto h-4 w-24" />
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- profile

function Profile({ user }: { user: { name: string; email: string; image?: string | null } }) {
  return (
    <Panel title="Profile" description="From the account you signed in with. We keep your name and email, nothing else about you.">
      <div className="flex items-center gap-4 rounded-md border border-line bg-surface p-5 shadow-card">
        <Avatar src={user.image} name={user.name || user.email} size={52} />
        <div className="min-w-0">
          <div className="truncate text-[16px] font-medium text-ink">{user.name || "No name set"}</div>
          <div className="truncate text-[13.5px] text-ink-2">{user.email}</div>
        </div>
      </div>
      <dl className="overflow-hidden rounded-md border border-line bg-surface">
        {[
          ["What we store", "Name, email, your watchlist, alert settings, saved comparisons, and API keys as one-way hashes."],
          ["What we never store", "Passwords (sign-in is by link or OAuth), tracking cookies, or anything about what you browse."],
          ["Delete your account", "Email the maintainers from the address above and everything is removed within a week."],
        ].map(([k, v]) => (
          <div key={k} className="grid gap-1 border-b border-line px-5 py-3.5 last:border-b-0 sm:grid-cols-[200px_minmax(0,1fr)] sm:gap-4">
            <dt className="text-[13px] text-ink-3">{k}</dt>
            <dd className="text-[13.5px] text-ink-2">{v}</dd>
          </div>
        ))}
      </dl>
    </Panel>
  );
}

// ---------------------------------------------------------------- watchlist

function Watchlist() {
  const res = useApi<{ providers: string[] }>("/api/watchlist");
  const run = useRun().data?.run;
  const rows = run ? overall(run) : [];
  const toggle = async (id: string, on: boolean) => {
    res.mutate((d) => ({ providers: on ? [...d.providers, id] : d.providers.filter((x) => x !== id) }));
    try {
      await api(`/api/watchlist/${encodeURIComponent(id)}`, { method: on ? "PUT" : "DELETE" });
    } catch (e) {
      res.mutate((d) => ({ providers: on ? d.providers.filter((x) => x !== id) : [...d.providers, id] }));
      fail(e, "Could not update your watchlist");
    }
  };
  const watched = res.data?.providers ?? [];
  return (
    <Panel title="Watchlist" description="Watched models are listed here with their latest numbers. Rank-change alerts only cover models you watch.">
      {res.status === "loading" && <ListSkeleton />}
      {res.status === "error" && <ErrorState title="Could not load your watchlist." onRetry={res.reload} />}
      {res.status === "ready" && (
        <>
          {watched.length === 0 ? (
            <EmptyState icon={<Star size={16} />} title="You are not watching any model yet." body="Pick a few below, or use the Watch button on any model's page." />
          ) : (
            <ul className="overflow-hidden rounded-md border border-line bg-surface shadow-card">
              {watched.map((id) => {
                const a = rows.find((r) => r.provider === id);
                const rank = rows.findIndex((r) => r.provider === id) + 1;
                return (
                  <li key={id} className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line px-4 py-3 last:border-b-0 sm:px-5">
                    <Link to={`/providers/${id}`} className="flex min-w-0 flex-1 items-center gap-2.5 hover:underline">
                      {run && <SeriesDot color={providerColor(run, id)} />}
                      <span className="truncate text-[14px] font-medium text-ink">{run ? providerLabel(run, id) : id}</span>
                    </Link>
                    {a ? (
                      <span className="flex items-center gap-4 text-[12.5px] text-ink-3">
                        <span className="num text-ink">{pct(a.error_rate)}</span>
                        <span className="num">#{rank}</span>
                        <span className="num hidden sm:inline">{usdPerHour(a.cost_per_hour)}/h</span>
                      </span>
                    ) : (
                      <span className="text-[12.5px] text-ink-3">Not in the latest run</span>
                    )}
                    <Button variant="ghost" size="sm" onClick={() => toggle(id, false)} aria-label={`Stop watching ${run ? providerLabel(run, id) : id}`}>
                      <Star size={14} className="fill-[var(--warn)] text-warn" /> Watching
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
          {run && rows.some((r) => !watched.includes(r.provider)) && (
            <div>
              <div className="label mb-2.5">Add a model</div>
              <div className="flex flex-wrap gap-1.5">
                {rows
                  .filter((r) => !watched.includes(r.provider))
                  .map((r) => (
                    <Chip key={r.provider} pressed={false} onClick={() => toggle(r.provider, true)} dot={<SeriesDot color={providerColor(run, r.provider)} />}>
                      {providerLabel(run, r.provider)}
                    </Chip>
                  ))}
              </div>
            </div>
          )}
        </>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------- alerts

function Alerts() {
  const res = useApi<AlertPrefs>("/api/alerts");
  const save = async (patch: Partial<AlertPrefs>) => {
    const prev = res.data!;
    const next = { ...prev, ...patch };
    res.mutate(() => next);
    try {
      await api("/api/alerts", { method: "PUT", body: next });
      toast("Alert settings saved");
    } catch (e) {
      res.mutate(() => prev);
      fail(e, "Could not save your alert settings");
    }
  };
  const items: { key: keyof AlertPrefs; title: string; body: string }[] = [
    { key: "email_on_new_run", title: "New run published", body: "One email when a run lands, with the new leader and what changed. A few times a month at most." },
    { key: "email_on_rank_change", title: "A watched model changes rank", body: "Only for models on your watchlist, and only when the paired test backs the move." },
  ];
  return (
    <Panel title="Email alerts" description="Sent to your sign-in address. Every email has a one-click unsubscribe.">
      {res.status === "loading" && <ListSkeleton />}
      {res.status === "error" && <ErrorState title="Could not load your alert settings." onRetry={res.reload} />}
      {res.data && (
        <ul className="overflow-hidden rounded-md border border-line bg-surface shadow-card">
          {items.map((it) => (
            <li key={it.key} className="flex items-start justify-between gap-6 border-b border-line px-5 py-4 last:border-b-0">
              <span>
                <span id={`al-${it.key}`} className="block text-[14px] font-medium text-ink">
                  {it.title}
                </span>
                <span className="mt-0.5 block max-w-[56ch] text-[13px] text-ink-2">{it.body}</span>
              </span>
              <Switch checked={res.data![it.key]} onChange={(v) => save({ [it.key]: v })} labelledBy={`al-${it.key}`} className="mt-0.5" />
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------- comparisons

function compareUrl(c: SavedComparison) {
  return `/compare?${new URLSearchParams({ m: c.providers.join(","), tier: c.tier, metric: c.metric, ...(c.tags.length ? { tags: c.tags.join(",") } : {}) })}`;
}

function Comparisons() {
  const res = useApi<{ comparisons: SavedComparison[] }>("/api/comparisons");
  const run = useRun().data?.run;
  const [del, setDel] = useState<SavedComparison | null>(null);
  const remove = async (c: SavedComparison) => {
    setDel(null);
    res.mutate((d) => ({ comparisons: d.comparisons.filter((x) => x.id !== c.id) }));
    try {
      await api(`/api/comparisons/${encodeURIComponent(c.id)}`, { method: "DELETE" });
      toast("Comparison deleted");
    } catch (e) {
      res.reload();
      fail(e, "Could not delete the comparison");
    }
  };
  const copy = async (c: SavedComparison) => {
    try {
      await navigator.clipboard.writeText(`${location.origin}/compare?c=${c.id}`);
      toast("Public link copied");
    } catch {
      toast.error("Could not copy the link");
    }
  };
  return (
    <Panel
      title="Saved comparisons"
      description="Saved from the Compare page. Public ones open for anyone with the link; your name is never shown."
      actions={
        <Link to="/compare" className={buttonClass("secondary", "sm")}>
          <Plus size={14} /> New comparison
        </Link>
      }
    >
      {res.status === "loading" && <ListSkeleton />}
      {res.status === "error" && <ErrorState title="Could not load your comparisons." onRetry={res.reload} />}
      {res.status === "ready" &&
        (res.data!.comparisons.length === 0 ? (
          <EmptyState title="No saved comparisons." body="Pick two to four models on the Compare page and press Save." />
        ) : (
          <ul className="overflow-hidden rounded-md border border-line bg-surface shadow-card">
            {res.data!.comparisons.map((c) => (
              <li key={c.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line px-4 py-3.5 last:border-b-0 sm:px-5">
                <div className="min-w-0 flex-1">
                  <Link to={compareUrl(c)} className="text-[14px] font-medium text-ink hover:underline">
                    {c.name}
                  </Link>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-3">
                    <span className="inline-flex flex-wrap items-center gap-x-2">
                      {c.providers.map((p) => (
                        <span key={p} className="inline-flex items-center gap-1">
                          {run && <SeriesDot color={providerColor(run, p)} size={6} />}
                          {run ? providerLabel(run, p) : p}
                        </span>
                      ))}
                    </span>
                    <span>{TIER_LABEL[c.tier]}</span>
                    <span className="font-mono">{c.metric}</span>
                    {c.tags.length > 0 && <span>{c.tags.map((t) => TAG_LABEL[t] ?? t).join(", ")}</span>}
                    <span>{dateMs(c.created_at)}</span>
                  </div>
                </div>
                {c.public ? <Badge tone="ins">Public</Badge> : <Badge>Private</Badge>}
                <span className="flex gap-1">
                  {c.public && (
                    <Button variant="ghost" size="icon" onClick={() => copy(c)} aria-label={`Copy public link to ${c.name}`}>
                      <Link2 size={14} />
                    </Button>
                  )}
                  <Button variant="ghost" size="icon" onClick={() => setDel(c)} aria-label={`Delete ${c.name}`}>
                    <Trash2 size={14} />
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        ))}
      <Dialog open={del != null} onOpenChange={(o) => !o && setDel(null)} title="Delete this comparison?" description={del ? `"${del.name}" and its public link stop working. This cannot be undone.` : undefined}>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setDel(null)}>
            Cancel
          </Button>
          <Button variant="signal" onClick={() => del && remove(del)}>
            Delete
          </Button>
        </div>
      </Dialog>
    </Panel>
  );
}

// ---------------------------------------------------------------- API keys

function Keys() {
  const res = useApi<{ keys: ApiKey[] }>("/api/keys");
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [revoke, setRevoke] = useState<ApiKey | null>(null);
  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      const out = await api<{ key: ApiKey; secret: string }>("/api/keys", { method: "POST", body: { name: name.trim() } });
      res.mutate((d) => ({ keys: [out.key, ...d.keys] }));
      setSecret(out.secret);
    } catch (err) {
      fail(err, "Could not create the key");
    } finally {
      setBusy(false);
    }
  };
  const close = (o: boolean) => {
    setOpen(o);
    if (!o) {
      setSecret(null);
      setName("");
      setCopied(false);
    }
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(secret ?? "");
      setCopied(true);
      toast("Key copied");
    } catch {
      toast.error("Could not copy. Select the key and copy it by hand.");
    }
  };
  const doRevoke = async (k: ApiKey) => {
    setRevoke(null);
    res.mutate((d) => ({ keys: d.keys.filter((x) => x.id !== k.id) }));
    try {
      await api(`/api/keys/${encodeURIComponent(k.id)}`, { method: "DELETE" });
      toast(`Revoked "${k.name}"`);
    } catch (e) {
      res.reload();
      fail(e, "Could not revoke the key");
    }
  };
  const keys = res.data?.keys ?? [];
  return (
    <Panel
      title="API keys"
      description={
        <>
          Keys raise the rate limit on the public data API from 30 to 600 requests a minute. Send one as <code className="rounded-[4px] border border-line bg-sunken px-1 font-mono text-[12px]">Authorization: Bearer cb_...</code>.{" "}
          <Link to="/docs/api" className="text-ink underline decoration-line-strong underline-offset-4">
            API reference
          </Link>
        </>
      }
      actions={
        <Dialog
          open={open}
          onOpenChange={close}
          title={secret ? "Copy your new key" : "Create an API key"}
          description={secret ? undefined : "Name it after where it will live, so you know which one to revoke later."}
          trigger={
            <Button size="sm" disabled={res.status !== "ready" || keys.length >= 10}>
              <Plus size={14} /> Create key
            </Button>
          }
        >
          {secret ? (
            <div className="grid gap-4">
              <p className="flex gap-2.5 rounded-sm border border-line bg-warn-soft/60 px-3 py-2.5 text-[13px] text-ink-2">
                <AlertTriangle size={15} className="mt-0.5 shrink-0 text-warn" aria-hidden />
                This is the only time the full key is shown. We store a hash, so it cannot be recovered.
              </p>
              <div className="flex items-center gap-2 rounded-sm border border-line-strong bg-sunken p-1.5 pl-3">
                <code className="min-w-0 flex-1 truncate font-mono text-[13px] text-ink select-all">{secret}</code>
                <Button size="sm" variant={copied ? "secondary" : "primary"} onClick={copy}>
                  {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? "Copied" : "Copy"}
                </Button>
              </div>
              <div className="flex justify-end">
                <Button variant="secondary" onClick={() => close(false)}>
                  Done
                </Button>
              </div>
            </div>
          ) : (
            <form onSubmit={create} className="grid gap-4">
              <Field label="Name" htmlFor="key-name" hint="For example: laptop, CI, research notebook.">
                <Input id="key-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} autoFocus />
              </Field>
              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => close(false)}>
                  Cancel
                </Button>
                <Button type="submit" disabled={busy || !name.trim()}>
                  {busy ? <Loader2 size={14} className="animate-spin" /> : <KeyRound size={14} />} Create key
                </Button>
              </div>
            </form>
          )}
        </Dialog>
      }
    >
      {res.status === "loading" && <ListSkeleton />}
      {res.status === "error" && <ErrorState title="Could not load your API keys." onRetry={res.reload} />}
      {res.status === "ready" &&
        (keys.length === 0 ? (
          <EmptyState icon={<KeyRound size={16} />} title="No API keys yet." body="The data API works without a key at a low rate limit. Create one when you need more." />
        ) : (
          <div className="overflow-x-auto rounded-md border border-line bg-surface shadow-card">
            <table className="w-full border-separate border-spacing-0 text-[13.5px]">
              <caption className="sr-only">API keys</caption>
              <thead>
                <tr>
                  {["Name", "Key", "Created", "Last used", ""].map((h, i) => (
                    <th key={i} scope="col" className={cn("border-b border-line px-3 py-2.5 text-left font-normal first:pl-5 last:pr-5", i === 4 && "w-px", (i === 2 || i === 3) && "hidden sm:table-cell")}>
                      {h && <span className="label">{h}</span>}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {keys.map((k) => (
                  <tr key={k.id} className="group">
                    <td className="border-b border-line py-3 pr-3 pl-5 font-medium text-ink group-last:border-b-0">
                      {k.name}
                      <span className="block text-[12px] font-normal text-ink-3 sm:hidden">Last used {k.last_used_at ? ago(k.last_used_at) : "never"}</span>
                    </td>
                    <td className="border-b border-line px-3 py-3 font-mono text-[12.5px] text-ink-2 group-last:border-b-0">
                      {k.prefix}
                      <span className="text-ink-3">{"•".repeat(4)}</span>
                    </td>
                    <td className="hidden border-b border-line px-3 py-3 text-[12.5px] text-ink-2 group-last:border-b-0 sm:table-cell">{dateMs(k.created_at)}</td>
                    <td className="hidden border-b border-line px-3 py-3 text-[12.5px] text-ink-2 group-last:border-b-0 sm:table-cell">{k.last_used_at ? ago(k.last_used_at) : <span className="text-ink-3">Never</span>}</td>
                    <td className="border-b border-line py-3 pr-5 pl-3 text-right group-last:border-b-0">
                      <Button variant="ghost" size="sm" onClick={() => setRevoke(k)}>
                        Revoke
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      {keys.length >= 10 && <p className="text-[12.5px] text-ink-3">You have the maximum of 10 keys. Revoke one to create another.</p>}
      <Dialog open={revoke != null} onOpenChange={(o) => !o && setRevoke(null)} title="Revoke this key?" description={revoke ? `Requests that send "${revoke.name}" (${revoke.prefix}...) start failing right away. This cannot be undone.` : undefined}>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setRevoke(null)}>
            Cancel
          </Button>
          <Button variant="signal" onClick={() => revoke && doRevoke(revoke)}>
            Revoke key
          </Button>
        </div>
      </Dialog>
    </Panel>
  );
}

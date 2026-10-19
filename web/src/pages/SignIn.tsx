import { useEffect, useState, type FormEvent } from "react";
import { maxOf } from "@/lib/scale";
import { Link, Navigate, useSearchParams } from "react-router";
import { ArrowLeft, BellRing, CircleAlert, KeyRound, Loader2, MailCheck, Star } from "lucide-react";
import { signInMagicLink, signInSocial, useSession } from "@/lib/auth";
import { useAuthProviders } from "@/lib/api";
import { useRun } from "@/lib/data";
import { overall, providerColor, providerLabel } from "@/lib/analysis";
import { pct } from "@/lib/format";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import { Badge, SeriesDot } from "@/components/ui/Badge";
import { CIBar } from "@/components/ui/CIBar";
import { Corners } from "@/components/ui/Card";
import { GitHubIcon, GoogleIcon } from "@/components/ui/BrandIcons";
import { toast } from "@/components/ui/Toast";
import { Wordmark } from "@/components/layout/Logo";

const LAST = "clipbench-last-signin";
type Method = "github" | "google" | "email";

function readLast(): Method | null {
  try {
    const v = localStorage.getItem(LAST);
    return v === "github" || v === "google" || v === "email" ? v : null;
  } catch {
    return null;
  }
}
function saveLast(m: Method) {
  try {
    localStorage.setItem(LAST, m);
  } catch {
    /* ignore */
  }
}

/**
 * Sign in: GitHub, Google, or an email magic link (Better Auth via the Worker). Split layout: form left,
 * a preview of what an account adds on the right. When /api is down, actions fail with a clear toast.
 */
export default function SignIn() {
  const { data } = useSession();
  const [params] = useSearchParams();
  const next = params.get("next") ?? "/account";
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState<Method | null>(null);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const last = readLast();
  const auth = useAuthProviders();
  const show = auth.providers;
  const off = auth.status !== "ready";

  useEffect(() => {
    document.title = "Sign in | clipbench";
  }, []);

  if (data?.user) return <Navigate to={next} replace />;

  const social = async (p: "github" | "google") => {
    setBusy(p);
    saveLast(p);
    try {
      const r = await signInSocial(p, next);
      if (r?.error) throw new Error(r.error.message ?? "Sign-in failed");
    } catch {
      toast.error("Sign-in is not available right now", { description: "The account service did not answer. Browsing works without an account." });
      setBusy(null);
    }
  };

  const magic = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!/^\S+@\S+\.\S+$/.test(email)) {
      setError("Enter an email address like name@example.com.");
      return;
    }
    setBusy("email");
    saveLast("email");
    try {
      const r = await signInMagicLink(email, next);
      if (r?.error) throw new Error(r.error.message ?? "Could not send the link");
      setSent(true);
    } catch {
      toast.error("Could not send the link", { description: "The account service did not answer. Try again in a minute." });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="frame grid min-h-[calc(100dvh-56px)] gap-10 py-10 lg:grid-cols-2 lg:gap-16 lg:py-16">
      <div className="flex flex-col justify-center">
        <div className="mx-auto w-full max-w-[380px]">
          <Link to="/" className="mb-10 inline-flex items-center gap-1.5 text-[13px] text-ink-3 hover:text-ink">
            <ArrowLeft size={14} /> Back
          </Link>
          {sent ? (
            <div role="status">
              <span className="grid size-11 place-items-center rounded-md border border-line bg-surface text-ink">
                <MailCheck size={20} strokeWidth={1.75} />
              </span>
              <h1 className="display mt-6 text-[40px]">Check your inbox.</h1>
              <p className="mt-3 text-[15px] text-ink-2">
                We sent a sign-in link to <span className="text-ink">{email}</span>. It works once and expires soon.
              </p>
              <button type="button" onClick={() => setSent(false)} className="mt-6 text-[13.5px] text-ink-2 underline decoration-line-strong underline-offset-4 hover:text-ink">
                Use a different email
              </button>
            </div>
          ) : (
            <>
              <h1 className="display text-[44px] leading-none">Sign in</h1>
              <p className="mt-3 text-[15px] text-ink-2">Watch models, get run alerts, save comparisons and create API keys. Browsing results never needs an account.</p>
              {auth.status === "down" && (
                <p role="status" className="mt-6 flex gap-2.5 rounded-sm border border-line bg-sunken px-3 py-2.5 text-[13px] text-ink-2">
                  <CircleAlert size={15} className="mt-0.5 shrink-0 text-ink-3" aria-hidden />
                  Sign in is unavailable right now. Browsing results works without an account.
                </p>
              )}
              {auth.status === "ready" && !show.github && !show.google && !show.email && (
                <p role="status" className="mt-6 text-[13px] text-ink-2">No sign-in method is configured on this server yet.</p>
              )}
              {(show.github || show.google) && (
                <div className={auth.status === "down" ? "mt-5 grid gap-2.5" : "mt-8 grid gap-2.5"}>
                  {show.github && (
                    <SocialButton onClick={() => social("github")} busy={busy === "github"} disabled={off} last={last === "github"} icon={<GitHubIcon size={17} />}>
                      Continue with GitHub
                    </SocialButton>
                  )}
                  {show.google && (
                    <SocialButton onClick={() => social("google")} busy={busy === "google"} disabled={off} last={last === "google"} icon={<GoogleIcon size={17} />}>
                      Continue with Google
                    </SocialButton>
                  )}
                </div>
              )}
              {(show.github || show.google) && show.email && (
                <div className="my-6 flex items-center gap-3 text-[12px] text-ink-3">
                  <span className="h-px flex-1 bg-line" />
                  or with email
                  <span className="h-px flex-1 bg-line" />
                </div>
              )}
              {show.email && (
                <form onSubmit={magic} noValidate className={show.github || show.google ? "grid gap-3" : "mt-8 grid gap-3"}>
                  <Field label="Email" htmlFor="email" error={error ?? undefined} hint="We send a one-time link. No password.">
                    <Input id="email" type="email" autoComplete="email" inputMode="email" placeholder="you@example.com" value={email} onChange={(e) => setEmail(e.target.value)} invalid={Boolean(error)} disabled={off} />
                  </Field>
                  <Button type="submit" size="lg" disabled={busy !== null || off} className="relative w-full">
                    {busy === "email" ? <Loader2 size={16} className="animate-spin" /> : null}
                    Email me a sign-in link
                    {last === "email" && <LastUsed />}
                  </Button>
                </form>
              )}
              <p className="mt-8 text-[12px] leading-relaxed text-ink-3">
                By continuing you agree to store a session cookie on this site. We keep your email and name, nothing else. No tracking.
              </p>
            </>
          )}
        </div>
      </div>
      <Preview />
    </div>
  );
}

function SocialButton({ children, icon, onClick, busy, last, disabled }: { children: React.ReactNode; icon: React.ReactNode; onClick: () => void; busy: boolean; last: boolean; disabled?: boolean }) {
  return (
    <Button variant="secondary" size="lg" onClick={onClick} disabled={busy || disabled} className="relative w-full">
      {busy ? <Loader2 size={16} className="animate-spin" /> : icon}
      {children}
      {last && <LastUsed />}
    </Button>
  );
}

function LastUsed() {
  return (
    <span className="absolute -top-2 right-3">
      <Badge tone="ins" className="bg-surface">
        Last used
      </Badge>
    </span>
  );
}

/** Right panel: what the account adds, drawn with real numbers from the latest run. */
function Preview() {
  const run = useRun().data?.run;
  const rows = run ? overall(run).slice(0, 3) : [];
  const max = maxOf(rows.map((r) => r.ci_high), 0.1) * 1.1 || 0.1;
  return (
    <div className="dot-grid relative hidden overflow-hidden rounded-lg border border-line bg-sunken lg:flex lg:items-center lg:justify-center">
      <div className="w-full max-w-[420px] p-8">
        <div className="relative rounded-md border border-line bg-surface shadow-pop">
          <Corners />
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <span className="label">Your watchlist</span>
            <Badge tone="outline">Example</Badge>
          </div>
          <ul>
            {run &&
              rows.map((r) => (
                <li key={r.provider} className="flex items-center gap-3 border-b border-line px-4 py-3 last:border-b-0">
                  <Star size={14} className="fill-[var(--warn)] text-warn" aria-hidden />
                  <SeriesDot color={providerColor(run, r.provider)} />
                  <span className="flex-1 text-[13.5px] text-ink">{providerLabel(run, r.provider)}</span>
                  <CIBar value={r.error_rate} low={r.ci_low} high={r.ci_high} max={max} color={providerColor(run, r.provider)} width={90} />
                  <span className="num w-12 text-right text-[12.5px] text-ink-2">{pct(r.error_rate)}</span>
                </li>
              ))}
          </ul>
        </div>
        <ul className="mt-8 grid gap-4">
          {[
            { icon: BellRing, t: "An email when a new run lands or a model you watch changes rank." },
            { icon: Star, t: "Saved comparisons with a link you can share." },
            { icon: KeyRound, t: "API keys with higher limits on the data API." },
          ].map((f) => (
            <li key={f.t} className="flex gap-3 text-[13.5px] text-ink-2">
              <f.icon size={16} strokeWidth={1.75} className="mt-0.5 shrink-0 text-ink-3" />
              {f.t}
            </li>
          ))}
        </ul>
        <div className="mt-10 opacity-60">
          <Wordmark />
        </div>
      </div>
    </div>
  );
}

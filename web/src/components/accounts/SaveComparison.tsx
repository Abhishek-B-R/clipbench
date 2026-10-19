import { useState, type FormEvent } from "react";
import { Link, useLocation } from "react-router";
import { Bookmark, Loader2 } from "lucide-react";
import type { Run, Tier } from "@/lib/types";
import { api, ApiError } from "@/lib/api";
import { useSession } from "@/lib/auth";
import { providerLabel } from "@/lib/analysis";
import { Button, buttonClass } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Field, Input } from "@/components/ui/Input";
import { Switch } from "@/components/ui/Switch";
import { toast } from "@/components/ui/Toast";

function defaultName(run: Run, models: string[]) {
  return models.map((m) => providerLabel(run, m)).join(" vs ");
}

/** Save a comparison to the account. Lazy-loaded by Compare only when ACCOUNTS is on (it carries the Better Auth client). */
export default function SaveComparison({ run, models, tier, metric, tags }: { run: Run; models: string[]; tier: Tier; metric: string; tags: string[] }) {
  const { data } = useSession();
  const loc = useLocation();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [pub, setPub] = useState(true);
  const [busy, setBusy] = useState(false);
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api("/api/comparisons", { method: "POST", body: { name: name.trim() || defaultName(run, models), providers: models, tier, metric, tags, public: pub } });
      toast.success("Comparison saved", { description: "Find it under Account, Saved comparisons." });
      setOpen(false);
    } catch (err) {
      toast.error("Could not save the comparison", { description: err instanceof ApiError && !err.unavailable ? err.message : "The account service did not answer. Try again in a minute." });
    } finally {
      setBusy(false);
    }
  };
  return data?.user ? (
        <Dialog
          open={open}
          onOpenChange={(o) => {
            setOpen(o);
            if (o) setName(defaultName(run, models));
          }}
          title="Save this comparison"
          description="Keeps the models, tier, metric and clip types. Open it again from your account."
          trigger={
            <Button size="sm">
              <Bookmark size={14} /> Save
            </Button>
          }
        >
          <form onSubmit={save} className="grid gap-4">
            <Field label="Name" htmlFor="cmp-name">
              <Input id="cmp-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} autoFocus />
            </Field>
            <div className="flex items-start justify-between gap-4">
              <span>
                <span id="cmp-public" className="block text-[13px] font-medium text-ink">
                  Public link
                </span>
                <span className="block text-[12px] text-ink-3">Anyone with the link can open it. Your name is never shown.</span>
              </span>
              <Switch checked={pub} onChange={setPub} labelledBy="cmp-public" />
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={busy}>
                {busy && <Loader2 size={14} className="animate-spin" />} Save
              </Button>
            </div>
          </form>
        </Dialog>
      ) : (
        <Link to={`/sign-in?next=${encodeURIComponent(loc.pathname + loc.search)}`} className={buttonClass("primary", "sm")}>
          <Bookmark size={14} /> Save
        </Link>
      );
}

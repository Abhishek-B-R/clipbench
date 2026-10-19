import { useEffect, useState } from "react";
import { api, ApiError } from "./api";
import { useSession } from "./auth";
import { toast } from "@/components/ui/Toast";

/** Watchlist state for one provider with an optimistic toggle (PUT/DELETE /api/watchlist/:id). */
export function useWatch(providerId: string) {
  const { data: session } = useSession();
  const signedIn = Boolean(session?.user);
  const [watching, setWatching] = useState<boolean | null>(null);
  useEffect(() => {
    if (!signedIn) return setWatching(null);
    let alive = true;
    api<{ providers: string[] }>("/api/watchlist").then(
      (r) => alive && setWatching(r.providers.includes(providerId)),
      () => alive && setWatching(false),
    );
    return () => {
      alive = false;
    };
  }, [signedIn, providerId]);
  const toggle = async () => {
    const next = !watching;
    setWatching(next);
    try {
      await api(`/api/watchlist/${encodeURIComponent(providerId)}`, { method: next ? "PUT" : "DELETE" });
      toast(next ? "Added to your watchlist" : "Removed from your watchlist");
    } catch (e) {
      setWatching(!next);
      toast.error("Could not update your watchlist", { description: e instanceof ApiError && !e.unavailable ? e.message : "The account service did not answer. Try again in a minute." });
    }
  };
  return { signedIn, watching, toggle };
}

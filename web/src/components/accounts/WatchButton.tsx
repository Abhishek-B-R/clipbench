import { Link } from "react-router";
import { Star } from "lucide-react";
import { useWatch } from "@/lib/watch";
import { Button, buttonClass } from "@/components/ui/Button";

/** Watch toggle for a model page. Lazy-loaded only when ACCOUNTS is on (it carries the Better Auth client). */
export default function WatchButton({ id }: { id: string }) {
  const { signedIn, watching, toggle } = useWatch(id);
  return signedIn ? (
    <Button variant={watching ? "secondary" : "primary"} size="sm" onClick={toggle} disabled={watching === null} aria-pressed={Boolean(watching)}>
      <Star size={14} className={watching ? "fill-[var(--warn)] text-warn" : ""} />
      {watching ? "Watching" : "Watch"}
    </Button>
  ) : (
    <Link to={`/sign-in?next=${encodeURIComponent(`/providers/${id}`)}`} className={buttonClass("primary", "sm")}>
      <Star size={14} /> Watch
    </Link>
  );
}

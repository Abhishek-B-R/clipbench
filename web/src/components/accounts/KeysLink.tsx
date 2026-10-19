import { Link } from "react-router";
import { useSession } from "@/lib/auth";
import { buttonClass } from "@/components/ui/Button";

/** "Manage your keys" or "Sign in to create a key" on the API docs. Lazy-loaded only when ACCOUNTS is on. */
export default function KeysLink() {
  const { data } = useSession();
  return (
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
  );
}

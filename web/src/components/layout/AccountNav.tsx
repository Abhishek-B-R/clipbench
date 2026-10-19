import { Link, useNavigate } from "react-router";
import * as Menu from "@radix-ui/react-dropdown-menu";
import { KeyRound, LogOut, Star, User } from "lucide-react";
import { signOut, useSession } from "@/lib/auth";
import { buttonClass } from "../ui/Button";
import { Avatar } from "../ui/Avatar";

/** Account controls for the nav. Lazy-loaded by Nav only when ACCOUNTS is on (they carry the Better Auth client). */
export function AccountButton() {
  const { data, isPending } = useSession();
  const navigate = useNavigate();
  const user = data?.user;
  if (isPending) return <span className="hidden h-8 w-16 sm:block" aria-hidden />;
  if (!user)
    return (
      <Link to="/sign-in" className={buttonClass("primary", "sm", "hidden sm:inline-flex")}>
        Sign in
      </Link>
    );
  const item = "flex h-8 cursor-pointer items-center gap-2.5 rounded-sm px-2 text-[13px] text-ink-2 outline-none data-[highlighted]:bg-sunken data-[highlighted]:text-ink";
  return (
    <Menu.Root>
      <Menu.Trigger className="hidden rounded-full sm:inline-flex" aria-label="Account menu">
        <Avatar src={user.image} name={user.name || user.email} size={28} />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content align="end" sideOffset={8} className="z-50 min-w-56 rounded-md border border-line bg-surface p-1.5 shadow-pop animate-fade-up">
          <div className="px-2 pt-1.5 pb-2">
            <div className="truncate text-[13px] font-medium text-ink">{user.name || "Signed in"}</div>
            <div className="truncate text-[12px] text-ink-3">{user.email}</div>
          </div>
          <Menu.Separator className="my-1 h-px bg-line" />
          <Menu.Item className={item} onSelect={() => navigate("/account")}>
            <User size={14} /> Account
          </Menu.Item>
          <Menu.Item className={item} onSelect={() => navigate("/account#watchlist")}>
            <Star size={14} /> Watchlist
          </Menu.Item>
          <Menu.Item className={item} onSelect={() => navigate("/account#keys")}>
            <KeyRound size={14} /> API keys
          </Menu.Item>
          <Menu.Separator className="my-1 h-px bg-line" />
          <Menu.Item className={item} onSelect={() => void signOut().then(() => navigate("/"))}>
            <LogOut size={14} /> Sign out
          </Menu.Item>
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}

/** Only mounted when ACCOUNTS is on, so a static build never calls /api/auth. */
export function MobileAccount() {
  const { data } = useSession();
  return data?.user ? (
    <Link to="/account" className={buttonClass("secondary", "lg", "flex-1")}>
      Account
    </Link>
  ) : (
    <Link to="/sign-in" className={buttonClass("primary", "lg", "flex-1")}>
      Sign in
    </Link>
  );
}

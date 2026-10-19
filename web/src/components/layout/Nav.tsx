import { useEffect, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router";
import * as Menu from "@radix-ui/react-dropdown-menu";
import * as Sheet from "@radix-ui/react-dialog";
import { KeyRound, LogOut, Menu as MenuIcon, Search, Star, User, X } from "lucide-react";
import { cn } from "@/lib/cn";
import { signOut, useSession } from "@/lib/auth";
import { GITHUB_URL } from "@/lib/config";
import { ACCOUNTS } from "@/lib/flags";
import { Wordmark } from "./Logo";
import { buttonClass } from "../ui/Button";
import { Kbd, modKey } from "../ui/Kbd";
import { ThemeToggle } from "../ui/ThemeToggle";
import { Avatar } from "../ui/Avatar";
import { GitHubIcon } from "../ui/BrandIcons";
import { setCommandMenuOpen } from "../ui/CommandMenu";

export const NAV_LINKS = [
  { to: "/leaderboard", label: "Leaderboard" },
  { to: "/compare", label: "Compare" },
  { to: "/picker", label: "Picker" },
  { to: "/clips", label: "Clips" },
  { to: "/methodology", label: "Methodology" },
  ...(ACCOUNTS ? [{ to: "/requests", label: "Requests" }] : []),
  { to: "/docs/api", label: "Docs" },
];

/**
 * Top navigation. Sticky, translucent paper with a hairline that appears once the page scrolls.
 * Desktop: wordmark, links, search (Cmd+K), GitHub, theme, account. Phone: wordmark, search, menu sheet.
 */
export function Nav() {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 8);
    on();
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);

  return (
    <header
      className={cn(
        "sticky top-0 z-40 border-b transition-[background-color,border-color] duration-300",
        scrolled ? "border-line bg-paper/82 backdrop-blur-md backdrop-saturate-150" : "border-transparent bg-paper",
      )}
    >
      <div className="frame flex h-14 items-center gap-6">
        <Link to="/" aria-label="clipbench home" className="shrink-0 rounded-sm">
          <Wordmark />
        </Link>
        <nav aria-label="Main" className="hidden items-center gap-0.5 lg:flex">
          {NAV_LINKS.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              className={({ isActive }) =>
                cn(
                  "rounded-sm px-2.5 py-1.5 text-[13.5px] transition-colors",
                  isActive ? "text-ink" : "text-ink-2 hover:text-ink",
                )
              }
            >
              {l.label}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setCommandMenuOpen(true)}
            className="hidden h-8 items-center gap-2 rounded-sm border border-line bg-surface pr-1.5 pl-2.5 text-[13px] text-ink-3 transition-colors hover:border-line-strong hover:text-ink-2 md:inline-flex"
          >
            <Search size={14} aria-hidden />
            <span className="pr-6">Search</span>
            <span className="inline-flex gap-0.5">
              <Kbd>{modKey}</Kbd>
              <Kbd>K</Kbd>
            </span>
          </button>
          <button
            type="button"
            aria-label="Open command menu"
            onClick={() => setCommandMenuOpen(true)}
            className="grid size-8 place-items-center rounded-sm text-ink-2 hover:bg-sunken hover:text-ink md:hidden"
          >
            <Search size={16} />
          </button>
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noreferrer"
            aria-label="clipbench on GitHub"
            className="hidden size-8 place-items-center rounded-sm text-ink-2 hover:bg-sunken hover:text-ink sm:grid"
          >
            <GitHubIcon size={16} />
          </a>
          <ThemeToggle />
          {ACCOUNTS && (
            <>
              <span aria-hidden className="mx-1 hidden h-5 w-px bg-line sm:block" />
              <AccountButton />
            </>
          )}
          <MobileMenu />
        </div>
      </div>
    </header>
  );
}

function AccountButton() {
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

function MobileMenu() {
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  return (
    <Sheet.Root open={open} onOpenChange={setOpen}>
      <Sheet.Trigger className="grid size-8 place-items-center rounded-sm text-ink-2 hover:bg-sunken hover:text-ink lg:hidden" aria-label="Open menu">
        <MenuIcon size={18} />
      </Sheet.Trigger>
      <Sheet.Portal>
        <Sheet.Overlay className="fixed inset-0 z-50 bg-[var(--scrim)]" />
        <Sheet.Content className="fixed inset-x-0 top-0 z-50 border-b border-line bg-paper pb-6 shadow-pop animate-fade-up focus:outline-none">
          <Sheet.Title className="sr-only">Menu</Sheet.Title>
          <div className="frame flex h-14 items-center justify-between">
            <Wordmark />
            <Sheet.Close className="grid size-8 place-items-center rounded-sm text-ink-2 hover:bg-sunken" aria-label="Close menu">
              <X size={18} />
            </Sheet.Close>
          </div>
          <nav aria-label="Mobile" className="frame grid">
            {[{ to: "/", label: "Overview" }, ...NAV_LINKS, { to: "/changelog", label: "Changelog" }].map((l) => (
              <NavLink
                key={l.to}
                to={l.to}
                end
                className={({ isActive }) => cn("flex h-12 items-center border-b border-line text-[16px]", isActive ? "text-ink" : "text-ink-2")}
              >
                {l.label}
              </NavLink>
            ))}
          </nav>
          <div className="frame mt-5 flex gap-2">
            {ACCOUNTS && <MobileAccount />}
            <a href={GITHUB_URL} target="_blank" rel="noreferrer" className={buttonClass("secondary", "lg")}>
              <GitHubIcon /> GitHub
            </a>
          </div>
        </Sheet.Content>
      </Sheet.Portal>
    </Sheet.Root>
  );
}

/** Only mounted when ACCOUNTS is on, so a static build never calls /api/auth. */
function MobileAccount() {
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

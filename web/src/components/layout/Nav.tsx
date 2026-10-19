import { lazy, Suspense, useEffect, useState } from "react";
import { Link, NavLink, useLocation } from "react-router";
import * as Sheet from "@radix-ui/react-dialog";
import { Menu as MenuIcon, Search, X } from "lucide-react";
import { cn } from "@/lib/cn";
import { GITHUB_URL } from "@/lib/config";
import { ACCOUNTS } from "@/lib/flags";
import { Wordmark } from "./Logo";
import { buttonClass } from "../ui/Button";
import { Kbd, modKey } from "../ui/Kbd";
import { ThemeToggle } from "../ui/ThemeToggle";
import { GitHubIcon } from "../ui/BrandIcons";
import { setCommandMenuOpen } from "../ui/commandMenuStore";

// The account controls pull in the Better Auth client. They load as their own chunk, and only when ACCOUNTS is on,
// so the public build never ships or runs it.
const AccountButton = ACCOUNTS ? lazy(() => import("./AccountNav").then((m) => ({ default: m.AccountButton }))) : null;
const MobileAccount = ACCOUNTS ? lazy(() => import("./AccountNav").then((m) => ({ default: m.MobileAccount }))) : null;

export const NAV_LINKS = [
  { to: "/leaderboard", label: "Leaderboard" },
  { to: "/streaming", label: "Streaming" },
  { to: "/compare", label: "Compare" },
  { to: "/picker", label: "Picker" },
  { to: "/features", label: "Features" },
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
          {AccountButton && (
            <>
              <span aria-hidden className="mx-1 hidden h-5 w-px bg-line sm:block" />
              <Suspense fallback={<span className="hidden h-8 w-16 sm:block" aria-hidden />}>
                <AccountButton />
              </Suspense>
            </>
          )}
          <MobileMenu />
        </div>
      </div>
    </header>
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
            {MobileAccount && (
              <Suspense fallback={null}>
                <MobileAccount />
              </Suspense>
            )}
            <a href={GITHUB_URL} target="_blank" rel="noreferrer" className={buttonClass("secondary", "lg")}>
              <GitHubIcon /> GitHub
            </a>
          </div>
        </Sheet.Content>
      </Sheet.Portal>
    </Sheet.Root>
  );
}

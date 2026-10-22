import { Command } from "cmdk";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import {
  ArrowRight,
  BookOpen,
  Code2,
  Copy,
  FlaskConical,
  GitCompareArrows,
  History,
  LayoutList,
  LogIn,
  Play,
  Moon,
  Search,
  Sparkles,
  Trophy,
  User,
  Vote,
  KeyRound,
  Home,
  FlaskRound,
  Globe,
  Radio,
  Table2,
} from "lucide-react";
import { setDemo, useRun, useSource } from "@/lib/data";
import { setTheme, useTheme } from "@/lib/theme";
import { LANGUAGE_NAME, excludedProviders, isEligible, providerColor, providerLabel } from "@/lib/analysis";
import { pct } from "@/lib/format";
import { LANGUAGES, REPO_SLUG } from "@/lib/config";
import { ACCOUNTS } from "@/lib/flags";
import { SeriesDot } from "./Badge";
import { Kbd } from "./Kbd";
import { toast } from "./Toast";
import { setCommandMenuOpen, useCommandMenuOpen } from "./commandMenuStore";

const PAGES = [
  { to: "/try", label: "Try it", icon: Play, hint: "Drop a clip, compare systems side by side" },
  { to: "/leaderboard", label: "Leaderboard", icon: Trophy, hint: "Every model ranked, with intervals" },
  { to: "/early", label: "Early look", icon: FlaskConical, hint: "First run on creator captions, not a ranking" },
  { to: "/streaming", label: "Streaming leaderboard", icon: Radio, hint: "Realtime APIs: first partial, final segment, stability" },
  { to: "/features", label: "Feature matrix", icon: Table2, hint: "What each provider offers, with doc links" },
  { to: "/compare", label: "Compare", icon: GitCompareArrows, hint: "Pick models, clip types and a metric" },
  { to: "/picker", label: "Pick a provider", icon: Sparkles, hint: "Which speech-to-text for my app" },
  { to: "/clips", label: "Clips", icon: LayoutList, hint: "Browse the clip set" },
  { to: "/methodology", label: "Methodology", icon: FlaskConical, hint: "How scoring works" },
  ...(ACCOUNTS ? [{ to: "/requests", label: "Requests", icon: Vote, hint: "Vote for the next model or language" }] : []),
  { to: "/changelog", label: "Changelog", icon: History, hint: "Runs over time" },
  { to: "/docs/api", label: "API docs", icon: Code2, hint: "Public data API" },
  ...(ACCOUNTS
    ? [
        { to: "/account", label: "Account", icon: User, hint: "Watchlist, alerts, saved comparisons" },
        { to: "/account#keys", label: "API keys", icon: KeyRound, hint: "Create or revoke a key" },
      ]
    : []),
  ...LANGUAGES.map((l) => ({ to: `/lang/${l}`, label: LANGUAGE_NAME[l] ?? l, icon: Globe, hint: "Progress and results for this language" })),
  ...LANGUAGES.map((l) => ({ to: `/findings/${l}`, label: `${LANGUAGE_NAME[l] ?? l} findings`, icon: Sparkles, hint: "Shareable findings, each linked to its table" })),
  { to: "/", label: "Overview", icon: Home, hint: "The landing page" },
];

/**
 * Cmd+K / Ctrl+K command menu (cmdk). Groups: Pages, Models (from the latest run), Actions.
 * Footer shows the key legend (Supabase / Vapi pattern). Loaded by CommandMenuHost on the first open.
 */
export default function CommandMenu() {
  const isOpen = useCommandMenuOpen();
  const navigate = useNavigate();
  const { resolved } = useTheme();
  const run = useRun();
  const src = useSource();
  const [q, setQ] = useState("");

  useEffect(() => {
    if (!isOpen) setQ("");
  }, [isOpen]);

  const go = (to: string) => {
    setCommandMenuOpen(false);
    navigate(to);
  };

  const item =
    "flex h-10 cursor-pointer items-center gap-3 rounded-sm px-2.5 text-[13.5px] text-ink-2 data-[selected=true]:bg-sunken data-[selected=true]:text-ink";

  return (
    <Command.Dialog
      open={isOpen}
      onOpenChange={setCommandMenuOpen}
      label="Command menu"
      overlayClassName="fixed inset-0 z-50 bg-[var(--scrim)] backdrop-blur-[2px]"
      contentClassName="fixed top-[14vh] left-1/2 z-50 w-[calc(100vw-24px)] max-w-[600px] -translate-x-1/2 overflow-hidden rounded-md border border-line bg-surface shadow-pop animate-fade-up"
    >
      <div className="flex items-center gap-2.5 border-b border-line px-4">
        <Search size={16} className="text-ink-3" aria-hidden />
        <Command.Input
          value={q}
          onValueChange={setQ}
          placeholder="Search pages, models and actions"
          className="h-12 flex-1 bg-transparent text-[15px] text-ink placeholder:text-ink-3 focus:outline-none"
        />
        <Kbd>esc</Kbd>
      </div>
      <Command.List className="max-h-[min(420px,60vh)] overflow-y-auto p-2 [&_[cmdk-group-heading]]:label [&_[cmdk-group-heading]]:px-2.5 [&_[cmdk-group-heading]]:pt-2 [&_[cmdk-group-heading]]:pb-1.5">
        <Command.Empty className="px-3 py-8 text-center text-[13px] text-ink-3">Nothing matches "{q}".</Command.Empty>
        <Command.Group heading="Pages">
          {PAGES.map((p) => (
            <Command.Item key={p.to} value={`${p.label} ${p.hint}`} onSelect={() => go(p.to)} className={item}>
              <p.icon size={16} strokeWidth={1.75} className="text-ink-3" aria-hidden />
              <span className="text-ink">{p.label}</span>
              <span className="truncate text-[12.5px] text-ink-3">{p.hint}</span>
              <ArrowRight size={14} className="ml-auto opacity-0 [[data-selected=true]_&]:opacity-60" aria-hidden />
            </Command.Item>
          ))}
        </Command.Group>
        {run.data && (
          <Command.Group heading="Models">
            {run.data.run.providers
              .filter((p) => isEligible(run.data.run, p.id))
              .map((p) => (
                <Command.Item key={p.id} value={`${p.label} ${p.model_id}`} onSelect={() => go(`/providers/${p.id}`)} className={item}>
                  <SeriesDot color={providerColor(run.data.run, p.id)} />
                  <span className="text-ink">{p.label}</span>
                  <span className="font-mono text-[12px] text-ink-3">{p.model_id}</span>
                </Command.Item>
              ))}
          </Command.Group>
        )}
        {run.data && excludedProviders(run.data.run).length > 0 && (
          <Command.Group heading="Not ranked">
            {excludedProviders(run.data.run).map((e) => (
              <Command.Item key={e.provider} value={`${providerLabel(run.data.run, e.provider)} not ranked`} onSelect={() => go(`/providers/${e.provider}`)} className={item}>
                <SeriesDot color="var(--ink-3)" />
                <span className="text-ink">{providerLabel(run.data.run, e.provider)}</span>
                <span className="num text-[12px] text-ink-3">coverage {pct(e.coverage)}</span>
              </Command.Item>
            ))}
          </Command.Group>
        )}
        <Command.Group heading="Actions">
          <Command.Item
            value="toggle theme dark light"
            onSelect={() => {
              setTheme(resolved === "dark" ? "light" : "dark");
              setCommandMenuOpen(false);
            }}
            className={item}
          >
            <Moon size={16} strokeWidth={1.75} className="text-ink-3" aria-hidden />
            <span className="text-ink">Switch to {resolved === "dark" ? "light" : "dark"} theme</span>
          </Command.Item>
          <Command.Item
            value="copy clone command reproduce"
            onSelect={() => {
              void navigator.clipboard?.writeText(`git clone https://github.com/${REPO_SLUG}`);
              toast("Clone command copied");
              setCommandMenuOpen(false);
            }}
            className={item}
          >
            <Copy size={16} strokeWidth={1.75} className="text-ink-3" aria-hidden />
            <span className="text-ink">Copy the clone command</span>
          </Command.Item>
          {import.meta.env.DEV && src.data && (
            <Command.Item
              value="demo data synthetic toggle real results"
              onSelect={() => {
                setCommandMenuOpen(false);
                setDemo(!src.data!.forced);
              }}
              className={item}
            >
              <FlaskRound size={16} strokeWidth={1.75} className="text-ink-3" aria-hidden />
              <span className="text-ink">{src.data.forced ? "Back to real results" : "View the demo data"}</span>
            </Command.Item>
          )}
          <Command.Item value="read methodology docs" onSelect={() => go("/methodology")} className={item}>
            <BookOpen size={16} strokeWidth={1.75} className="text-ink-3" aria-hidden />
            <span className="text-ink">Read how scoring works</span>
          </Command.Item>
          {ACCOUNTS && (
            <Command.Item value="sign in account" onSelect={() => go("/sign-in")} className={item}>
              <LogIn size={16} strokeWidth={1.75} className="text-ink-3" aria-hidden />
              <span className="text-ink">Sign in</span>
            </Command.Item>
          )}
        </Command.Group>
      </Command.List>
      <div className="flex items-center gap-4 border-t border-line bg-sunken/60 px-4 py-2 text-[11.5px] text-ink-3">
        <span className="inline-flex items-center gap-1.5">
          <Kbd>↑</Kbd>
          <Kbd>↓</Kbd> navigate
        </span>
        <span className="inline-flex items-center gap-1.5">
          <Kbd>↵</Kbd> open
        </span>
        <span className="ml-auto inline-flex items-center gap-1.5">
          <Kbd>esc</Kbd> close
        </span>
      </div>
    </Command.Dialog>
  );
}

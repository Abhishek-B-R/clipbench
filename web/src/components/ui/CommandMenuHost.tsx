import { lazy, Suspense, useEffect } from "react";
import { isCommandMenuOpen, setCommandMenuOpen, useCommandMenuWanted } from "./commandMenuStore";

const CommandMenu = lazy(() => import("./CommandMenu"));

/** Mounted once in the shell: owns the Cmd/Ctrl+K shortcut and loads the menu (cmdk) the first time it opens. */
export function CommandMenuHost() {
  const wanted = useCommandMenuWanted();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCommandMenuOpen(!isCommandMenuOpen());
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  if (!wanted) return null;
  return (
    <Suspense fallback={null}>
      <CommandMenu />
    </Suspense>
  );
}

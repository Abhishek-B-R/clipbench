import { Moon, Sun } from "lucide-react";
import { useTheme } from "@/lib/theme";

/**
 * Switches light/dark. The choice wins over the OS setting both ways and persists.
 * A native title instead of the Radix tooltip: this button is on every page, and the tooltip would load popper code with it.
 */
export function ThemeToggle() {
  const { resolved, cycle } = useTheme();
  const next = resolved === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={cycle}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      className="grid size-8 place-items-center rounded-sm text-ink-2 transition-colors hover:bg-sunken hover:text-ink"
    >
      {resolved === "dark" ? <Sun size={16} strokeWidth={1.75} /> : <Moon size={16} strokeWidth={1.75} />}
    </button>
  );
}

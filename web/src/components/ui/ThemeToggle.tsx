import { Moon, Sun } from "lucide-react";
import { useTheme } from "@/lib/theme";
import { Tooltip } from "./Tooltip";

/** Switches light/dark. The choice wins over the OS setting both ways and persists. */
export function ThemeToggle() {
  const { resolved, cycle } = useTheme();
  const next = resolved === "dark" ? "light" : "dark";
  return (
    <Tooltip content={`Switch to ${next} theme`} side="bottom">
      <button
        type="button"
        onClick={cycle}
        aria-label={`Switch to ${next} theme`}
        className="grid size-8 place-items-center rounded-sm text-ink-2 transition-colors hover:bg-sunken hover:text-ink"
      >
        {resolved === "dark" ? <Sun size={16} strokeWidth={1.75} /> : <Moon size={16} strokeWidth={1.75} />}
      </button>
    </Tooltip>
  );
}

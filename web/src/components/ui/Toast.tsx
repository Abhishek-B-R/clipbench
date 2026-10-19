import { lazy, Suspense, useSyncExternalStore } from "react";
import type { ExternalToast } from "sonner";
import { useTheme } from "@/lib/theme";

// sonner loads on demand, outside the main chunk: toasts only follow a user action, and sonner replays toasts
// created before its Toaster subscribes, so nothing is lost while the chunk loads.
let mod: Promise<typeof import("sonner")> | null = null;
const load = () => (mod ??= import("sonner"));

const Sonner = lazy(() => load().then((m) => ({ default: m.Toaster })));

// The Toaster mounts on the first toast, so a visit that never toasts never downloads sonner.
let used = false;
const subs = new Set<() => void>();
function markUsed() {
  if (used) return;
  used = true;
  subs.forEach((s) => s());
}
const useUsed = () =>
  useSyncExternalStore(
    (cb) => {
      subs.add(cb);
      return () => subs.delete(cb);
    },
    () => used,
    () => false,
  );

/** Mount once in the shell. Call `toast("Copied")`, `toast.success(...)`, `toast.error(...)` anywhere. */
export function Toaster() {
  const { resolved } = useTheme();
  if (!useUsed()) return null;
  return (
    <Suspense fallback={null}>
      <Sonner
        theme={resolved}
        position="bottom-right"
        gap={8}
        toastOptions={{
          classNames: {
            toast: "!rounded-md !border !border-line !bg-surface !text-ink !shadow-pop !font-sans !text-[13.5px]",
            description: "!text-ink-2",
          },
        }}
      />
    </Suspense>
  );
}

type Message = string;
const send = (kind?: "success" | "error") => (message: Message, data?: ExternalToast) => {
  markUsed();
  void load().then((m) => (kind ? m.toast[kind] : m.toast)(message, data));
};

export const toast = Object.assign(send(), { success: send("success"), error: send("error") });

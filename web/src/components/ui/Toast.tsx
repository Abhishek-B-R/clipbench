import { Toaster as Sonner, toast } from "sonner";
import { useTheme } from "@/lib/theme";

/** Mount once in the shell. Call `toast("Copied")`, `toast.success(...)`, `toast.error(...)` anywhere. */
export function Toaster() {
  const { resolved } = useTheme();
  return (
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
  );
}

export { toast };

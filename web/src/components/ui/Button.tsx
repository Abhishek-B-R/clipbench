import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "signal";
export type ButtonSize = "sm" | "md" | "lg" | "icon";

const base =
  "inline-flex items-center justify-center gap-2 whitespace-nowrap font-medium select-none " +
  "transition-[background-color,color,border-color,box-shadow,transform] duration-150 ease-out " +
  "active:translate-y-px disabled:pointer-events-none disabled:opacity-45 " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus";

const variants: Record<ButtonVariant, string> = {
  primary: "bg-ink text-paper hover:bg-ink/88 shadow-[inset_0_1px_0_oklch(1_0_0/0.12)]",
  secondary: "bg-surface text-ink border border-line-strong hover:border-ink-3 hover:bg-sunken",
  ghost: "text-ink-2 hover:text-ink hover:bg-sunken",
  signal: "bg-signal text-white hover:bg-signal-ink",
};

const sizes: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-[13px] rounded-sm",
  md: "h-9 px-3.5 text-[14px] rounded-sm",
  lg: "h-11 px-5 text-[15px] rounded-md",
  icon: "size-8 rounded-sm",
};

/** Class string for anything that should look like a button (links included). */
export function buttonClass(variant: ButtonVariant = "primary", size: ButtonSize = "md", extra?: string) {
  return cn(base, variants[variant], sizes[size], extra);
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", size = "md", className, type = "button", ...props },
  ref,
) {
  return <button ref={ref} type={type} className={buttonClass(variant, size, className)} {...props} />;
});

import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input({ className, invalid, ...props }, ref) {
  return (
    <input
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(
        "h-10 w-full rounded-sm border bg-surface px-3 text-[14px] text-ink placeholder:text-ink-3",
        "border-line-strong transition-[border-color,box-shadow] duration-150",
        "hover:border-ink-3 focus:border-ink focus:outline-none focus:ring-[3px] focus:ring-signal/20",
        invalid && "border-signal focus:border-signal",
        className,
      )}
      {...props}
    />
  );
});

export function Field({ label, hint, error, children, htmlFor }: { label: string; hint?: string; error?: string; children: React.ReactNode; htmlFor: string }) {
  return (
    <div className="grid gap-1.5">
      <label htmlFor={htmlFor} className="text-[13px] font-medium text-ink">
        {label}
      </label>
      {children}
      {error ? (
        <p role="alert" className="text-[12px] text-signal-ink">
          {error}
        </p>
      ) : hint ? (
        <p className="text-[12px] text-ink-3">{hint}</p>
      ) : null}
    </div>
  );
}

import { useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/cn";
import { toast } from "./Toast";

export interface CodeSample {
  /** Tab label, e.g. "curl", "JavaScript", "Python", "200". */
  label: string;
  code: string;
  lang?: "shell" | "js" | "python" | "json";
}

/**
 * Code panel in the same ink "terminal" as the landing page's Reproduce block, with language tabs (Resend /
 * GitBook pattern), a copy button and light syntax colour (strings, comments, keys, numbers). Always dark.
 */
export function CodeBlock({ samples, title, className }: { samples: CodeSample[]; title?: ReactNode; className?: string }) {
  const [i, setI] = useState(0);
  const [copied, setCopied] = useState(false);
  const cur = samples[i] ?? samples[0]!;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(cur.code);
      setCopied(true);
      toast("Copied to clipboard");
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Could not copy. Select the text instead.");
    }
  };
  return (
    <div
      className={cn("overflow-hidden rounded-md border border-[oklch(0.3_0.005_70)] bg-[oklch(0.17_0.005_70)] text-[oklch(0.93_0.005_85)] shadow-card", className)}
    >
      <div className="flex h-10 items-center gap-1 border-b border-[oklch(0.27_0.005_70)] pr-1.5 pl-2">
        {title && <span className="mr-2 truncate pl-1.5 font-mono text-[11.5px] text-[oklch(0.64_0.006_85)]">{title}</span>}
        <div role="tablist" aria-label="Language" className="flex min-w-0 gap-0.5 overflow-x-auto scrollbar-none">
          {samples.length > 1 &&
            samples.map((s, k) => (
              <button
                key={s.label}
                role="tab"
                type="button"
                aria-selected={k === i}
                onClick={() => setI(k)}
                className={cn(
                  "h-7 shrink-0 rounded-sm px-2 font-mono text-[11.5px] transition-colors",
                  k === i ? "bg-[oklch(0.25_0.005_70)] text-white" : "text-[oklch(0.64_0.006_85)] hover:text-white",
                )}
              >
                {s.label}
              </button>
            ))}
        </div>
        <button
          type="button"
          onClick={copy}
          aria-label="Copy code"
          className="ml-auto grid size-7 shrink-0 place-items-center rounded-sm text-[oklch(0.72_0.006_85)] hover:bg-[oklch(0.24_0.005_70)] hover:text-white"
        >
          {copied ? <Check size={13} /> : <Copy size={13} />}
        </button>
      </div>
      <pre tabIndex={0} className="overflow-x-auto p-4 font-mono text-[12.5px] leading-[1.7]">
        <code>{highlight(cur.code, cur.lang ?? "shell")}</code>
      </pre>
    </div>
  );
}

const C = {
  str: "text-[oklch(0.8_0.11_150)]",
  com: "text-[oklch(0.64_0.006_85)]",
  key: "text-[oklch(0.8_0.09_255)]",
  num: "text-[oklch(0.8_0.12_75)]",
  kw: "text-[oklch(0.76_0.15_36)]",
};

/** Tiny tokenizer: good enough for short examples, never used on user input. */
function highlight(code: string, lang: CodeSample["lang"]): ReactNode[] {
  const re =
    lang === "json"
      ? /("(?:[^"\\]|\\.)*")(\s*:)?|(-?\b\d+(?:\.\d+)?\b)|\b(true|false|null)\b/g
      : lang === "python"
        ? /(#.*$)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|\b(import|from|as|for|in|print|def|return|with|if|else)\b|(\b\d+(?:\.\d+)?\b)/gm
        : lang === "js"
          ? /(\/\/.*$)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)|\b(const|let|await|async|for|of|return|new|if|else|function)\b|(\b\d+(?:\.\d+)?\b)/gm
          : /(#.*$)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|(^|\s)(curl|-H|-s|-i|jq|export)\b/gm;
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(code))) {
    if (m.index > last) out.push(code.slice(last, m.index));
    if (lang === "json") {
      if (m[1]) out.push(<span key={k++} className={m[2] ? C.key : C.str}>{m[1]}</span>, m[2] ?? "");
      else if (m[3]) out.push(<span key={k++} className={C.num}>{m[3]}</span>);
      else out.push(<span key={k++} className={C.kw}>{m[4]}</span>);
    } else if (lang === "shell") {
      if (m[1]) out.push(<span key={k++} className={C.com}>{m[1]}</span>);
      else if (m[2]) out.push(<span key={k++} className={C.str}>{m[2]}</span>);
      else out.push(m[3] ?? "", <span key={k++} className={C.kw}>{m[4]}</span>);
    } else {
      if (m[1]) out.push(<span key={k++} className={C.com}>{m[1]}</span>);
      else if (m[2]) out.push(<span key={k++} className={C.str}>{m[2]}</span>);
      else if (m[3]) out.push(<span key={k++} className={C.kw}>{m[3]}</span>);
      else out.push(<span key={k++} className={C.num}>{m[4]}</span>);
    }
    last = m.index + m[0].length;
    if (m[0].length === 0) re.lastIndex++;
  }
  if (last < code.length) out.push(code.slice(last));
  return out;
}

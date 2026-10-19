import { Fragment, type ReactNode } from "react";
import { GITHUB_URL } from "./config";

/**
 * A small Markdown renderer for METHODOLOGY.md (and nothing else): headings, paragraphs, nested lists,
 * ordered lists with continuation lines, pipe tables, `code`, **bold**, [links](...). No HTML passthrough.
 */
export type Block =
  | { type: "h"; level: number; text: string; id: string }
  | { type: "p"; text: string }
  | { type: "list"; ordered: boolean; items: ListItem[] }
  | { type: "table"; head: string[]; rows: string[][] };
export interface ListItem {
  text: string;
  children: Block[];
}

export function slug(s: string) {
  return s
    .toLowerCase()
    .replace(/`/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

export function parse(md: string): Block[] {
  const lines = md.replace(/\r/g, "").split("\n");
  return parseLines(lines);
}

function indentOf(l: string) {
  return l.length - l.trimStart().length;
}

function parseLines(lines: string[]): Block[] {
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    if (!line.trim()) {
      i++;
      continue;
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      out.push({ type: "h", level: h[1]!.length, text: h[2]!.trim(), id: slug(h[2]!) });
      i++;
      continue;
    }
    if (line.trim().startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i]!.trim().startsWith("|")) {
        const cells = lines[i]!.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(cells);
        i++;
      }
      out.push({ type: "table", head: rows[0] ?? [], rows: rows.slice(1) });
      continue;
    }
    const li = /^(\s*)([-*]|\d+\.)\s+(.*)$/.exec(line);
    if (li) {
      const base = li[1]!.length;
      const ordered = /\d/.test(li[2]!);
      const items: ListItem[] = [];
      while (i < lines.length) {
        const m = /^(\s*)([-*]|\d+\.)\s+(.*)$/.exec(lines[i]!);
        if (!m || m[1]!.length !== base || /\d/.test(m[2]!) !== ordered) break;
        let text = m[3]!;
        i++;
        const sub: string[] = [];
        // continuation: indented lines (or blank lines followed by indented lines)
        while (i < lines.length) {
          const l = lines[i]!;
          if (!l.trim()) {
            const next = lines[i + 1];
            if (next != null && next.trim() && indentOf(next) > base) {
              sub.push("");
              i++;
              continue;
            }
            break;
          }
          if (indentOf(l) <= base) break;
          if (/^\s*([-*]|\d+\.)\s+/.test(l) || sub.length) sub.push(l.slice(Math.min(indentOf(l), base + 2)));
          else text += " " + l.trim();
          i++;
        }
        items.push({ text, children: sub.length ? parseLines(dedent(sub)) : [] });
      }
      out.push({ type: "list", ordered, items });
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i]!.trim() && !/^(#{1,6}\s|\s*\||\s*([-*]|\d+\.)\s)/.test(lines[i]!)) {
      para.push(lines[i]!.trim());
      i++;
    }
    out.push({ type: "p", text: para.join(" ") });
  }
  return out;
}

function dedent(ls: string[]) {
  const ind = Math.min(...ls.filter((l) => l.trim()).map(indentOf));
  return ls.map((l) => l.slice(ind));
}

/** Inline: `code`, **bold**, [text](href). Repo-relative links point at the repository on GitHub. */
export function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)]+)\)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1] != null)
      out.push(
        <code key={k++} className="rounded-[4px] border border-line bg-sunken px-1 py-px font-mono text-[0.86em] text-ink">
          {m[1]}
        </code>,
      );
    else if (m[2] != null)
      out.push(
        <strong key={k++} className="font-semibold text-ink">
          {inline(m[2])}
        </strong>,
      );
    else {
      const href = /^https?:/.test(m[4]!) ? m[4]! : `${GITHUB_URL}/blob/main/${m[4]!.replace(/^\.?\//, "")}`;
      out.push(
        <a key={k++} href={href} target="_blank" rel="noreferrer" className="text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink">
          {inline(m[3]!)}
        </a>,
      );
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out.map((n, i) => <Fragment key={i}>{n}</Fragment>);
}

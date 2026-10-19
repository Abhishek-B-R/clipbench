import { useMemo, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { cn } from "@/lib/cn";

export interface Column<T> {
  key: string;
  header: ReactNode;
  cell: (row: T, index: number) => ReactNode;
  /** Value used for sorting. Omit to make the column unsortable. */
  sort?: (row: T) => number | string | null;
  align?: "left" | "right" | "center";
  className?: string;
  headerClassName?: string;
  /** Stick to the left edge while the table scrolls sideways (first column on phones). */
  sticky?: boolean;
  /** Help text shown under the header label for screen readers. */
  description?: string;
}

/**
 * Sortable data table. Real <table> semantics, aria-sort on the active header, header buttons are
 * keyboard reachable. Numbers right-aligned in mono. Wide tables scroll inside their own box, never the page.
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  initialSort,
  caption,
  dense = false,
  highlight,
  onRowClick,
  className,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  initialSort?: { key: string; dir: "asc" | "desc" };
  caption?: string;
  dense?: boolean;
  highlight?: (row: T) => boolean;
  onRowClick?: (row: T) => void;
  className?: string;
}) {
  const [sort, setSort] = useState(initialSort);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sort) return rows;
    const get = col.sort;
    return [...rows].sort((a, b) => {
      const va = get(a);
      const vb = get(b);
      if (va == null) return 1;
      if (vb == null) return -1;
      const r = typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb));
      return sort.dir === "asc" ? r : -r;
    });
  }, [rows, columns, sort]);

  const toggle = (key: string) =>
    setSort((s) => (s?.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));

  return (
    <div className={cn("relative overflow-x-auto", className)}>
      <table className="w-full border-separate border-spacing-0 text-[13.5px]">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr>
            {columns.map((c) => {
              const active = sort?.key === c.key;
              const ariaSort = active ? (sort!.dir === "asc" ? "ascending" : "descending") : c.sort ? "none" : undefined;
              const Icon = active ? (sort!.dir === "asc" ? ArrowUp : ArrowDown) : ChevronsUpDown;
              return (
                <th
                  key={c.key}
                  scope="col"
                  aria-sort={ariaSort}
                  className={cn(
                    "border-b border-line bg-surface px-3 py-2 text-left align-bottom font-normal whitespace-nowrap first:pl-4 last:pr-4 sm:first:pl-5 sm:last:pr-5",
                    c.align === "right" && "text-right",
                    c.align === "center" && "text-center",
                    c.sticky && "sticky left-0 z-10",
                    c.headerClassName,
                  )}
                >
                  {c.sort ? (
                    <button
                      type="button"
                      onClick={() => toggle(c.key)}
                      className={cn("label inline-flex items-center gap-1 hover:text-ink", active && "text-ink", c.align === "right" && "flex-row-reverse")}
                    >
                      {c.header}
                      <Icon size={12} aria-hidden className={active ? "opacity-100" : "opacity-40"} />
                    </button>
                  ) : (
                    <span className="label">{c.header}</span>
                  )}
                  {c.description && <span className="sr-only">{c.description}</span>}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => {
            const hi = highlight?.(row);
            return (
              <tr
                key={rowKey(row)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={cn("group", onRowClick && "cursor-pointer")}
              >
                {columns.map((c) => (
                  <td
                    key={c.key}
                    className={cn(
                      "border-b border-line px-3 whitespace-nowrap first:pl-4 last:pr-4 sm:first:pl-5 sm:last:pr-5",
                      dense ? "py-2" : "py-3",
                      "bg-surface transition-colors group-hover:bg-sunken/60",
                      hi && "bg-signal-soft/40",
                      c.align === "right" && "text-right",
                      c.align === "center" && "text-center",
                      c.sticky && "sticky left-0 z-[1]",
                      c.className,
                    )}
                  >
                    {c.cell(row, i)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

"use client";

import { ArrowUpDown, ChevronDown, ChevronUp, ChevronsUpDown, Filter, Inbox, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { CellText } from "@/components/cell-text";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectItem,
  SelectPopup,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Toolbar, ToolbarButton, ToolbarGroup, ToolbarSeparator } from "@/components/ui/toolbar";
import { display, text } from "@/lib/format";
import { cn } from "@/lib/utils";

export type Column<T> = {
  key: string;
  header: string;
  width?: string;
  align?: "left" | "right";
  mono?: boolean;
  render?: (row: T) => React.ReactNode;
  value?: (row: T) => unknown;
};

type SortState = { key: string; dir: "asc" | "desc" } | null;

const PAGE = 25;

function isEmpty(value: unknown): boolean {
  return value == null || value === "";
}

function compareValues(a: unknown, b: unknown): number {
  const aNum = typeof a === "number" ? a : typeof a === "boolean" ? Number(a) : null;
  const bNum = typeof b === "number" ? b : typeof b === "boolean" ? Number(b) : null;
  if (aNum != null && bNum != null && Number.isFinite(aNum) && Number.isFinite(bNum)) {
    return aNum - bNum;
  }
  return text(a).localeCompare(text(b), undefined, { numeric: true, sensitivity: "base" });
}

function cellValue<T extends object>(row: T, column: Column<T> | undefined, key: string): unknown {
  if (column?.value) return column.value(row);
  return (row as Record<string, unknown>)[key];
}

export function DataTable<T extends object>({
  rows,
  columns,
  groupOptions = [],
  searchKeys,
  onRowClick,
  selectedKey,
  rowKey,
  defaultSort = null,
  emptyTitle = "No rows",
  emptyDescription = "Nothing matches the current filters.",
}: {
  rows: T[];
  columns: Array<Column<T>>;
  groupOptions?: Array<{ value: string; label: string }>;
  searchKeys?: string[];
  onRowClick?: (row: T) => void;
  selectedKey?: string;
  rowKey: (row: T) => string;
  defaultSort?: SortState;
  emptyTitle?: string;
  emptyDescription?: string;
}): React.ReactElement {
  const [query, setQuery] = useState("");
  const [groupBy, setGroupBy] = useState("none");
  const [sort, setSort] = useState<SortState>(defaultSort);
  const [page, setPage] = useState(0);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((row) => {
      const keys = searchKeys || columns.map((column) => column.key);
      return keys.some((key) => {
        const column = columns.find((item) => item.key === key);
        return text(cellValue(row, column, key)).toLowerCase().includes(needle);
      });
    });
  }, [columns, query, rows, searchKeys]);

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const column = columns.find((item) => item.key === sort.key);
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const av = cellValue(a, column, sort.key);
      const bv = cellValue(b, column, sort.key);
      const ae = isEmpty(av);
      const be = isEmpty(bv);
      if (ae || be) {
        if (ae && be) return 0;
        return ae ? 1 : -1;
      }
      return dir * compareValues(av, bv);
    });
  }, [columns, filtered, sort]);

  const grouped = useMemo(() => {
    if (groupBy === "none") return [{ key: "", rows: sorted }];
    const buckets = new Map<string, T[]>();
    for (const row of sorted) {
      const column = columns.find((item) => item.key === groupBy);
      const label = display(cellValue(row, column, groupBy));
      buckets.set(label, [...(buckets.get(label) || []), row]);
    }
    return [...buckets.entries()]
      .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" }))
      .map(([key, groupRows]) => ({ key, rows: groupRows }));
  }, [columns, groupBy, sorted]);

  const keyed = useMemo(() => {
    const seen = new Map<string, number>();
    const map = new Map<T, string>();
    for (const row of rows) {
      const base = rowKey(row);
      const n = seen.get(base) || 0;
      seen.set(base, n + 1);
      map.set(row, n ? `${base}#${n}` : base);
    }
    return map;
  }, [rowKey, rows]);
  const keyOf = (row: T) => keyed.get(row) || rowKey(row);

  const flat = useMemo(
    () => grouped.flatMap((group) => group.rows.map((row) => ({ row, group }))),
    [grouped],
  );
  const pages = Math.max(1, Math.ceil(flat.length / PAGE));
  const current = Math.min(page, pages - 1);
  const visible = useMemo(() => {
    const slice = flat.slice(current * PAGE, current * PAGE + PAGE);
    const groups: Array<{ key: string; total: number; rows: T[] }> = [];
    for (const item of slice) {
      const last = groups[groups.length - 1];
      if (!last || last.key !== item.group.key) {
        groups.push({ key: item.group.key, total: item.group.rows.length, rows: [item.row] });
      } else {
        last.rows.push(item.row);
      }
    }
    return groups;
  }, [current, flat]);

  function toggleSort(key: string) {
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, dir: "asc" };
      if (prev.dir === "asc") return { key, dir: "desc" };
      return null;
    });
    setPage(0);
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar className="h-auto w-full flex-wrap justify-between gap-2 border-b px-6 py-2">
        <ToolbarGroup className="min-w-0 flex-1">
          <div className="relative w-full max-w-xs">
            <Search className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(0);
              }}
              placeholder="Filter…"
              size="sm"
              aria-label="Filter rows"
              className="ps-7"
            />
          </div>
        </ToolbarGroup>
        {groupOptions.length ? (
          <>
            <ToolbarSeparator />
            <ToolbarGroup>
              <Select
                value={groupBy}
                items={[
                  { value: "none", label: "No grouping" },
                  ...groupOptions.map((option) => ({ value: option.value, label: option.label })),
                ]}
                onValueChange={(value) => {
                  setGroupBy(String(value || "none"));
                  setPage(0);
                }}
              >
                <SelectTrigger size="sm" className="min-w-36">
                  <SelectValue placeholder="Group" />
                </SelectTrigger>
                <SelectPopup>
                  <SelectItem value="none">No grouping</SelectItem>
                  {groupOptions.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectPopup>
              </Select>
            </ToolbarGroup>
          </>
        ) : null}
        <ToolbarSeparator />
        <ToolbarGroup>
          <ToolbarButton render={<Button size="xs" variant="ghost" className="h-[26px] text-[12px]" />}>
            <Filter className="size-3.5" />
            Filter
          </ToolbarButton>
          <ToolbarButton
            render={<Button size="xs" variant="ghost" className="h-[26px] text-[12px]" />}
            onClick={() => columns[0] && toggleSort(columns[0].key)}
          >
            <ArrowUpDown className="size-3.5" />
            Sort
          </ToolbarButton>
          <span className="px-1 text-[11px] tabular-nums text-muted-foreground">
            {filtered.length} {filtered.length === 1 ? "row" : "rows"}
          </span>
        </ToolbarGroup>
      </Toolbar>
      {filtered.length === 0 ? (
        <EmptyState icon={Inbox} title={emptyTitle} description={emptyDescription} />
      ) : (
        <div className="min-h-0 flex-1 overflow-auto">
          <Table>
            <TableHeader className="sticky top-0 z-10 bg-background/95 backdrop-blur-sm">
              <TableRow className="hover:bg-transparent">
                {columns.map((column) => {
                  const active = sort?.key === column.key ? sort.dir : null;
                  return (
                    <TableHead
                      key={column.key}
                      style={column.width ? { width: column.width, maxWidth: column.width } : undefined}
                      aria-sort={active === "asc" ? "ascending" : active === "desc" ? "descending" : "none"}
                      className={cn("h-8 px-2 text-[12px]", column.align === "right" && "text-right")}
                    >
                      <Button
                        size="xs"
                        variant="ghost"
                        className={cn(
                          "-ms-1 h-6 px-1 text-[11px] font-medium text-muted-foreground hover:text-foreground",
                          column.align === "right" && "ms-auto -me-1",
                        )}
                        onClick={() => toggleSort(column.key)}
                        aria-label={`Sort by ${column.header}`}
                      >
                        {column.header}
                        {active === "asc" ? (
                          <ChevronUp />
                        ) : active === "desc" ? (
                          <ChevronDown />
                        ) : (
                          <ChevronsUpDown className="opacity-35" />
                        )}
                      </Button>
                    </TableHead>
                  );
                })}
              </TableRow>
            </TableHeader>
            <TableBody>
              {visible.map((group) => (
                <GroupRows
                  key={`${group.key || "all"}-${group.rows.map(keyOf).join("|")}`}
                  group={group}
                  columns={columns}
                  colSpan={columns.length}
                  onRowClick={onRowClick}
                  selectedKey={selectedKey}
                  rowKey={keyOf}
                />
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {pages > 1 ? (
        <Toolbar className="h-auto w-full justify-between gap-2 rounded-none border-x-0 border-b-0 px-3 py-1.5">
          <ToolbarGroup>
            <span className="px-1 text-[11px] tabular-nums text-muted-foreground">
              Page {current + 1} of {pages}
            </span>
          </ToolbarGroup>
          <ToolbarGroup>
            <ToolbarButton
              render={<Button size="xs" variant="outline" />}
              disabled={current === 0}
              onClick={() => setPage((n) => Math.max(0, n - 1))}
            >
              Prev
            </ToolbarButton>
            <ToolbarButton
              render={<Button size="xs" variant="outline" />}
              disabled={current >= pages - 1}
              onClick={() => setPage((n) => n + 1)}
            >
              Next
            </ToolbarButton>
          </ToolbarGroup>
        </Toolbar>
      ) : null}
    </div>
  );
}

function GroupRows<T extends object>({
  group,
  columns,
  colSpan,
  onRowClick,
  selectedKey,
  rowKey,
}: {
  group: { key: string; total: number; rows: T[] };
  columns: Array<Column<T>>;
  colSpan: number;
  onRowClick?: (row: T) => void;
  selectedKey?: string;
  rowKey: (row: T) => string;
}): React.ReactElement {
  return (
    <>
      {group.key ? (
        <TableRow className="hover:bg-transparent">
          <TableCell
            colSpan={colSpan}
            className="bg-transparent py-2 text-[12.5px] font-semibold text-foreground"
          >
            {group.key} · {group.total}
          </TableCell>
        </TableRow>
      ) : null}
      {group.rows.map((row) => {
        const key = rowKey(row);
        return (
          <TableRow
            key={key}
            data-state={selectedKey === key ? "selected" : undefined}
            tabIndex={onRowClick ? 0 : undefined}
            className={cn(
              onRowClick &&
                "cursor-pointer transition-colors focus-visible:bg-accent/60 focus-visible:outline-none",
            )}
            onClick={onRowClick ? () => onRowClick(row) : undefined}
            onKeyDown={
              onRowClick
                ? (event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onRowClick(row);
                    }
                  }
                : undefined
            }
          >
            {columns.map((column) => (
              <TableCell
                key={column.key}
                style={column.width ? { width: column.width, maxWidth: column.width } : undefined}
                className={cn("h-10 px-2 py-0 text-[13.5px]", column.align === "right" && "text-right")}
              >
                {column.render ? (
                  column.render(row)
                ) : (
                  <span className={cn(column.key === columns[0]?.key && "issue-title")}>
                    <CellText value={cellValue(row, column, column.key)} mono={column.mono} />
                  </span>
                )}
              </TableCell>
            ))}
          </TableRow>
        );
      })}
    </>
  );
}

"use client";

import { ArrowUpDown, ChevronDown, ChevronUp, ChevronsUpDown, Download, Filter, Users, X } from "lucide-react";
import { useMemo, useState } from "react";
import { CellText } from "@/components/cell-text";
import { EmptyState } from "@/components/empty-state";
import { PageToolbar } from "@/components/page-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Menu, MenuItem, MenuPopup, MenuTrigger } from "@/components/ui/menu";
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
import { toastManager } from "@/components/ui/toast";
import { display, humanName, text } from "@/lib/format";
import { downloadCsv } from "@/lib/lead-ops";
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
const DEFAULT_MIN = "8rem";

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

function columnStyle(column: { width?: string }): React.CSSProperties {
  const minWidth = column.width || DEFAULT_MIN;
  return column.width ? { width: column.width, minWidth, maxWidth: column.width } : { minWidth };
}

function csvEscape(value: unknown): string {
  const str = text(value);
  if (/[",\n]/.test(str)) return `"${str.replace(/"/g, '""')}"`;
  return str;
}

function rowsToCsv<T extends object>(rows: T[], columns: Array<Column<T>>): string {
  const headers = columns.map((column) => csvEscape(column.header)).join(",");
  const lines = rows.map((row) =>
    columns.map((column) => csvEscape(cellValue(row, column, column.key))).join(","),
  );
  return `${[headers, ...lines].join("\n")}\n`;
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
  exportFilename,
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
  exportFilename?: string;
  emptyTitle?: string;
  emptyDescription?: string;
}): React.ReactElement {
  const [query, setQuery] = useState("");
  const [groupBy, setGroupBy] = useState("none");
  const [columnFilter, setColumnFilter] = useState<{ key: string; value: string; label: string } | null>(null);
  const [sort, setSort] = useState<SortState>(defaultSort);
  const [page, setPage] = useState(0);

  const filterFacets = useMemo(() => {
    const facets: Array<{ key: string; header: string; values: string[] }> = [];
    for (const column of columns) {
      const distinct = [
        ...new Set(
          rows
            .map((row) => text(cellValue(row, column, column.key)).trim())
            .filter((val) => val.length > 0 && val.length <= 40),
        ),
      ];
      if (distinct.length >= 2 && distinct.length <= 10) {
        facets.push({ key: column.key, header: column.header, values: distinct.slice(0, 8) });
      }
    }
    return facets.slice(0, 3);
  }, [columns, rows]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    let result = rows;
    if (columnFilter) {
      const col = columns.find((item) => item.key === columnFilter.key);
      result = result.filter(
        (row) => text(cellValue(row, col, columnFilter.key)).trim().toLowerCase() === columnFilter.value.toLowerCase(),
      );
    }
    if (!needle) return result;
    return result.filter((row) => {
      const keys = searchKeys || columns.map((column) => column.key);
      return keys.some((key) => {
        const column = columns.find((item) => item.key === key);
        return text(cellValue(row, column, key)).toLowerCase().includes(needle);
      });
    });
  }, [columnFilter, columns, query, rows, searchKeys]);

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
      const raw = display(cellValue(row, column, groupBy));
      const label = raw === "—" ? raw : humanName(raw);
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

  const activeSortHeader = sort ? columns.find((col) => col.key === sort.key)?.header : null;

  return (
    <div data-slot="data-table" className="flex min-h-0 min-w-0 flex-1 flex-col">
      <PageToolbar>
        <Input
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setPage(0);
          }}
          placeholder="Filter…"
          size="sm"
          aria-label="Filter rows"
          className="w-64"
        />
        {groupOptions.length ? (
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
            <SelectTrigger size="sm" className="w-auto min-w-36">
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
        ) : null}
        <Menu>
          <MenuTrigger render={<Button size="sm" variant="ghost" />}>
            <Filter className="size-3.5" />
            Filter
            {columnFilter ? <span className="text-muted-foreground">{columnFilter.label}</span> : null}
            <ChevronDown className="size-3 opacity-60" />
          </MenuTrigger>
          <MenuPopup align="start" className="min-w-44">
            <MenuItem
              onClick={() => {
                setColumnFilter(null);
                setPage(0);
              }}
            >
              All rows
            </MenuItem>
            {filterFacets.flatMap((facet) =>
              facet.values.map((val) => {
                const label = `${facet.header}: ${humanName(val)}`;
                return (
                  <MenuItem
                    key={`${facet.key}:${val}`}
                    onClick={() => {
                      setColumnFilter({ key: facet.key, value: val, label: humanName(val) });
                      setPage(0);
                    }}
                  >
                    {label}
                  </MenuItem>
                );
              }),
            )}
          </MenuPopup>
        </Menu>
        <Menu>
          <MenuTrigger render={<Button size="sm" variant="ghost" />}>
            <ArrowUpDown className="size-3.5" />
            Sort
            {activeSortHeader ? (
              <span className="text-muted-foreground">
                {activeSortHeader} {sort?.dir === "asc" ? "↑" : "↓"}
              </span>
            ) : null}
            <ChevronDown className="size-3 opacity-60" />
          </MenuTrigger>
          <MenuPopup align="start" className="min-w-40">
            <MenuItem
              onClick={() => {
                setSort(null);
                setPage(0);
              }}
            >
              Default order
            </MenuItem>
            {columns.map((column) => (
              <MenuItem key={column.key} onClick={() => toggleSort(column.key)}>
                {column.header}
              </MenuItem>
            ))}
          </MenuPopup>
        </Menu>
        {query || columnFilter || groupBy !== "none" ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setQuery("");
              setColumnFilter(null);
              setGroupBy("none");
              setPage(0);
            }}
          >
            <X className="size-3.5" />
            Reset
          </Button>
        ) : null}
        {exportFilename ? (
          <Button
            size="sm"
            variant="ghost"
            disabled={!sorted.length}
            onClick={() => {
              downloadCsv(exportFilename, rowsToCsv(sorted, columns));
              toastManager.add({
                type: "success",
                title: "CSV exported",
                description: `${sorted.length} rows`,
              });
            }}
          >
            <Download className="size-3.5" />
            Export CSV
          </Button>
        ) : null}
        <span className="ms-auto text-xs tabular-nums text-muted-foreground">
          {filtered.length} {filtered.length === 1 ? "row" : "rows"}
        </span>
      </PageToolbar>
      {filtered.length === 0 ? (
        <EmptyState icon={Users} title={emptyTitle} description={emptyDescription} />
      ) : (
        <div data-slot="page-table" className="min-h-0 min-w-0 flex-1 overflow-auto">
          <Table className="min-w-(--table-min-width) table-fixed">
            <TableHeader className="sticky top-0 z-10 bg-background/95 backdrop-blur-sm">
              <TableRow className="hover:bg-transparent">
                {columns.map((column) => {
                  const active = sort?.key === column.key ? sort.dir : null;
                  return (
                    <TableHead
                      key={column.key}
                      style={columnStyle(column)}
                      aria-sort={active === "asc" ? "ascending" : active === "desc" ? "descending" : "none"}
                      className={cn(
                        "h-8 overflow-hidden px-3 text-xs",
                        column.align === "right" && "text-right",
                      )}
                    >
                      <Button
                        size="xs"
                        variant="ghost"
                        className={cn(
                          "h-6 max-w-full px-1 text-xs font-medium text-muted-foreground hover:text-foreground",
                          column.align === "right" && "ms-auto",
                        )}
                        onClick={() => toggleSort(column.key)}
                        aria-label={`Sort by ${column.header}`}
                      >
                        <span className="truncate">{column.header}</span>
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
        <PageToolbar>
          <span className="text-xs tabular-nums text-muted-foreground">
            Page {current + 1} of {pages}
          </span>
          <span className="ms-auto flex items-center gap-2">
            <Button size="xs" variant="outline" disabled={current === 0} onClick={() => setPage((n) => Math.max(0, n - 1))}>
              Prev
            </Button>
            <Button
              size="xs"
              variant="outline"
              disabled={current >= pages - 1}
              onClick={() => setPage((n) => n + 1)}
            >
              Next
            </Button>
          </span>
        </PageToolbar>
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
          <TableCell colSpan={colSpan} className="bg-transparent py-2 text-sm font-semibold text-foreground">
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
              onRowClick && "cursor-pointer transition-colors focus-visible:bg-accent/60 focus-visible:outline-none",
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
                style={columnStyle(column)}
                className={cn(
                  "h-10 min-w-0 overflow-hidden px-3 py-0 text-sm",
                  column.align === "right" && "text-right",
                )}
              >
                <div className="min-w-0 max-w-full overflow-hidden">
                  {column.render ? (
                    column.render(row)
                  ) : (
                    <span className={cn(column.key === columns[0]?.key && "issue-title")}>
                      <CellText value={cellValue(row, column, column.key)} mono={column.mono} />
                    </span>
                  )}
                </div>
              </TableCell>
            ))}
          </TableRow>
        );
      })}
    </>
  );
}

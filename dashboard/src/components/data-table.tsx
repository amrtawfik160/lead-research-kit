"use client";

import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";
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

export type Column<T> = {
  key: string;
  header: string;
  width?: string;
  render?: (row: T) => React.ReactNode;
  value?: (row: T) => unknown;
};

const PAGE = 25;

export function DataTable<T extends object>({
  rows,
  columns,
  groupOptions = [],
  searchKeys,
  onRowClick,
  selectedKey,
  rowKey,
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
  emptyTitle?: string;
  emptyDescription?: string;
}): React.ReactElement {
  const [query, setQuery] = useState("");
  const [groupBy, setGroupBy] = useState("none");
  const [page, setPage] = useState(0);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((row) => {
      const keys = searchKeys || columns.map((column) => column.key);
      return keys.some((key) => {
        const column = columns.find((item) => item.key === key);
        const value = column?.value ? column.value(row) : (row as Record<string, unknown>)[key];
        return text(value).toLowerCase().includes(needle);
      });
    });
  }, [columns, query, rows, searchKeys]);

  const grouped = useMemo(() => {
    if (groupBy === "none") return [{ key: "", rows: filtered }];
    const buckets = new Map<string, T[]>();
    for (const row of filtered) {
      const column = columns.find((item) => item.key === groupBy);
      const label = display(column?.value ? column.value(row) : (row as Record<string, unknown>)[groupBy]);
      buckets.set(label, [...(buckets.get(label) || []), row]);
    }
    return [...buckets.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, groupRows]) => ({ key, rows: groupRows }));
  }, [columns, filtered, groupBy]);

  const keyed = useMemo(() => {
    const seen = new Map<string, number>();
    return rows.map((row) => {
      const base = rowKey(row);
      const n = seen.get(base) || 0;
      seen.set(base, n + 1);
      return { row, key: n ? `${base}#${n}` : base };
    });
  }, [rowKey, rows]);
  const keyOf = (row: T) => keyed.find((item) => item.row === row)?.key || rowKey(row);

  const flat = grouped.flatMap((group) => group.rows);
  const pages = Math.max(1, Math.ceil(flat.length / PAGE));
  const current = Math.min(page, pages - 1);
  const visible = useMemo(() => {
    const start = current * PAGE;
    const slice = new Set(flat.slice(start, start + PAGE));
    return grouped
      .map((group) => ({ ...group, rows: group.rows.filter((row) => slice.has(row)) }))
      .filter((group) => group.rows.length);
  }, [current, flat, grouped]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar className="h-auto w-full justify-between gap-2 border-b px-3 py-2">
        <ToolbarGroup className="min-w-0 flex-1">
          <Input
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(0);
            }}
            placeholder="Filter…"
            size="sm"
            aria-label="Filter rows"
          />
        </ToolbarGroup>
        {groupOptions.length ? (
          <>
            <ToolbarSeparator />
            <ToolbarGroup>
              <Select
                value={groupBy}
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
          <span className="px-1 text-xs text-muted-foreground">
            {filtered.length} {filtered.length === 1 ? "row" : "rows"}
          </span>
        </ToolbarGroup>
      </Toolbar>
      {filtered.length === 0 ? (
        <Empty className="py-16">
          <EmptyHeader>
            <EmptyTitle>{emptyTitle}</EmptyTitle>
            <EmptyDescription>{emptyDescription}</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto">
          <Table>
            <TableHeader>
              <TableRow>
                {columns.map((column) => (
                  <TableHead key={column.key} style={column.width ? { width: column.width } : undefined}>
                    {column.header}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {visible.map((group) => (
                <GroupRows
                  key={group.key || "all"}
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
        <Toolbar className="h-auto w-full justify-between gap-2 rounded-none border-x-0 border-b-0 px-3 py-2">
          <ToolbarGroup>
            <span className="px-1 text-xs text-muted-foreground">
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
  group: { key: string; rows: T[] };
  columns: Array<Column<T>>;
  colSpan: number;
  onRowClick?: (row: T) => void;
  selectedKey?: string;
  rowKey: (row: T) => string;
}): React.ReactElement {
  return (
    <>
      {group.key ? (
        <TableRow>
          <TableCell colSpan={colSpan} className="bg-muted/40 py-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            {group.key} · {group.rows.length}
          </TableCell>
        </TableRow>
      ) : null}
      {group.rows.map((row) => {
        const key = rowKey(row);
        return (
          <TableRow
            key={key}
            data-state={selectedKey === key ? "selected" : undefined}
            className={onRowClick ? "cursor-pointer" : undefined}
            onClick={onRowClick ? () => onRowClick(row) : undefined}
          >
            {columns.map((column) => (
              <TableCell key={column.key} className="text-xs">
                {column.render
                  ? column.render(row)
                  : display(column.value ? column.value(row) : (row as Record<string, unknown>)[column.key])}
              </TableCell>
            ))}
          </TableRow>
        );
      })}
    </>
  );
}

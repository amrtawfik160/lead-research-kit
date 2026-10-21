"use client";

import { ArrowUpDown, ChevronDown, ChevronRight, Filter, Users, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { CellText } from "@/components/cell-text";
import { EmptyState } from "@/components/empty-state";
import { EntityAvatar } from "@/components/entity-avatar";
import { LabelChip } from "@/components/label-chip";
import { PageToolbar } from "@/components/page-shell";
import { PriorityGlyph, QuietStatus, StatusGlyph } from "@/components/status-glyph";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Menu, MenuItem, MenuPopup, MenuTrigger } from "@/components/ui/menu";
import { display, humanName, titleCase } from "@/lib/format";
import { cn } from "@/lib/utils";

export type IssueItem<T> = {
  id: string;
  title: string;
  subtitle?: string;
  leadGlyph?: string;
  project?: string;
  status?: string;
  priority?: string;
  due?: string;
  avatar?: string;
  group?: string;
  done?: boolean;
  raw: T;
};

export type IssueColumns = {
  title: string;
  project: string;
  status: string;
  priority: string;
  due: string;
};

const LEAD_COLUMNS: IssueColumns = {
  title: "Lead",
  project: "Company",
  status: "Source",
  priority: "Tier",
  due: "City",
};

const ROW_GRID =
  "grid min-w-xl grid-cols-[minmax(0,1.5fr)_13rem_9rem_7rem_7.5rem] items-center gap-x-5 border-b";

export function IssueList<T>({
  items,
  selectedId,
  onSelect,
  groupBy = "group",
  emptyTitle = "No leads",
  emptyDescription = "Nothing matches the current lead filters.",
  hideIds = false,
  columns = LEAD_COLUMNS,
  searchPlaceholder = "Filter leads…",
  searchLabel = "Filter leads",
  filterAllLabel = "All sources",
}: {
  items: Array<IssueItem<T>>;
  selectedId?: string;
  onSelect?: (item: IssueItem<T> | null) => void;
  groupBy?: "group" | "none";
  emptyTitle?: string;
  emptyDescription?: string;
  hideIds?: boolean;
  columns?: IssueColumns;
  searchPlaceholder?: string;
  searchLabel?: string;
  filterAllLabel?: string;
}): React.ReactElement {
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [cursor, setCursor] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sortKey, setSortKey] = useState<"manual" | "title" | "project" | "status" | "priority" | "due">("manual");
  const activeId = selectedId || cursor;

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    let rows = items;
    if (statusFilter !== "all") {
      rows = rows.filter((item) => String(item.status || "").toLowerCase() === statusFilter);
    }
    if (needle) {
      rows = rows.filter((item) =>
        [item.id, item.title, item.subtitle, item.project, item.status, item.priority, item.due, item.group]
          .filter(Boolean)
          .join(" ")
          .toLowerCase()
          .includes(needle),
      );
    }
    if (sortKey === "title") {
      rows = [...rows].sort((a, b) => a.title.localeCompare(b.title));
    } else if (sortKey === "project") {
      rows = [...rows].sort((a, b) => display(a.project).localeCompare(display(b.project)));
    } else if (sortKey === "status") {
      rows = [...rows].sort((a, b) => display(a.status).localeCompare(display(b.status)));
    } else if (sortKey === "priority") {
      rows = [...rows].sort((a, b) => display(a.priority).localeCompare(display(b.priority)));
    } else if (sortKey === "due") {
      rows = [...rows].sort((a, b) => display(a.due).localeCompare(display(b.due)));
    }
    return rows;
  }, [items, query, sortKey, statusFilter]);

  const statuses = useMemo(() => {
    return [...new Set(items.map((item) => String(item.status || "").toLowerCase()).filter(Boolean))].sort();
  }, [items]);

  const groups = useMemo(() => {
    if (groupBy === "none") return [{ key: "", rows: filtered }];
    const buckets = new Map<string, Array<IssueItem<T>>>();
    for (const item of filtered) {
      const key = item.group || "Ungrouped";
      buckets.set(key, [...(buckets.get(key) || []), item]);
    }
    return [...buckets.entries()].map(([key, rows]) => ({ key, rows }));
  }, [filtered, groupBy]);

  const flat = useMemo(() => groups.flatMap((group) => (collapsed[group.key] ? [] : group.rows)), [collapsed, groups]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)
      ) {
        return;
      }
      if (event.key === "Escape" && onSelect) {
        event.preventDefault();
        setCursor("");
        onSelect(null);
        return;
      }
      if (!flat.length || !onSelect) return;
      const index = Math.max(0, flat.findIndex((item) => item.id === activeId));
      if (event.key === "j" || event.key === "ArrowDown") {
        event.preventDefault();
        const next = flat[Math.min(flat.length - 1, index + 1)];
        setCursor(next.id);
        onSelect(next);
      }
      if (event.key === "k" || event.key === "ArrowUp") {
        event.preventDefault();
        const next = flat[Math.max(0, index - 1)];
        setCursor(next.id);
        onSelect(next);
      }
      if (event.key === "Enter" && flat[index]) onSelect(flat[index]);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [activeId, cursor, flat, onSelect]);

  const sortLabels: Record<typeof sortKey, string> = {
    manual: "",
    title: columns.title,
    project: columns.project,
    status: columns.status,
    priority: columns.priority,
    due: columns.due,
  };

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <PageToolbar>
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={searchPlaceholder}
          size="sm"
          aria-label={searchLabel}
          className="w-64"
        />
        <Menu>
          <MenuTrigger render={<Button size="sm" variant="ghost" />}>
            <Filter className="size-3.5" />
            Filter
            {statusFilter !== "all" ? <span className="text-muted-foreground">{humanName(statusFilter)}</span> : null}
            <ChevronDown className="size-3 opacity-60" />
          </MenuTrigger>
          <MenuPopup align="start" className="min-w-40">
            <MenuItem onClick={() => setStatusFilter("all")}>{filterAllLabel}</MenuItem>
            {statuses.map((status) => (
              <MenuItem key={status} onClick={() => setStatusFilter(status)}>
                {humanName(status)}
              </MenuItem>
            ))}
          </MenuPopup>
        </Menu>
        <Menu>
          <MenuTrigger render={<Button size="sm" variant="ghost" />}>
            <ArrowUpDown className="size-3.5" />
            Sort
            {sortKey !== "manual" ? <span className="text-muted-foreground">{sortLabels[sortKey]}</span> : null}
            <ChevronDown className="size-3 opacity-60" />
          </MenuTrigger>
          <MenuPopup align="start" className="min-w-40">
            <MenuItem onClick={() => setSortKey("manual")}>Default order</MenuItem>
            <MenuItem onClick={() => setSortKey("title")}>{columns.title}</MenuItem>
            <MenuItem onClick={() => setSortKey("project")}>{columns.project}</MenuItem>
            <MenuItem onClick={() => setSortKey("status")}>{columns.status}</MenuItem>
            <MenuItem onClick={() => setSortKey("priority")}>{columns.priority}</MenuItem>
            <MenuItem onClick={() => setSortKey("due")}>{columns.due}</MenuItem>
          </MenuPopup>
        </Menu>
        {query || statusFilter !== "all" || sortKey !== "manual" ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setQuery("");
              setStatusFilter("all");
              setSortKey("manual");
            }}
          >
            <X className="size-3.5" />
            Reset
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
          <div data-slot="table-row" className={cn(ROW_GRID, "text-xs font-medium text-muted-foreground")}>
            <div data-slot="table-cell" className="flex h-8 min-w-0 items-center overflow-hidden">{columns.title}</div>
            <div data-slot="table-cell" className="flex h-8 min-w-0 items-center overflow-hidden">{columns.project}</div>
            <div data-slot="table-cell" className="flex h-8 min-w-0 items-center overflow-hidden">{columns.status}</div>
            <div data-slot="table-cell" className="flex h-8 min-w-0 items-center overflow-hidden">{columns.priority}</div>
            <div data-slot="table-cell" className="flex h-8 min-w-0 items-center overflow-hidden">{columns.due}</div>
          </div>
          {groups.map((group) => (
            <div key={group.key || "all"}>
              {group.key ? (
                <button
                  type="button"
                  onClick={() => setCollapsed((prev) => ({ ...prev, [group.key]: !prev[group.key] }))}
                  className="mt-2.5 flex h-9 w-full items-center gap-2 border-b text-sm font-semibold"
                >
                  {collapsed[group.key] ? (
                    <ChevronRight className="size-3.5 text-muted-foreground" />
                  ) : (
                    <ChevronDown className="size-3.5 text-muted-foreground" />
                  )}
                  {titleCase(group.key)}
                  <span className="font-medium text-muted-foreground">{group.rows.length}</span>
                </button>
              ) : null}
              {collapsed[group.key]
                ? null
                : group.rows.map((item) => {
                    const selected = activeId === item.id;
                    const priorityText = item.priority ? humanName(display(item.priority)) : "—";
                    return (
                      <button
                        key={item.id}
                        type="button"
                        data-slot="table-row"
                        onClick={() => {
                          setCursor(item.id);
                          onSelect?.(item);
                        }}
                        className={cn(
                          ROW_GRID,
                          "min-h-11 w-full text-left transition-colors duration-150 focus-visible:bg-accent/60 focus-visible:outline-none",
                          selected ? "bg-muted" : "hover:bg-muted/70",
                        )}
                      >
                        <div data-slot="table-cell" className="flex min-w-0 items-center gap-2 overflow-hidden">
                          <StatusGlyph value={item.leadGlyph || item.status || item.priority} />
                          {hideIds ? null : (
                            <span className="shrink-0 font-mono text-xs text-muted-foreground">{item.id}</span>
                          )}
                          <CellText
                            value={item.title}
                            className={cn("issue-title text-sm", item.done && "text-muted-foreground")}
                          />
                          {item.subtitle ? (
                            <span className="hidden shrink-0 text-xs text-muted-foreground xl:inline">
                              · {item.subtitle}
                            </span>
                          ) : null}
                        </div>
                        <div data-slot="table-cell" className="min-w-0 overflow-hidden">
                          <LabelChip value={item.project} />
                        </div>
                        <div data-slot="table-cell" className="min-w-0 overflow-hidden">
                          <QuietStatus value={item.status} />
                        </div>
                        <div data-slot="table-cell" className="flex min-w-0 items-center gap-1.5 overflow-hidden text-xs text-muted-foreground">
                          <PriorityGlyph value={item.priority} />
                          <CellText value={priorityText} className="text-xs text-muted-foreground" />
                        </div>
                        <div data-slot="table-cell" className="flex min-w-0 items-center justify-between gap-2 overflow-hidden">
                          <CellText value={display(item.due)} className="text-xs text-muted-foreground" />
                          <EntityAvatar name={item.avatar || item.project || item.title} size={18} />
                        </div>
                      </button>
                    );
                  })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

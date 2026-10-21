"use client";

import { ArrowUpDown, ChevronDown, ChevronRight, Filter, Inbox, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { EmptyState } from "@/components/empty-state";
import { EntityAvatar } from "@/components/entity-avatar";
import { LabelChip } from "@/components/label-chip";
import { PriorityGlyph, QuietStatus, StatusGlyph } from "@/components/status-glyph";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Menu, MenuItem, MenuPopup, MenuTrigger } from "@/components/ui/menu";
import { display, titleCase } from "@/lib/format";
import { cn } from "@/lib/utils";

export type IssueItem<T> = {
  id: string;
  title: string;
  project?: string;
  status?: string;
  priority?: string;
  due?: string;
  avatar?: string;
  group?: string;
  done?: boolean;
  raw: T;
};

export function IssueList<T>({
  items,
  selectedId,
  onSelect,
  groupBy = "group",
  emptyTitle = "No rows",
  emptyDescription = "Nothing matches the current filters.",
  hideIds = false,
}: {
  items: Array<IssueItem<T>>;
  selectedId?: string;
  onSelect?: (item: IssueItem<T> | null) => void;
  groupBy?: "group" | "none";
  emptyTitle?: string;
  emptyDescription?: string;
  hideIds?: boolean;
}): React.ReactElement {
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [cursor, setCursor] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sortKey, setSortKey] = useState<"manual" | "title" | "status" | "due">("manual");
  const activeId = selectedId || cursor;

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    let rows = items;
    if (statusFilter !== "all") {
      rows = rows.filter((item) => String(item.status || "").toLowerCase() === statusFilter);
    }
    if (needle) {
      rows = rows.filter((item) =>
        [item.id, item.title, item.project, item.status, item.priority, item.due, item.group]
          .filter(Boolean)
          .join(" ")
          .toLowerCase()
          .includes(needle),
      );
    }
    if (sortKey === "title") {
      rows = [...rows].sort((a, b) => a.title.localeCompare(b.title));
    } else if (sortKey === "status") {
      rows = [...rows].sort((a, b) => display(a.status).localeCompare(display(b.status)));
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

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-1.5 border-b px-6 py-2">
        <div className="relative w-full max-w-[200px]">
          <Search className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter…"
            size="sm"
            aria-label="Filter tasks"
            className="h-[26px] ps-7"
          />
        </div>
        <Menu>
          <MenuTrigger
            render={
              <Button size="sm" variant="ghost" className="h-[26px] rounded-[6px] px-2 text-[12px] font-medium" />
            }
          >
            <Filter className="size-3.5" />
            Filter
            {statusFilter !== "all" ? <span className="text-muted-foreground">{titleCase(statusFilter)}</span> : null}
          </MenuTrigger>
          <MenuPopup align="start" className="min-w-40">
            <MenuItem onClick={() => setStatusFilter("all")}>All statuses</MenuItem>
            {statuses.map((status) => (
              <MenuItem key={status} onClick={() => setStatusFilter(status)}>
                {titleCase(status)}
              </MenuItem>
            ))}
          </MenuPopup>
        </Menu>
        <Menu>
          <MenuTrigger
            render={
              <Button size="sm" variant="ghost" className="h-[26px] rounded-[6px] px-2 text-[12px] font-medium" />
            }
          >
            <ArrowUpDown className="size-3.5" />
            Sort
          </MenuTrigger>
          <MenuPopup align="start" className="min-w-40">
            <MenuItem onClick={() => setSortKey("manual")}>Manual</MenuItem>
            <MenuItem onClick={() => setSortKey("title")}>Title</MenuItem>
            <MenuItem onClick={() => setSortKey("status")}>Status</MenuItem>
            <MenuItem onClick={() => setSortKey("due")}>Due date</MenuItem>
          </MenuPopup>
        </Menu>
        <span className="ms-auto text-[11px] tabular-nums text-muted-foreground">
          {filtered.length} {filtered.length === 1 ? "row" : "rows"}
        </span>
      </div>
      {filtered.length === 0 ? (
        <EmptyState icon={Inbox} title={emptyTitle} description={emptyDescription} />
      ) : (
        <div className="min-h-0 flex-1 overflow-auto">
          <div className="grid grid-cols-[minmax(0,1.6fr)_10rem_7.5rem_6rem_7rem] items-center border-b px-6 text-[12px] font-medium text-muted-foreground">
            <div className="h-8">Task</div>
            <div className="h-8">Project</div>
            <div className="h-8">Status</div>
            <div className="h-8">Priority</div>
            <div className="h-8">Due date</div>
          </div>
          {groups.map((group) => (
            <div key={group.key || "all"}>
              {group.key ? (
                <button
                  type="button"
                  onClick={() => setCollapsed((prev) => ({ ...prev, [group.key]: !prev[group.key] }))}
                  className="mt-2.5 flex h-9 w-full items-center gap-2 border-b px-6 text-[12.5px] font-semibold"
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
                    return (
                      <button
                        key={item.id}
                        type="button"
                        onClick={() => {
                          setCursor(item.id);
                          onSelect?.(item);
                        }}
                        className={cn(
                          "grid min-h-10 w-full grid-cols-[minmax(0,1.6fr)_10rem_7.5rem_6rem_7rem] items-center border-b border-[#f0eeea] px-6 text-left transition-colors duration-150 dark:border-[#252523]",
                          selected ? "bg-[color-mix(in_srgb,var(--primary)_10%,transparent)]" : "hover:bg-[#f6f5f2] dark:hover:bg-[#252524]",
                        )}
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          <StatusGlyph value={item.status || item.priority} />
                          {hideIds ? null : (
                            <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{item.id}</span>
                          )}
                          <span
                            className={cn(
                              "issue-title truncate text-[13.5px]",
                              item.done && "text-muted-foreground line-through",
                            )}
                          >
                            {item.title}
                          </span>
                        </div>
                        <div className="min-w-0">
                          <LabelChip value={item.project} />
                        </div>
                        <div>
                          <QuietStatus value={item.status} />
                        </div>
                        <div className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
                          <PriorityGlyph value={item.priority} />
                          <span className="truncate">{display(item.priority)}</span>
                        </div>
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate text-[12px] text-muted-foreground">{display(item.due)}</span>
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

"use client";

import type { ReactNode } from "react";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

export function Panel({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}): React.ReactElement {
  return (
    <section className={cn("overflow-hidden rounded-xl border bg-card", className)}>{children}</section>
  );
}

export function PanelHeader({
  title,
  actions,
}: {
  title: string;
  actions?: ReactNode;
}): React.ReactElement {
  return (
    <div className="flex items-center gap-2 px-4 pt-4 pb-3">
      <h2 className="text-sm font-semibold">{title}</h2>
      {actions ? <div className="ms-auto flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function SegTabs<T extends string>({
  value,
  onChange,
  items,
}: {
  value: T;
  onChange: (value: T) => void;
  items: Array<{ value: T; label: string; count?: number }>;
}): React.ReactElement {
  return (
    <ToggleGroup
      size="sm"
      variant="outline"
      aria-label={items.map((item) => item.label).join(", ")}
      value={[value]}
      onValueChange={(next) => {
        const picked = next.find((item) => item !== value) ?? next[0];
        if (picked) onChange(picked as T);
      }}
    >
      {items.map((item) => (
        <ToggleGroupItem key={item.value} value={item.value}>
          {item.label}
          {item.count != null ? <span className="text-muted-foreground">{item.count}</span> : null}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

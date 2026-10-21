import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Panel({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}): React.ReactElement {
  return (
    <section className={cn("overflow-hidden rounded-[10px] border bg-card", className)}>{children}</section>
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
    <div className="flex items-center gap-2 px-3.5 pt-3 pb-2.5">
      <h2 className="text-[13.5px] font-semibold tracking-[-0.01em]">{title}</h2>
      {actions ? <div className="ms-auto flex items-center gap-1">{actions}</div> : null}
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
    <div className="inline-flex gap-0.5 rounded-[6px] bg-[#eeede9] p-0.5 dark:bg-[#2d2d2b]">
      {items.map((item) => {
        const on = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={() => onChange(item.value)}
            className={cn(
              "inline-flex h-6 items-center gap-1 rounded-[5px] px-2 text-[12px] font-medium transition-colors",
              on
                ? "bg-background text-foreground shadow-[0_1px_2px_rgba(0,0,0,0.08),0_0_0_1px_var(--border)]"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {item.label}
            {item.count != null ? <span className="text-[#8c8880]">{item.count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

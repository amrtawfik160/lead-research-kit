import { humanName } from "@/lib/format";
import { cn } from "@/lib/utils";

const TONES = [
  "bg-[var(--chart-1)]",
  "bg-[var(--chart-2)]",
  "bg-[var(--chart-3)]",
  "bg-[var(--chart-4)]",
  "bg-[var(--chart-5)]",
];

export function CountBar({
  counts,
  className,
}: {
  counts?: Record<string, number> | null;
  className?: string;
}): React.ReactElement | null {
  const items = Object.entries(counts || {}).filter(([, value]) => value > 0);
  if (!items.length) return null;
  const total = items.reduce((sum, [, value]) => sum + value, 0) || 1;
  return (
    <div className={cn("grid gap-3", className)}>
      {items.map(([label, value], index) => (
        <div key={label} className="grid gap-1.5">
          <div className="flex items-center justify-between gap-3 text-sm">
            <span className="inline-flex min-w-0 items-center gap-2">
              <span className={cn("size-2.5 shrink-0 rounded-full", TONES[index % TONES.length])} />
              <span className="truncate">{humanName(label)}</span>
            </span>
            <span className="tabular-nums text-muted-foreground">{value}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-muted">
            <div
              className={cn("h-full rounded-full", TONES[index % TONES.length])}
              style={{ width: `${Math.max(6, (value / total) * 100)}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

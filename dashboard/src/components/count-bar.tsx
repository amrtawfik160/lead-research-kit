import { titleCase } from "@/lib/format";
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
    <div className={cn("flex flex-col gap-2", className)}>
      <div className="flex h-1.5 overflow-hidden rounded-full bg-muted">
        {items.map(([label, value], index) => (
          <div
            key={label}
            className={cn("h-full min-w-0.5 transition-[width]", TONES[index % TONES.length])}
            style={{ width: `${(value / total) * 100}%` }}
            title={`${titleCase(label)} ${value}`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        {items.map(([label, value], index) => (
          <span key={label} className="inline-flex items-center gap-1.5">
            <span className={cn("size-1.5 rounded-full", TONES[index % TONES.length])} />
            {titleCase(label)}
            <span className="tabular-nums text-foreground/80">{value}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

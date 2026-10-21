import { cn } from "@/lib/utils";

const ACCENT = "#4b5bd6";
const TRACK = "#eeede9";
const MUTED = "#8c8880";

export function ChartCard({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <section className="flex min-h-[148px] flex-col rounded-[10px] border bg-card px-4 py-3">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h3 className="text-[12.5px] font-semibold tracking-[-0.01em]">{title}</h3>
        {hint ? <span className="text-[11px] tabular-nums text-muted-foreground">{hint}</span> : null}
      </div>
      <div className="min-h-0 flex-1">{children}</div>
    </section>
  );
}

export function BarSpark({
  values,
  labels,
}: {
  values: number[];
  labels?: string[];
}): React.ReactElement {
  const max = Math.max(1, ...values);
  const width = 240;
  const height = 72;
  const gap = 4;
  const bar = values.length ? (width - gap * (values.length - 1)) / values.length : width;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-[72px] w-full" role="img" aria-label="Leads per day">
      {values.map((value, index) => {
        const h = Math.max(2, (value / max) * 56);
        const x = index * (bar + gap);
        return (
          <g key={`${labels?.[index] || index}`}>
            <rect x={x} y={height - 12 - h} width={Math.max(2, bar)} height={h} rx="2" fill={ACCENT} />
            {labels?.[index] ? (
              <text x={x + bar / 2} y={height - 2} textAnchor="middle" fill={MUTED} fontSize="8">
                {labels[index].slice(5)}
              </text>
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}

export function Donut({
  value,
  total,
  label,
}: {
  value: number;
  total: number;
  label: string;
}): React.ReactElement {
  const pct = total ? value / total : 0;
  const r = 28;
  const c = 2 * Math.PI * r;
  return (
    <div className="flex items-center gap-3">
      <svg viewBox="0 0 72 72" className="size-[72px]" role="img" aria-label={label}>
        <circle cx="36" cy="36" r={r} fill="none" stroke={TRACK} strokeWidth="8" />
        <circle
          cx="36"
          cy="36"
          r={r}
          fill="none"
          stroke={ACCENT}
          strokeWidth="8"
          strokeDasharray={`${c * pct} ${c}`}
          strokeLinecap="round"
          transform="rotate(-90 36 36)"
        />
        <text x="36" y="40" textAnchor="middle" fill="currentColor" fontSize="13" fontWeight="600">
          {Math.round(pct * 100)}%
        </text>
      </svg>
      <div className="text-[12px] leading-5 text-muted-foreground">{label}</div>
    </div>
  );
}

export function HBars({
  items,
}: {
  items: Array<{ label: string; value: number; max?: number; hint?: string }>;
}): React.ReactElement {
  const max = Math.max(1, ...items.map((item) => item.max ?? item.value));
  if (!items.length) {
    return <p className="pt-4 text-[12px] text-muted-foreground">No data in this workspace yet.</p>;
  }
  return (
    <ul className="grid gap-1.5">
      {items.map((item) => (
        <li key={item.label} className="grid grid-cols-[7rem_minmax(0,1fr)_2.5rem] items-center gap-2 text-[11.5px]">
          <span className="truncate text-[#57544e] dark:text-[#b5b2ab]">{item.label}</span>
          <span className="h-1.5 overflow-hidden rounded-full bg-[#eeede9] dark:bg-[#2d2d2b]">
            <span
              className="block h-full rounded-full bg-primary"
              style={{ width: `${Math.min(100, (item.value / max) * 100)}%` }}
            />
          </span>
          <span className={cn("text-right tabular-nums text-muted-foreground")}>{item.hint ?? item.value}</span>
        </li>
      ))}
    </ul>
  );
}

export function StackedQuota({
  items,
}: {
  items: Array<{ label: string; remaining: number; spent: number; hint?: string }>;
}): React.ReactElement {
  if (!items.length) {
    return <p className="pt-4 text-[12px] text-muted-foreground">No attested free pools yet.</p>;
  }
  return (
    <ul className="grid gap-1.5">
      {items.map((item) => {
        const total = Math.max(1, item.remaining + item.spent);
        return (
          <li key={item.label} className="grid gap-0.5 text-[11.5px]">
            <div className="flex justify-between gap-2">
              <span className="truncate text-[#57544e] dark:text-[#b5b2ab]">{item.label}</span>
              <span className="tabular-nums text-muted-foreground">
                {item.hint || (item.spent ? `${item.spent} used` : `${item.remaining} left`)}
              </span>
            </div>
            <span className="flex h-1.5 overflow-hidden rounded-full bg-[#eeede9] dark:bg-[#2d2d2b]">
              <span className="h-full bg-primary" style={{ width: `${(item.spent / total) * 100}%` }} />
              <span className="h-full bg-[#9aa3e8]" style={{ width: `${(item.remaining / total) * 100}%` }} />
            </span>
          </li>
        );
      })}
    </ul>
  );
}

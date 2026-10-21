"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContentProps } from "recharts";
import { EmptyState } from "@/components/empty-state";
import { cn } from "@/lib/utils";

const AXIS = { fill: "var(--muted-foreground)", fontSize: 12 };
const GRID = "var(--border)";

function shortLabel(value: string | number): string {
  const text = String(value);
  return text.length > 26 ? `${text.slice(0, 25)}…` : text;
}

function SeriesLegend({ items }: { items: Array<{ label: string; color: string }> }): React.ReactElement {
  return (
    <ul className="mb-1 flex flex-wrap gap-x-3 gap-y-1 px-2 text-xs text-muted-foreground">
      {items.map((item) => (
        <li key={item.label} className="inline-flex items-center gap-1.5">
          <span className="size-2 rounded-[2px]" style={{ background: item.color }} />
          {item.label}
        </li>
      ))}
    </ul>
  );
}

export function ChartCard({
  title,
  hint,
  children,
  className,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
  className?: string;
}): React.ReactElement {
  return (
    <section className={cn("flex min-h-[300px] min-w-0 flex-col rounded-xl border bg-card", className)}>
      <div className="flex items-baseline justify-between gap-3 px-4 pt-4 pb-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        {hint ? <span className="text-xs tabular-nums text-muted-foreground">{hint}</span> : null}
      </div>
      <div className="min-h-0 flex-1 px-3 pb-4">{children}</div>
    </section>
  );
}

function ChartTooltip({
  active,
  payload,
  label,
}: Partial<TooltipContentProps<number, string>>): React.ReactElement | null {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border bg-popover px-2.5 py-2 text-sm text-popover-foreground shadow-md">
      {label ? <p className="mb-1 font-medium">{label}</p> : null}
      <ul className="grid gap-0.5">
        {payload.map((item) => (
          <li key={String(item.dataKey || item.name)} className="flex items-center justify-between gap-4">
            <span className="inline-flex items-center gap-1.5 text-muted-foreground">
              <span className="size-2 rounded-[2px]" style={{ background: item.color || "var(--chart-1)" }} />
              {item.name}
            </span>
            <span className="font-medium tabular-nums">{item.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function LeadsPerDayChart({
  points,
}: {
  points: Array<{ date: string; delivered: number; target: number }>;
}): React.ReactElement {
  if (!points.length) return <EmptyState compact title="No deliveries yet" description="Run a daily delivery to chart leads per day." />;
  const data = points.map((row) => ({
    date: String(row.date || "").slice(5),
    Delivered: row.delivered,
    Target: row.target,
  }));
  return (
    <div className="flex h-[228px] w-full flex-col" role="img" aria-label="Delivered and target leads per day">
      <SeriesLegend
        items={[
          { label: "Delivered", color: "var(--chart-1)" },
          { label: "Target", color: "var(--chart-4)" },
        ]}
      />
      <div className="min-h-0 flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} barGap={3} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke={GRID} />
            <XAxis dataKey="date" tick={AXIS} tickLine={false} axisLine={{ stroke: GRID }} />
            <YAxis allowDecimals={false} width={32} tick={AXIS} tickLine={false} axisLine={false} />
            <Tooltip content={<ChartTooltip />} cursor={{ fill: "var(--accent)", opacity: 0.35 }} />
            <Bar dataKey="Delivered" fill="var(--chart-1)" radius={[4, 4, 0, 0]} maxBarSize={22} />
            <Bar dataKey="Target" fill="var(--chart-4)" radius={[4, 4, 0, 0]} maxBarSize={22} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function VerificationChart({
  verified,
  unverified,
}: {
  verified: number;
  unverified: number;
}): React.ReactElement {
  const total = verified + unverified;
  if (!total) return <EmptyState compact title="No leads to verify" description="The latest delivery has no email rows yet." />;
  const data = [
    { name: "Verified", value: verified, fill: "var(--chart-2)" },
    { name: "Unverified", value: unverified, fill: "var(--chart-3)" },
  ];
  return (
    <div className="flex h-[228px] w-full flex-col" role="img" aria-label="Verified and unverified leads">
      <SeriesLegend
        items={[
          { label: "Verified", color: "var(--chart-2)" },
          { label: "Unverified", color: "var(--chart-3)" },
        ]}
      />
      <div className="min-h-0 flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
          <Pie data={data} dataKey="value" nameKey="name" innerRadius={58} outerRadius={78} paddingAngle={2} stroke="var(--card)">
            {data.map((slice) => (
              <Cell key={slice.name} fill={slice.fill} />
            ))}
          </Pie>
          <Tooltip content={<ChartTooltip />} />
          <text x="50%" y="48%" textAnchor="middle" fill="var(--foreground)" fontSize="22" fontWeight="600">
            {Math.round((verified / total) * 100)}%
          </text>
          <text x="50%" y="58%" textAnchor="middle" fill="var(--muted-foreground)" fontSize="11">
            verified
          </text>
          </PieChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function HorizontalBarChart({
  rows,
  emptyTitle,
  emptyDescription,
}: {
  rows: Array<{ label: string; value: number }>;
  emptyTitle: string;
  emptyDescription: string;
}): React.ReactElement {
  if (!rows.length) return <EmptyState compact title={emptyTitle} description={emptyDescription} />;
  const data = rows.map((row) => ({ name: row.label, Count: row.value }));
  return (
    <div className="h-[228px] w-full" role="img" aria-label="Counts by label">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 4, bottom: 0 }}>
          <CartesianGrid horizontal={false} stroke={GRID} />
          <XAxis type="number" allowDecimals={false} tick={AXIS} tickLine={false} axisLine={{ stroke: GRID }} />
          <YAxis
            type="category"
            dataKey="name"
            width={156}
            tick={AXIS}
            tickLine={false}
            axisLine={false}
            tickFormatter={shortLabel}
          />
          <Tooltip content={<ChartTooltip />} cursor={{ fill: "var(--accent)", opacity: 0.35 }} />
          <Bar dataKey="Count" fill="var(--chart-1)" radius={[0, 4, 4, 0]} maxBarSize={14} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function QuotaChart({
  rows,
}: {
  rows: Array<{ label: string; remaining: number; spent: number }>;
}): React.ReactElement {
  if (!rows.length) {
    return <EmptyState compact title="No attested pools" description="Confirm a free allowance before quota can be charted." />;
  }
  const data = rows.map((row) => ({
    name: row.label,
    Remaining: row.remaining,
    Spent: row.spent,
  }));
  return (
    <div className="flex h-[228px] w-full flex-col" role="img" aria-label="Spent and remaining provider quota">
      <SeriesLegend
        items={[
          { label: "Spent", color: "var(--chart-5)" },
          { label: "Remaining", color: "var(--chart-1)" },
        ]}
      />
      <div className="min-h-0 flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 4, bottom: 0 }}>
            <CartesianGrid horizontal={false} stroke={GRID} />
            <XAxis type="number" allowDecimals={false} tick={AXIS} tickLine={false} axisLine={{ stroke: GRID }} />
            <YAxis
              type="category"
              dataKey="name"
              width={156}
              tick={AXIS}
              tickLine={false}
              axisLine={false}
              tickFormatter={shortLabel}
            />
            <Tooltip content={<ChartTooltip />} cursor={{ fill: "var(--accent)", opacity: 0.35 }} />
            <Bar dataKey="Spent" stackId="quota" fill="var(--chart-5)" maxBarSize={14} />
            <Bar dataKey="Remaining" stackId="quota" fill="var(--chart-1)" radius={[0, 4, 4, 0]} maxBarSize={14} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

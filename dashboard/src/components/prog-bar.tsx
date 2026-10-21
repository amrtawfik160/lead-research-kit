import { cn } from "@/lib/utils";

export function ProgBar({
  value,
  tone,
  className,
}: {
  value: number;
  tone?: "ok" | "bad";
  className?: string;
}): React.ReactElement {
  const pct = Math.max(0, Math.min(100, Math.round(value)));
  return (
    <span
      className={cn("prog", tone === "ok" && "ok", tone === "bad" && "bad", className)}
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <i style={{ "--p": pct / 100 } as React.CSSProperties} />
    </span>
  );
}

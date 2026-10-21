import { Circle, CircleAlert, CircleCheck, CircleDashed, CircleDot } from "lucide-react";
import { display } from "@/lib/format";
import { cn } from "@/lib/utils";

type Tone = "todo" | "progress" | "review" | "done" | "alert" | "backlog";

function toneOf(value?: string | null): Tone {
  const raw = (value || "").toLowerCase();
  if (/done|valid|low|confirmed|enabled|clear|active|completed|a\b|intact/.test(raw)) return "done";
  if (/progress|medium|partial|pending|uncertain|finder|b\b/.test(raw)) return "progress";
  if (/review|paused|quota|catch|affiliation/.test(raw)) return "review";
  if (/held|fail|high|reject|broken|invalid|error|overdue|risk/.test(raw)) return "alert";
  if (/backlog|unknown|skip|off|preview/.test(raw)) return "backlog";
  return "todo";
}

const ICONS = {
  todo: Circle,
  progress: CircleDot,
  review: CircleDot,
  done: CircleCheck,
  alert: CircleAlert,
  backlog: CircleDashed,
} as const;

const COLOR: Record<Tone, string> = {
  todo: "text-muted-foreground",
  progress: "text-warning-foreground",
  review: "text-info-foreground",
  done: "text-success-foreground",
  alert: "text-destructive-foreground",
  backlog: "text-muted-foreground",
};

export function StatusGlyph({
  value,
  className,
}: {
  value?: string | number | null;
  className?: string;
}): React.ReactElement {
  const tone = toneOf(value == null ? "" : String(value));
  const Icon = ICONS[tone];
  return <Icon className={cn("size-3.5 shrink-0", COLOR[tone], className)} />;
}

export function PriorityGlyph({
  value,
  className,
}: {
  value?: string | number | null;
  className?: string;
}): React.ReactElement {
  const raw = (value == null ? "" : String(value)).toLowerCase();
  const urgent = /urgent|reject|high|held|fail/.test(raw);
  const high = /a\b|high/.test(raw);
  const low = /c\b|low|skip/.test(raw);
  return (
    <span
      className={cn(
        "inline-flex w-3.5 justify-center text-xs font-semibold leading-none",
        urgent || high ? "text-destructive-foreground" : low ? "text-muted-foreground" : "text-warning-foreground",
        className,
      )}
      aria-hidden
    >
      {urgent ? "!!" : high ? "↑" : low ? "↓" : "–"}
    </span>
  );
}

export function QuietStatus({
  value,
  className,
}: {
  value?: string | number | null;
  className?: string;
}): React.ReactElement {
  return (
    <span className={cn("inline-flex min-w-0 items-center gap-1.5 text-sm text-muted-foreground", className)}>
      <StatusGlyph value={value} />
      <span className="truncate">{display(value)}</span>
    </span>
  );
}

import { Circle, CircleAlert, CircleCheck, CircleDashed, CircleDot } from "lucide-react";
import { CellText } from "@/components/cell-text";
import { display, humanName } from "@/lib/format";
import { cn } from "@/lib/utils";

type Tone = "todo" | "progress" | "review" | "done" | "alert" | "backlog";

function toneOf(value?: string | null): Tone {
  const raw = (value || "").toLowerCase().trim();
  if (!raw || raw === "—") return "backlog";
  if (
    /done|valid|verified|deliverable|low|confirmed|enabled|clear|active|completed|succeeded|present|ok\b|qualified|replied|resolved|clean|free|a\b|intact|website_published|website published|pattern_verified|pattern verified/.test(
      raw,
    )
  ) {
    return "done";
  }
  if (
    /progress|running|medium|partial|pending|uncertain|finder|contacted|linkedin|public_web|public web|directory|cached|in_progress|warm|b\b/.test(
      raw,
    )
  ) {
    return "progress";
  }
  if (
    /review|paused|quota|catch|affiliation|sample|unverified|user_supplied|user supplied/.test(
      raw,
    )
  ) {
    return "review";
  }
  if (
    /held|hold|fail|failed|high|reject|rejected|broken|invalid|error|overdue|risk|unreachable|bounced|disqualified|do_not_contact|do not contact|exhausted|blocked/.test(
      raw,
    )
  ) {
    return "alert";
  }
  if (/backlog|unknown|skip|skipped|off|preview|none|missing|uncrawled/.test(raw)) return "backlog";
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
  return (
    <Icon
      data-slot="status-glyph"
      data-tone={tone}
      className={cn("size-3.5 shrink-0", COLOR[tone], className)}
    />
  );
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
  const raw = display(value);
  const shown = raw === "—" ? raw : humanName(raw);
  return (
    <span
      data-slot="quiet-status"
      className={cn("inline-flex min-w-0 max-w-full items-center gap-1.5 text-sm text-muted-foreground", className)}
    >
      <StatusGlyph value={value} />
      <CellText value={shown} className="min-w-0 text-sm text-muted-foreground" />
    </span>
  );
}

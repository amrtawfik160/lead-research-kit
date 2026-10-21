import { Circle, CircleAlert, CircleCheck, CircleDashed, CircleDot } from "lucide-react";
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
  todo: "text-[#7c7870] dark:text-[#a6a39c]",
  progress: "text-[#c88a14] dark:text-[#d8a443]",
  review: "text-[#7652c2] dark:text-[#a98deb]",
  done: "text-[#2e8456] dark:text-[#5cba86]",
  alert: "text-[#b23c30] dark:text-[#e57d70]",
  backlog: "text-[#a29e96] dark:text-[#6f6c66]",
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
        "inline-flex w-3.5 justify-center text-[11px] font-semibold leading-none",
        urgent || high ? "text-[#b23c30] dark:text-[#e57d70]" : low ? "text-muted-foreground" : "text-[#8f5e0e] dark:text-[#d8a443]",
        className,
      )}
      aria-hidden
    >
      {urgent ? "!!" : high ? "↑" : low ? "↓" : "–"}
    </span>
  );
}

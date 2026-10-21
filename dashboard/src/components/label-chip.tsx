import { labelColor } from "@/lib/gr8r";
import { titleCase } from "@/lib/format";
import { cn } from "@/lib/utils";

export function LabelChip({
  value,
  className,
}: {
  value?: string | number | null;
  className?: string;
}): React.ReactElement {
  const raw = value == null || value === "" ? "—" : String(value);
  const color = raw === "—" ? "#8a867e" : labelColor(raw);
  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 truncate text-[12px] text-muted-foreground",
        className,
      )}
    >
      <span className="size-2 shrink-0 rounded-[3px]" style={{ background: color }} />
      <span className="truncate">{raw === "—" ? raw : titleCase(raw)}</span>
    </span>
  );
}

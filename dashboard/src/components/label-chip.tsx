import { CellText } from "@/components/cell-text";
import { labelColor } from "@/lib/gr8r";
import { humanName } from "@/lib/format";
import { cn } from "@/lib/utils";

export function LabelChip({
  value,
  className,
}: {
  value?: string | number | null;
  className?: string;
}): React.ReactElement {
  const raw = value == null || value === "" ? "—" : String(value);
  const color = raw === "—" ? "var(--muted-foreground)" : labelColor(raw);
  return (
    <span
      className={cn(
        "inline-flex max-w-full min-w-0 items-center gap-1.5 truncate text-sm text-muted-foreground",
        className,
      )}
    >
      <span className="size-2 shrink-0 rounded-[3px]" style={{ background: color }} />
      <CellText value={raw === "—" ? raw : humanName(raw)} className="min-w-0 text-sm text-muted-foreground" />
    </span>
  );
}

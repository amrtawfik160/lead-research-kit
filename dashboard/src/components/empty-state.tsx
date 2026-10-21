import type { LucideIcon } from "lucide-react";
import { Users } from "lucide-react";
import { cn } from "@/lib/utils";

export function EmptyState({
  icon: Icon = Users,
  title,
  description,
  compact = false,
  className,
}: {
  icon?: LucideIcon;
  title: string;
  description?: string;
  compact?: boolean;
  className?: string;
}): React.ReactElement {
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-1.5 text-center",
        compact ? "px-4 py-7" : "px-6 py-14",
        className,
      )}
      data-slot="empty-state"
    >
      <div className="mb-1 grid size-[22px] place-items-center text-[#8c8880] dark:text-[#6f6c66]">
        <Icon className="size-[22px]" />
      </div>
      <h2 className="m-0 text-[14.5px] font-semibold tracking-[-0.01em]">{title}</h2>
      {description ? (
        <p className="m-0 max-w-[340px] text-[12.5px] leading-5 text-[#57544e] dark:text-[#b5b2ab]">{description}</p>
      ) : null}
    </div>
  );
}

import type { LucideIcon } from "lucide-react";
import { Users } from "lucide-react";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
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
    <Empty
      className={cn(compact ? "min-h-0 flex-none gap-2 px-4 py-6 md:py-6" : "py-12 md:py-16", className)}
      data-slot="empty-state"
    >
      <EmptyHeader>
        <EmptyMedia variant="icon" className={compact ? "mb-2" : "mb-3"}>
          <Icon />
        </EmptyMedia>
        <EmptyTitle className={compact ? "text-base font-semibold" : undefined}>{title}</EmptyTitle>
        {description ? (
          <EmptyDescription className="max-w-[360px]">{description}</EmptyDescription>
        ) : null}
      </EmptyHeader>
    </Empty>
  );
}

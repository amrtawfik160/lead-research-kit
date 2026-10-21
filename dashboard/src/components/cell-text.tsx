"use client";

import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip";
import { display } from "@/lib/format";
import { cn } from "@/lib/utils";

export function CellText({
  value,
  className,
  mono = false,
}: {
  value: unknown;
  className?: string;
  mono?: boolean;
}): React.ReactElement {
  const shown = display(value);
  const classes = cn("block min-w-0 truncate", mono && "font-mono text-xs", className);
  if (shown === "—" || shown.length <= 28) {
    return <span className={classes}>{shown}</span>;
  }
  return (
    <Tooltip>
      <TooltipTrigger render={<span className={classes} />}>{shown}</TooltipTrigger>
      <TooltipPopup className="max-w-xs text-xs">{shown}</TooltipPopup>
    </Tooltip>
  );
}

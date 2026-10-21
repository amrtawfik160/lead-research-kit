import type { CSSProperties } from "react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { initials, labelColor } from "@/lib/gr8r";
import { cn } from "@/lib/utils";

export function EntityAvatar({
  name,
  size = 22,
  className,
}: {
  name: string;
  size?: number;
  className?: string;
}): React.ReactElement {
  const color = labelColor(name || "V");
  return (
    <Avatar
      className={cn("text-[9px] font-semibold", className)}
      style={{ "--avatar-size": `${size}px`, background: `color-mix(in srgb, ${color} 17%, var(--background))`, color: `color-mix(in srgb, ${color} 55%, var(--foreground))` } as CSSProperties}
    >
      <AvatarFallback className="bg-transparent">{initials(name)}</AvatarFallback>
    </Avatar>
  );
}

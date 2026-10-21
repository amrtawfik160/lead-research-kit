import type { ComponentProps } from "react";
import { Badge } from "@/components/ui/badge";
import { titleCase } from "@/lib/format";

const VARIANT: Record<string, ComponentProps<typeof Badge>["variant"]> = {
  low: "success",
  high: "success",
  medium: "warning",
  A: "success",
  B: "info",
  C: "warning",
  reject: "error",
  completed: "success",
  provider_valid: "success",
  valid: "success",
  confirmed: "success",
  enabled: "success",
  funded: "success",
  present: "success",
  yes: "success",
  active: "success",
  clear: "secondary",
  pending: "warning",
  uncertain: "warning",
  partial: "warning",
  paused: "warning",
  http_error: "warning",
  provider_error: "warning",
  quota: "warning",
  catch_all: "warning",
  reserved: "info",
  sent: "info",
  queued: "info",
  website: "info",
  website_published: "info",
  public_record: "secondary",
  finder: "warning",
  hunter_finder: "warning",
  pattern_verified: "success",
  osm: "secondary",
  linkedin: "info",
  search: "secondary",
  failed: "error",
  cancelled: "error",
  provider_invalid: "error",
  invalid: "error",
  held: "error",
  missing: "error",
  no: "secondary",
  skipped: "secondary",
  unknown: "secondary",
  preview: "secondary",
  off: "secondary",
};

export function StatusBadge({
  value,
  fallback = "secondary",
}: {
  value?: string | number | null;
  fallback?: ComponentProps<typeof Badge>["variant"];
}): React.ReactElement {
  const raw = value == null || value === "" ? "—" : String(value);
  const variant = VARIANT[raw] || VARIANT[raw.toLowerCase()] || fallback;
  return (
    <Badge size="sm" variant={variant}>
      {raw === "—" ? raw : titleCase(raw)}
    </Badge>
  );
}

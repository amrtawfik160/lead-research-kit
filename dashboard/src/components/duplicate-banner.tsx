import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { duplicateSummary } from "@/lib/lead-ops";
import type { DuplicateWarning } from "@/lib/types";

export function DuplicateBanner({
  items,
  className,
}: {
  items?: DuplicateWarning[];
  className?: string;
}): React.ReactElement | null {
  if (!items?.length) return null;
  return (
    <Alert variant="warning" className={className || "mb-3"}>
      <AlertTitle>Duplicate emails</AlertTitle>
      <AlertDescription>
        {duplicateSummary(items)}. Same address across deliveries or history — review before outreach.
      </AlertDescription>
    </Alert>
  );
}

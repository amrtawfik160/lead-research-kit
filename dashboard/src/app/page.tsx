import { OverviewPage } from "@/components/pages/overview-page";
import { loadApi } from "@/lib/load";
import type { Overview } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Page(): Promise<React.ReactElement> {
  const initial = await loadApi<Overview>("/api/overview");
  return <OverviewPage initial={initial} />;
}

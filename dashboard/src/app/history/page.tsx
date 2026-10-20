import { HistoryPage } from "@/components/pages/history-page";
import { loadApi, type HistoryPayload } from "@/lib/load";

export const dynamic = "force-dynamic";

export default async function Page(): Promise<React.ReactElement> {
  return <HistoryPage initial={await loadApi<HistoryPayload>("/api/history")} />;
}

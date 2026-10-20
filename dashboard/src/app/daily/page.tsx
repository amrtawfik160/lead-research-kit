import { DailyPage } from "@/components/pages/daily-page";
import { loadDaily } from "@/lib/load";

export const dynamic = "force-dynamic";

export default async function Page(): Promise<React.ReactElement> {
  return <DailyPage initial={await loadDaily()} />;
}

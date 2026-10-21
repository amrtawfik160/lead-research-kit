import type { Metadata } from "next";
import { LeadsPage } from "@/components/pages/leads-page";
import { loadDaily } from "@/lib/load";

export const metadata: Metadata = { title: "Leads" };
export const dynamic = "force-dynamic";

export default async function Page(): Promise<React.ReactElement> {
  return <LeadsPage initial={await loadDaily()} />;
}

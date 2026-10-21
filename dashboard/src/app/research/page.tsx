import type { Metadata } from "next";
import { ResearchPage } from "@/components/pages/research-page";
import { loadApi, type ResearchPayload } from "@/lib/load";

export const metadata: Metadata = { title: "Research" };
export const dynamic = "force-dynamic";

export default async function Page(): Promise<React.ReactElement> {
  return <ResearchPage initial={await loadApi<ResearchPayload>("/api/research")} />;
}

import type { Metadata } from "next";
import { CompaniesPage } from "@/components/pages/companies-page";
import { loadApi, type CompaniesPayload } from "@/lib/load";

export const metadata: Metadata = { title: "Companies" };
export const dynamic = "force-dynamic";

export default async function Page(): Promise<React.ReactElement> {
  return <CompaniesPage initial={await loadApi<CompaniesPayload>("/api/companies")} />;
}

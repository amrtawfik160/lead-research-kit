import type { Metadata } from "next";
import { ProvidersPage } from "@/components/pages/providers-page";
import { loadApi, type ProvidersPayload } from "@/lib/load";

export const metadata: Metadata = { title: "Providers" };
export const dynamic = "force-dynamic";

export default async function Page(): Promise<React.ReactElement> {
  return <ProvidersPage initial={await loadApi<ProvidersPayload>("/api/providers")} />;
}

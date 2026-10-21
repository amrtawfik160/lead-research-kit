import type { Metadata } from "next";
import { SettingsPage } from "@/components/pages/settings-page";
import { loadApi, type SettingsPayload } from "@/lib/load";

export const metadata: Metadata = { title: "Settings" };
export const dynamic = "force-dynamic";

export default async function Page(): Promise<React.ReactElement> {
  return <SettingsPage initial={await loadApi<SettingsPayload>("/api/settings")} />;
}

import type { Metadata } from "next";
import { BudgetPage } from "@/components/pages/budget-page";
import { loadApi, type BudgetPayload } from "@/lib/load";

export const metadata: Metadata = { title: "Budget" };
export const dynamic = "force-dynamic";

export default async function Page(): Promise<React.ReactElement> {
  return <BudgetPage initial={await loadApi<BudgetPayload>("/api/budget")} />;
}

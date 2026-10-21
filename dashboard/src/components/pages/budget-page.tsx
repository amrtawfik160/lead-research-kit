"use client";

import { useState } from "react";
import { DataTable } from "@/components/data-table";
import { DetailSheet } from "@/components/detail-sheet";
import { ChartCard, HorizontalBarChart, QuotaChart } from "@/components/workspace-charts";
import { PageBody, PageHeader, PageShell } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { api } from "@/lib/api";
import { chartPool, display, humanName, humanPool } from "@/lib/format";
import type { BudgetPayload } from "@/lib/load";

export function BudgetPage({ initial = null }: { initial?: BudgetPayload | null }): React.ReactElement {
  const { data, error, loading } = useQuery(() => api.budget(), [], initial);
  const [row, setRow] = useState<(BudgetPayload["budgets"][number]) | null>(null);

  return (
    <PageShell>
      <PageHeader
        title="Budget"
        description="Attested free pools, remaining units, and charges from completed local attempts. The dashboard never shows keys."
      />
      <QueryGate loading={loading} error={error} empty={!data}>
        {data ? (
          <PageBody>
            <div className="grid gap-4 pb-4 md:grid-cols-2">
              <ChartCard title="Spent vs remaining">
                <QuotaChart
                  rows={data.budgets.slice(0, 6).map((budget) => ({
                    label: chartPool(`${budget.provider}.${budget.pool}`),
                    remaining: Number(budget.remaining) || 0,
                    spent: Number(budget.spent) || 0,
                  }))}
                />
              </ChartCard>
              <ChartCard title="Refills">
                <HorizontalBarChart
                  rows={data.refills.slice(0, 6).map((item) => ({
                    label: chartPool(`${item.provider}.${item.pool}`),
                    value: Number(item.amount) || 0,
                  }))}
                  emptyTitle="No recurring refills"
                  emptyDescription="Record a refill on the CLI to chart it here."
                />
              </ChartCard>
            </div>
            <DataTable
              rows={data.budgets}
              rowKey={(item) => `${item.provider}-${item.pool}`}
              onRowClick={setRow}
              selectedKey={row ? `${row.provider}-${row.pool}` : undefined}
              exportFilename="veinore-budgets.csv"
              emptyTitle="No attested budgets"
              emptyDescription="Confirm a free pool on the CLI before the dashboard can chart it."
              columns={[
                { key: "provider", header: "Provider", width: "10rem", value: (item) => humanName(item.provider) },
                { key: "pool", header: "Pool", width: "12rem", value: (item) => humanName(item.pool) },
                { key: "remaining", header: "Remaining", width: "8rem", mono: true },
                { key: "spent", header: "Spent", width: "8rem", mono: true, value: (item) => item.spent || "0" },
                { key: "source", header: "Source", value: (item) => humanName(String(item.source || "")) },
              ]}
            />
            <DetailSheet
              open={Boolean(row)}
              onOpenChange={(open) => !open && setRow(null)}
              title={row ? humanPool(`${row.provider}.${row.pool}`) : "Budget pool"}
              description="Attested remaining and local spend"
              sections={[
                {
                  title: "Pool",
                  fields: [
                    { label: "Remaining", value: display(row?.remaining) },
                    { label: "Spent", value: display(row?.spent) },
                    { label: "Quota", value: display(row?.quota) },
                    { label: "Source", value: display(row?.source) },
                  ],
                },
              ]}
            />
          </PageBody>
        ) : null}
      </QueryGate>
    </PageShell>
  );
}

"use client";

import { useState } from "react";
import { DataTable } from "@/components/data-table";
import { DetailSheet } from "@/components/detail-sheet";
import { HBars } from "@/components/mini-charts";
import { PageBody, PageHeader } from "@/components/page-header";
import { Panel, PanelHeader } from "@/components/panel";
import { QueryGate, useQuery } from "@/components/query-state";
import { api } from "@/lib/api";
import { display } from "@/lib/format";
import type { BudgetPayload } from "@/lib/load";

export function BudgetPage({ initial = null }: { initial?: BudgetPayload | null }): React.ReactElement {
  const { data, error, loading } = useQuery(() => api.budget(), [], initial);
  const [row, setRow] = useState<(BudgetPayload["budgets"][number]) | null>(null);

  return (
    <>
      <PageHeader
        title="Budget"
        description="Attested free pools, remaining units, and charges from completed local attempts. The dashboard never shows keys."
      />
      <QueryGate loading={loading} error={error} empty={!data}>
        {data ? (
          <PageBody className="flex min-h-0 flex-1 flex-col">
            <div className="grid gap-3 px-6 pb-3 md:grid-cols-2">
              <Panel>
                <PanelHeader title="Spent vs remaining" />
                <div className="px-4 pb-4">
                  <HBars
                    items={data.budgets.slice(0, 8).map((budget) => ({
                      label: `${budget.provider}.${budget.pool}`,
                      value: Number(budget.spent_units || 0),
                      max: Number(budget.quota_units || budget.remaining_units || 1),
                      hint: `${String(budget.spent || "0").replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "")} / ${budget.quota || budget.remaining}`,
                    }))}
                  />
                </div>
              </Panel>
              <Panel>
                <PanelHeader title="Refills" />
                <div className="px-4 pb-4 text-[12.5px]">
                  {data.refills.length === 0 ? (
                    <p className="text-muted-foreground">No recurring refills recorded.</p>
                  ) : (
                    <ul className="grid gap-1.5">
                      {data.refills.slice(0, 8).map((item) => (
                        <li key={`${item.provider}-${item.pool}`} className="flex justify-between gap-3">
                          <span className="truncate text-[#57544e] dark:text-[#b5b2ab]">
                            {display(item.provider)} · {display(item.pool)}
                          </span>
                          <span className="tabular-nums text-muted-foreground">{display(item.amount)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </Panel>
            </div>
            <DataTable
              rows={data.budgets}
              rowKey={(item) => `${item.provider}-${item.pool}`}
              onRowClick={setRow}
              selectedKey={row ? `${row.provider}-${row.pool}` : undefined}
              emptyTitle="No attested budgets"
              emptyDescription="Confirm a free pool on the CLI before the dashboard can chart it."
              columns={[
                { key: "provider", header: "Provider", width: "10rem", mono: true },
                { key: "pool", header: "Pool", width: "10rem", mono: true },
                { key: "remaining", header: "Remaining", width: "8rem", mono: true },
                { key: "spent", header: "Spent", width: "8rem", mono: true, value: (item) => item.spent || "0" },
                { key: "source", header: "Source" },
              ]}
            />
            <DetailSheet
              open={Boolean(row)}
              onOpenChange={(open) => !open && setRow(null)}
              title={`${display(row?.provider)} · ${display(row?.pool)}`}
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
    </>
  );
}

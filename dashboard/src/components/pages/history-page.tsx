"use client";

import { useState } from "react";
import { Card, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
import { DataTable } from "@/components/data-table";
import { DetailSheet } from "@/components/detail-sheet";
import { PageHeader } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import { display, formatTime } from "@/lib/format";
import type { HistoryPayload } from "@/lib/load";

export function HistoryPage({ initial = null }: { initial?: HistoryPayload | null }): React.ReactElement {
  const { data, error, loading } = useQuery(() => api.history(), [], initial);
  const [row, setRow] = useState<Record<string, unknown> | null>(null);
  const patterns = Object.entries(data?.patterns || {}).map(([domain, info]) => ({
    domain,
    ...info,
  }));
  const sources = Object.entries(data?.source_quality || {}).map(([source, info]) => ({
    source,
    ...info,
  }));

  return (
    <>
      <PageHeader
        title="History"
        description="Delivered keys, suppressions, outreach outcomes, distrusted guess patterns, and source quality."
        crumbs={[{ href: "/", label: "Overview" }, { label: "History" }]}
      />
      <QueryGate loading={loading} error={error} empty={!data}>
        {data ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="grid gap-3 px-4 pt-4 sm:grid-cols-4 sm:px-6">
              <Mini label="Delivered keys" value={data.stats.delivered_keys} />
              <Mini label="Suppressed" value={data.stats.suppressed} />
              <Mini label="Outcomes" value={data.stats.outcomes} />
              <Mini label="Distrusted patterns" value={data.stats.patterns_distrusted} />
            </div>
            <Tabs defaultValue="delivered" className="flex min-h-0 flex-1 flex-col px-4 py-3 sm:px-6">
              <TabsList variant="underline" size="sm">
                <TabsTab value="delivered">Delivered</TabsTab>
                <TabsTab value="suppressed">Suppressed</TabsTab>
                <TabsTab value="outcomes">Outcomes</TabsTab>
                <TabsTab value="patterns">Patterns</TabsTab>
                <TabsTab value="sources">Sources</TabsTab>
              </TabsList>
              <TabsPanel value="delivered" className="min-h-0 flex-1 pt-3">
                <DataTable
                  rows={data.delivered}
                  rowKey={(item) =>
                    `${item.batch}-${item.full_name}-${item.email}-${item.email_source}-${item.source}-${item.company_website}`
                  }
                  onRowClick={setRow}
                  groupOptions={[{ value: "batch", label: "Batch" }, { value: "email_source", label: "Email source" }]}
                  columns={[
                    { key: "full_name", header: "Name" },
                    { key: "email", header: "Email" },
                    { key: "company_website", header: "Website" },
                    { key: "batch", header: "Batch" },
                    { key: "email_source", header: "Email source" },
                    { key: "source", header: "Source" },
                  ]}
                />
              </TabsPanel>
              <TabsPanel value="suppressed" className="min-h-0 flex-1 pt-3">
                <DataTable
                  rows={data.suppressed}
                  rowKey={(item) => String(item.key)}
                  onRowClick={setRow}
                  columns={[
                    { key: "key", header: "Key" },
                    { key: "reason", header: "Reason", render: (item) => <StatusBadge value={String(item.reason)} /> },
                    { key: "added_at", header: "Added", render: (item) => formatTime(Number(item.added_at) || null) },
                  ]}
                />
              </TabsPanel>
              <TabsPanel value="outcomes" className="min-h-0 flex-1 pt-3">
                <DataTable
                  rows={data.outcomes}
                  rowKey={(item) => `${item.key}-${item.outcome}`}
                  onRowClick={setRow}
                  groupOptions={[{ value: "outcome", label: "Outcome" }]}
                  columns={[
                    { key: "key", header: "Key" },
                    { key: "outcome", header: "Outcome", render: (item) => <StatusBadge value={String(item.outcome)} /> },
                    { key: "email_source", header: "Email source" },
                    { key: "batch", header: "Batch" },
                  ]}
                />
              </TabsPanel>
              <TabsPanel value="patterns" className="min-h-0 flex-1 pt-3">
                <DataTable
                  rows={patterns}
                  rowKey={(item) => String(item.domain)}
                  onRowClick={setRow}
                  columns={[
                    { key: "domain", header: "Domain" },
                    { key: "reason", header: "Reason" },
                    { key: "bounces", header: "Bounces" },
                  ]}
                />
              </TabsPanel>
              <TabsPanel value="sources" className="min-h-0 flex-1 pt-3">
                <DataTable
                  rows={sources}
                  rowKey={(item) => String(item.source)}
                  onRowClick={setRow}
                  columns={[
                    { key: "source", header: "Source" },
                    { key: "delivered", header: "Delivered" },
                    { key: "bounced", header: "Bounced" },
                    { key: "replied", header: "Replied" },
                    { key: "bounce_rate", header: "Bounce rate" },
                    { key: "reply_rate", header: "Reply rate" },
                  ]}
                />
              </TabsPanel>
            </Tabs>
          </div>
        ) : null}
      </QueryGate>
      <DetailSheet
        open={Boolean(row)}
        onOpenChange={(open) => !open && setRow(null)}
        title="History row"
        fields={Object.entries(row || {}).map(([label, value]) => ({ label, value: display(value) }))}
      />
    </>
  );
}

function Mini({ label, value }: { label: string; value?: number }): React.ReactElement {
  return (
    <Card>
      <CardHeader className="p-4">
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-xl">{value ?? 0}</CardTitle>
      </CardHeader>
      <CardPanel className="hidden" />
    </Card>
  );
}

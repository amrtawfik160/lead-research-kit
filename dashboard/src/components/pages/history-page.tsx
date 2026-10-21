"use client";

import { useState } from "react";
import { DataTable } from "@/components/data-table";
import { DetailSheet } from "@/components/detail-sheet";
import { PageBody, PageHeader, PageShell } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { QuietStatus } from "@/components/status-glyph";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { display, formatTime, humanName } from "@/lib/format";
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
    <PageShell>
      <PageHeader
        title="History"
        description="Delivered keys, suppressions, outreach outcomes, distrusted guess patterns, and source quality."
        crumbs={[{ href: "/", label: "Overview" }, { label: "History" }]}
      />
      <QueryGate loading={loading} error={error} empty={!data} skeleton="cards">
        {data ? (
          <PageBody>
            <div className="grid gap-4 sm:grid-cols-4">
              <Mini label="Delivered keys" value={data.stats.delivered_keys} />
              <Mini label="Suppressed" value={data.stats.suppressed} />
              <Mini label="Outcomes" value={data.stats.outcomes} />
              <Mini label="Distrusted patterns" value={data.stats.patterns_distrusted} />
            </div>
            <Tabs defaultValue="delivered" className="flex min-h-0 flex-1 flex-col pt-2">
              <TabsList variant="underline" size="sm">
                <TabsTab value="delivered">Delivered</TabsTab>
                <TabsTab value="suppressed">Suppressed</TabsTab>
                <TabsTab value="outcomes">Outcomes</TabsTab>
                <TabsTab value="patterns">Patterns</TabsTab>
                <TabsTab value="sources">Sources</TabsTab>
              </TabsList>
              <TabsPanel value="delivered" className="min-h-0 flex-1 pt-4">
                <DataTable
                  rows={data.delivered}
                  rowKey={(item) =>
                    String(item.key || `${item.batch}-${item.full_name}-${item.email}-${item.email_source}-${item.source}-${item.company_website}`)
                  }
                  onRowClick={setRow}
                  exportFilename="veinore-history-delivered.csv"
                  emptyTitle="Nothing delivered yet"
                  emptyDescription="An executed daily run writes keys here."
                  groupOptions={[
                    { value: "batch", label: "Batch" },
                    { value: "email_source", label: "Email source" },
                  ]}
                  columns={[
                    { key: "full_name", header: "Name", width: "11rem" },
                    { key: "email", header: "Email", width: "14rem", mono: true },
                    { key: "company_website", header: "Website", width: "12rem", mono: true },
                    { key: "batch", header: "Batch", width: "8rem" },
                    {
                      key: "email_source",
                      header: "Source",
                      width: "11rem",
                      value: (item) => humanName(String(item.email_source || item.source || "")),
                      render: (item) => <QuietStatus value={String(item.email_source || item.source || "")} />,
                    },
                    {
                      key: "key_count",
                      header: "Keys",
                      width: "6.5rem",
                      align: "right",
                      value: (item) => Number(item.key_count || 1),
                    },
                  ]}
                />
              </TabsPanel>
              <TabsPanel value="suppressed" className="min-h-0 flex-1 pt-4">
                <DataTable
                  rows={data.suppressed}
                  rowKey={(item) => String(item.key)}
                  onRowClick={setRow}
                  exportFilename="veinore-history-suppressed.csv"
                  emptyTitle="No suppressions"
                  emptyDescription="Do-not-contact and bounce blocks appear here."
                  columns={[
                    { key: "key", header: "Key", width: "16rem", mono: true },
                    {
                      key: "reason",
                      header: "Reason",
                      width: "10rem",
                      render: (item) => <QuietStatus value={String(item.reason)} />,
                    },
                    {
                      key: "added_at",
                      header: "Added",
                      width: "10rem",
                      render: (item) => formatTime(Number(item.added_at) || null),
                    },
                  ]}
                />
              </TabsPanel>
              <TabsPanel value="outcomes" className="min-h-0 flex-1 pt-4">
                <DataTable
                  rows={data.outcomes}
                  rowKey={(item) => `${item.key}-${item.outcome}`}
                  onRowClick={setRow}
                  exportFilename="veinore-history-outcomes.csv"
                  emptyTitle="No outreach outcomes"
                  emptyDescription="Import send results to score sources."
                  groupOptions={[{ value: "outcome", label: "Outcome" }]}
                  columns={[
                    { key: "key", header: "Key", width: "16rem", mono: true },
                    {
                      key: "outcome",
                      header: "Outcome",
                      width: "8rem",
                      render: (item) => <QuietStatus value={String(item.outcome)} />,
                    },
                    { key: "email_source", header: "Email source", width: "10rem", value: (item) => humanName(String(item.email_source || "")) },
                    { key: "batch", header: "Batch", width: "8rem" },
                  ]}
                />
              </TabsPanel>
              <TabsPanel value="patterns" className="min-h-0 flex-1 pt-4">
                <DataTable
                  rows={patterns}
                  rowKey={(item) => String(item.domain)}
                  onRowClick={setRow}
                  exportFilename="veinore-history-patterns.csv"
                  emptyTitle="No distrusted patterns"
                  emptyDescription="Hard bounces on guessed emails mark a domain here."
                  columns={[
                    { key: "domain", header: "Domain", width: "14rem", mono: true },
                    { key: "reason", header: "Reason" },
                    { key: "bounces", header: "Bounces", width: "7rem", align: "right" },
                  ]}
                />
              </TabsPanel>
              <TabsPanel value="sources" className="min-h-0 flex-1 pt-4">
                <DataTable
                  rows={sources}
                  rowKey={(item) => String(item.source)}
                  onRowClick={setRow}
                  exportFilename="veinore-history-sources.csv"
                  emptyTitle="No source quality yet"
                  emptyDescription="Outcomes roll up bounce and reply rates per email source."
                  columns={[
                    { key: "source", header: "Source", width: "11rem", value: (item) => humanName(String(item.source || "")) },
                    { key: "delivered", header: "Delivered", width: "7rem", align: "right" },
                    { key: "bounced", header: "Bounced", width: "7rem", align: "right" },
                    { key: "replied", header: "Replied", width: "7rem", align: "right" },
                    { key: "bounce_rate", header: "Bounce rate", width: "8rem", align: "right" },
                    { key: "reply_rate", header: "Reply rate", width: "8rem", align: "right" },
                  ]}
                />
              </TabsPanel>
            </Tabs>
          </PageBody>
        ) : null}
      </QueryGate>
      <DetailSheet
        open={Boolean(row)}
        onOpenChange={(open) => !open && setRow(null)}
        title="History row"
        issueId={display(row?.email || row?.domain || row?.id)}
        fields={Object.entries(row || {}).map(([label, value]) => ({ label, value: display(value) }))}
      />
    </PageShell>
  );
}

function Mini({ label, value }: { label: string; value?: number }): React.ReactElement {
  return (
    <Card>
      <CardHeader className="gap-1.5 px-4 py-3">
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-xl font-medium tabular-nums">{value ?? 0}</CardTitle>
      </CardHeader>
    </Card>
  );
}

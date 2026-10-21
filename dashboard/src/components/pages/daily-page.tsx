"use client";

import { useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
import { DataTable } from "@/components/data-table";
import { DetailSheet } from "@/components/detail-sheet";
import { PageHeader } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import { display } from "@/lib/format";
import type { DailyPayload, LeadRow } from "@/lib/types";

export function DailyPage({ initial = null }: { initial?: DailyPayload | null }): React.ReactElement {
  const [date, setDate] = useState<string | undefined>(undefined);
  const { data, error, loading } = useQuery(() => api.daily(date), [date], initial);
  const [lead, setLead] = useState<LeadRow | null>(null);

  return (
    <>
      <PageHeader
        title="Daily"
        description="Dated delivery folders: leads.csv, details, report, and optional spot-check."
        crumbs={[{ href: "/", label: "Overview" }, { label: "Daily" }]}
      />
      <QueryGate loading={loading} error={error} empty={!data}>
        {data ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex flex-wrap gap-2 border-b px-4 py-3">
              {data.batches.map((batch) => (
                <button
                  key={batch.date}
                  type="button"
                  onClick={() => setDate(batch.date)}
                  className={`rounded-md border px-2 py-1 text-xs ${
                    batch.date === data.date ? "bg-accent text-accent-foreground" : "hover:bg-muted"
                  }`}
                >
                  {batch.date} · {batch.delivered}/{batch.target}
                  {batch.has_audit ? " · audit" : ""}
                </button>
              ))}
            </div>
            <Tabs defaultValue={data.leads.length ? "leads" : "report"} className="min-h-0 flex-1 px-4 py-3 sm:px-6">
              <TabsList variant="underline" size="sm">
                <TabsTab value="leads">Leads</TabsTab>
                <TabsTab value="report">Report</TabsTab>
                <TabsTab value="audit">Audit</TabsTab>
              </TabsList>
              <TabsPanel value="leads" className="min-h-0 flex-1 pt-3">
                <DataTable
                  rows={data.leads}
                  rowKey={(row) => `${row["Full Name"]}-${row.Email}-${row.Company}`}
                  onRowClick={setLead}
                  groupOptions={[
                    { value: "Source", label: "Source" },
                    { value: "Company Type", label: "Company type" },
                    { value: "Confidence", label: "Confidence" },
                    { value: "Email Risk", label: "Email risk" },
                    { value: "State", label: "State" },
                  ]}
                  columns={[
                    { key: "Full Name", header: "Name" },
                    { key: "Title", header: "Title" },
                    { key: "Company", header: "Company" },
                    { key: "Email", header: "Email" },
                    { key: "Source", header: "Source", render: (row) => <StatusBadge value={row.Source} /> },
                    { key: "Email Risk", header: "Risk", render: (row) => <StatusBadge value={row["Email Risk"]} /> },
                    { key: "Confidence", header: "Conf.", render: (row) => <StatusBadge value={row.Confidence} /> },
                    { key: "State", header: "ST" },
                  ]}
                />
              </TabsPanel>
              <TabsPanel value="report" className="pt-3">
                <pre className="max-w-3xl whitespace-pre-wrap rounded-xl border bg-card p-4 font-mono text-xs leading-5">
                  {data.markdown || "No report.md"}
                </pre>
              </TabsPanel>
              <TabsPanel value="audit" className="pt-3">
                {data.audit ? (
                  <div className="flex max-w-3xl flex-col gap-3">
                    <Alert variant="info">
                      <AlertTitle>Spot-check</AlertTitle>
                      <AlertDescription>
                        {data.audit.sampled} sampled · confirmed {data.audit.score?.confirmed_pct}% · partial{" "}
                        {data.audit.score?.partial_pct}% · failed {data.audit.score?.failed_pct}%
                      </AlertDescription>
                    </Alert>
                    <div className="flex flex-wrap gap-2">
                      {Object.entries(data.audit.top_reasons || {}).map(([reason, count]) => (
                        <Badge key={reason} size="sm" variant="outline">
                          {reason} {count}
                        </Badge>
                      ))}
                    </div>
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">No audit.json. Run `daily audit --sample 20`.</p>
                )}
              </TabsPanel>
            </Tabs>
            <DetailSheet
              open={Boolean(lead)}
              onOpenChange={(open) => !open && setLead(null)}
              title={display(lead?.["Full Name"])}
              description={display(lead?.Company)}
              fields={Object.entries(lead || {}).map(([label, value]) => ({
                label,
                value: display(value),
              }))}
            />
          </div>
        ) : null}
      </QueryGate>
    </>
  );
}

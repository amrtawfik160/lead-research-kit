"use client";

import { useState } from "react";
import { CountBar } from "@/components/count-bar";
import { DataTable } from "@/components/data-table";
import { DetailSheet } from "@/components/detail-sheet";
import { PageBody, PageHeader } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { StatusBadge } from "@/components/status-badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Progress, ProgressIndicator, ProgressTrack } from "@/components/ui/progress";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { display } from "@/lib/format";
import { leadFieldSections } from "@/lib/lead-fields";
import type { DailyPayload, LeadRow } from "@/lib/types";
import { cn } from "@/lib/utils";

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
          <PageBody className="flex min-h-0 flex-1 flex-col">
            <div className="flex flex-wrap gap-1.5 border-b px-4 py-2.5">
              {data.batches.map((batch) => {
                const selected = batch.date === data.date;
                return (
                  <button
                    key={batch.date}
                    type="button"
                    onClick={() => setDate(batch.date)}
                    className={cn(
                      "inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-[11px] transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                      selected
                        ? "border-ring/50 bg-accent text-accent-foreground"
                        : "text-muted-foreground hover:bg-muted hover:text-foreground",
                    )}
                  >
                    <span className="font-medium tabular-nums">{batch.date}</span>
                    <span className="tabular-nums">
                      {batch.delivered}/{batch.target}
                    </span>
                    {batch.has_audit ? (
                      <Badge size="sm" variant="success">
                        audit
                      </Badge>
                    ) : null}
                  </button>
                );
              })}
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
                  emptyTitle="No leads this day"
                  emptyDescription="Run daily --execute, or pick another dated folder."
                  groupOptions={[
                    { value: "Source", label: "Source" },
                    { value: "Company Type", label: "Company type" },
                    { value: "Confidence", label: "Confidence" },
                    { value: "Email Risk", label: "Email risk" },
                    { value: "State", label: "State" },
                  ]}
                  columns={[
                    { key: "Full Name", header: "Name", width: "11rem" },
                    { key: "Title", header: "Title", width: "11rem" },
                    { key: "Company", header: "Company", width: "13rem" },
                    { key: "Email", header: "Email", width: "14rem", mono: true },
                    { key: "Source", header: "Source", width: "7rem", render: (row) => <StatusBadge value={row.Source} /> },
                    {
                      key: "Email Risk",
                      header: "Risk",
                      width: "6rem",
                      render: (row) => <StatusBadge value={row["Email Risk"]} />,
                    },
                    {
                      key: "Confidence",
                      header: "Conf.",
                      width: "6rem",
                      render: (row) => <StatusBadge value={row.Confidence} />,
                    },
                    { key: "State", header: "ST", width: "3.5rem" },
                  ]}
                />
              </TabsPanel>
              <TabsPanel value="report" className="pt-3">
                <ReportView data={data} />
              </TabsPanel>
              <TabsPanel value="audit" className="pt-3">
                {data.audit ? (
                  <div className="flex max-w-3xl flex-col gap-4">
                    <div className="grid gap-3 sm:grid-cols-3">
                      <ScoreCard label="Confirmed" value={data.audit.score?.confirmed_pct} tone="ok" />
                      <ScoreCard label="Partial" value={data.audit.score?.partial_pct} tone="warn" />
                      <ScoreCard label="Failed" value={data.audit.score?.failed_pct} tone="bad" />
                    </div>
                    <Alert variant="info">
                      <AlertTitle>Spot-check</AlertTitle>
                      <AlertDescription>
                        {data.audit.sampled} sampled of {data.leads.length} delivered leads.
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
                  <p className="text-[12px] text-muted-foreground">No audit.json. Run `daily audit --sample 20`.</p>
                )}
              </TabsPanel>
            </Tabs>
            <DetailSheet
              open={Boolean(lead)}
              onOpenChange={(open) => !open && setLead(null)}
              title={display(lead?.["Full Name"])}
              description={display(lead?.Company)}
              sections={leadFieldSections(lead)}
            />
          </PageBody>
        ) : null}
      </QueryGate>
    </>
  );
}

function ReportView({ data }: { data: DailyPayload }): React.ReactElement {
  const report = data.report || {};
  const delivered = report.delivered ?? data.leads.length;
  const target = report.target ?? 0;
  const pct = target ? Math.min(100, Math.round((delivered / target) * 100)) : 0;
  return (
    <div className="grid max-w-4xl gap-3">
      <Card>
        <CardHeader className="px-5 py-4">
          <CardTitle className="text-sm font-medium">
            Leads {data.date}: {delivered} of {target || "—"} delivered
          </CardTitle>
          <CardDescription>
            {report.this_run != null
              ? `This run added ${report.this_run} (already on file today: ${report.prior_delivered ?? 0}).`
              : report.execute
                ? "Executed delivery"
                : "Preview only"}
          </CardDescription>
        </CardHeader>
        <CardPanel className="px-5 pb-5">
          <Progress value={pct}>
            <ProgressTrack className="h-1.5">
              <ProgressIndicator className="bg-[#5e6ad2]" />
            </ProgressTrack>
          </Progress>
        </CardPanel>
      </Card>
      <div className="grid gap-3 md:grid-cols-2">
        <Card>
          <CardHeader className="px-5 py-4">
            <CardTitle className="text-sm font-medium">Email sources</CardTitle>
          </CardHeader>
          <CardPanel className="px-5 pb-5">
            <CountBar counts={report.delivered_by_source} />
          </CardPanel>
        </Card>
        <Card>
          <CardHeader className="px-5 py-4">
            <CardTitle className="text-sm font-medium">Email risk</CardTitle>
          </CardHeader>
          <CardPanel className="px-5 pb-5">
            <CountBar counts={report.delivered_by_risk} />
          </CardPanel>
        </Card>
      </div>
      {report.budgets_left ? (
        <Card>
          <CardHeader className="px-5 py-4">
            <CardTitle className="text-sm font-medium">Budgets left</CardTitle>
          </CardHeader>
          <CardPanel className="px-5 pb-5">
            <ul className="grid gap-1.5 text-[12px] sm:grid-cols-2">
              {Object.entries(report.budgets_left).map(([name, left]) => (
                <li key={name} className="flex justify-between gap-3">
                  <span className="truncate text-muted-foreground">{name}</span>
                  <span className="font-mono tabular-nums">{left}</span>
                </li>
              ))}
            </ul>
          </CardPanel>
        </Card>
      ) : null}
      <pre className="max-w-3xl whitespace-pre-wrap rounded-xl border bg-card p-4 font-mono text-[11px] leading-5 text-muted-foreground">
        {data.markdown || "No report.md"}
      </pre>
    </div>
  );
}

function ScoreCard({
  label,
  value,
  tone,
}: {
  label: string;
  value?: number;
  tone: "ok" | "warn" | "bad";
}): React.ReactElement {
  return (
    <Card>
      <CardHeader className="px-4 py-3">
        <CardDescription>{label}</CardDescription>
        <CardTitle
          className={cn(
            "text-xl tabular-nums",
            tone === "ok" && "text-success-foreground",
            tone === "warn" && "text-warning-foreground",
            tone === "bad" && "text-destructive-foreground",
          )}
        >
          {value ?? 0}%
        </CardTitle>
      </CardHeader>
    </Card>
  );
}

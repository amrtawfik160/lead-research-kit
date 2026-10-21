"use client";

import { useMemo, useState } from "react";
import { CountBar } from "@/components/count-bar";
import { DetailSheet } from "@/components/detail-sheet";
import { IssueList, type IssueItem } from "@/components/issue-list";
import { PageBody, PageHeader } from "@/components/page-header";
import { Panel, PanelHeader } from "@/components/panel";
import { QueryGate, useQuery } from "@/components/query-state";
import { StatusBadge } from "@/components/status-badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Progress, ProgressIndicator, ProgressTrack } from "@/components/ui/progress";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { display } from "@/lib/format";
import { issueKey } from "@/lib/gr8r";
import { leadFieldSections } from "@/lib/lead-fields";
import type { DailyPayload, LeadRow } from "@/lib/types";
import { cn } from "@/lib/utils";

function toIssue(row: LeadRow, index: number): IssueItem<LeadRow> {
  return {
    id: issueKey("DAY", index),
    title: display(row["Full Name"]),
    project: display(row.Company),
    status: display(row.Source),
    priority: display(row.Confidence || row.Tier),
    due: display(row.State),
    avatar: display(row.Company),
    group: display(row.Source),
    raw: row,
  };
}

export function DailyPage({ initial = null }: { initial?: DailyPayload | null }): React.ReactElement {
  const [date, setDate] = useState<string | undefined>(undefined);
  const { data, error, loading } = useQuery(() => api.daily(date), [date], initial);
  const [lead, setLead] = useState<LeadRow | null>(null);
  const issues = useMemo(() => (data?.leads || []).map(toIssue), [data]);

  return (
    <>
      <PageHeader
        title="Inbox"
        description="Dated delivery folders: leads.csv, details, report, and optional spot-check."
      />
      <QueryGate loading={loading} error={error} empty={!data}>
        {data ? (
          <PageBody className="flex min-h-0 flex-1 flex-col">
            <div className="flex flex-wrap gap-1.5 border-b px-6 pb-3">
              {data.batches.map((batch) => {
                const selected = batch.date === data.date;
                return (
                  <button
                    key={batch.date}
                    type="button"
                    onClick={() => setDate(batch.date)}
                    className={cn(
                      "inline-flex h-[26px] items-center gap-1.5 rounded-[6px] border px-2 text-[12px] transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                      selected
                        ? "border-transparent bg-primary text-primary-foreground"
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
            <Tabs defaultValue={data.leads.length ? "leads" : "report"} className="min-h-0 flex-1">
              <TabsList variant="underline" size="sm" className="px-6">
                <TabsTab value="leads">Leads {data.leads.length}</TabsTab>
                <TabsTab value="report">Report</TabsTab>
                <TabsTab value="audit">Audit</TabsTab>
              </TabsList>
              <TabsPanel value="leads" className="min-h-0 flex-1">
                <IssueList
                  items={issues}
                  onSelect={(item) => setLead(item.raw)}
                  emptyTitle="No leads this day"
                  emptyDescription="Run daily --execute, or pick another dated folder."
                />
              </TabsPanel>
              <TabsPanel value="report" className="px-6 pt-3">
                <ReportView data={data} />
              </TabsPanel>
              <TabsPanel value="audit" className="px-6 pt-3">
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
                  <p className="text-[12.5px] text-muted-foreground">No audit.json. Run `daily audit --sample 20`.</p>
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
    <div className="grid max-w-4xl gap-3 pb-10">
      <Panel>
        <PanelHeader title={`Leads ${data.date}: ${delivered} of ${target || "—"} delivered`} />
        <div className="px-3.5 pb-4">
          <p className="mb-3 text-[12.5px] text-muted-foreground">
            {report.this_run != null
              ? `This run added ${report.this_run} (already on file today: ${report.prior_delivered ?? 0}).`
              : report.execute
                ? "Executed delivery"
                : "Preview only"}
          </p>
          <Progress value={pct}>
            <ProgressTrack className="h-1.5">
              <ProgressIndicator className="bg-primary" />
            </ProgressTrack>
          </Progress>
        </div>
      </Panel>
      <div className="grid gap-3 md:grid-cols-2">
        <Panel>
          <PanelHeader title="Email sources" />
          <div className="px-3.5 pb-4">
            <CountBar counts={report.delivered_by_source} />
          </div>
        </Panel>
        <Panel>
          <PanelHeader title="Email risk" />
          <div className="px-3.5 pb-4">
            <CountBar counts={report.delivered_by_risk} />
          </div>
        </Panel>
      </div>
      {report.budgets_left ? (
        <Panel>
          <PanelHeader title="Budgets left" />
          <ul className="grid gap-1.5 px-3.5 pb-4 text-[13px] sm:grid-cols-2">
            {Object.entries(report.budgets_left).map(([name, left]) => (
              <li key={name} className="flex justify-between gap-3">
                <span className="truncate text-muted-foreground">{name}</span>
                <span className="font-mono tabular-nums">{left}</span>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}
      <pre className="max-w-3xl whitespace-pre-wrap rounded-[10px] border bg-card p-4 font-mono text-[11px] leading-5 text-muted-foreground">
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
    <Panel>
      <div className="px-4 py-3">
        <p className="text-[12.5px] text-muted-foreground">{label}</p>
        <p
          className={cn(
            "text-xl font-semibold tabular-nums",
            tone === "ok" && "text-success-foreground",
            tone === "warn" && "text-warning-foreground",
            tone === "bad" && "text-destructive-foreground",
          )}
        >
          {value ?? 0}%
        </p>
      </div>
    </Panel>
  );
}

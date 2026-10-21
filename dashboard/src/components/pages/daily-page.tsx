"use client";

import { useMemo, useState } from "react";
import { ArrowLeft, CheckCheck, PanelRightOpen, Users } from "lucide-react";
import { CountBar } from "@/components/count-bar";
import { DailyRunControl } from "@/components/daily-run-control";
import { DetailSheet } from "@/components/detail-sheet";
import { DuplicateBanner } from "@/components/duplicate-banner";
import { EmptyState } from "@/components/empty-state";
import { EntityAvatar } from "@/components/entity-avatar";
import { LabelChip } from "@/components/label-chip";
import { ExportLeadsButton, LeadOpsPanel } from "@/components/lead-ops-panel";
import { PageBody } from "@/components/page-header";
import { Panel, PanelHeader } from "@/components/panel";
import { ProgBar } from "@/components/prog-bar";
import { QueryGate, useQuery } from "@/components/query-state";
import { QuietStatus } from "@/components/status-glyph";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTab } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { display } from "@/lib/format";
import { issueKey } from "@/lib/gr8r";
import { leadFieldSections } from "@/lib/lead-fields";
import { copyText, leadEmail, leadSource } from "@/lib/lead-ops";
import type { DailyPayload, LeadRow } from "@/lib/types";
import { cn } from "@/lib/utils";
import { toastManager } from "@/components/ui/toast";

type Cat = "all" | "website" | "finder" | "review" | "report";

function needsReview(row: LeadRow): boolean {
  return /high|medium/.test(String(row["Email Risk"] || "").toLowerCase());
}

function sourceOf(row: LeadRow): string {
  return leadSource(row).toLowerCase();
}

export function DailyPage({ initial = null }: { initial?: DailyPayload | null }): React.ReactElement {
  const [date, setDate] = useState<string | undefined>(undefined);
  const { data, error, loading, reload } = useQuery(() => api.daily(date), [date], initial);
  const [lead, setLead] = useState<LeadRow | null>(null);
  const [sheet, setSheet] = useState(false);
  const [cat, setCat] = useState<Cat>("all");
  const [reviewOnly, setReviewOnly] = useState(false);
  const [seen, setSeen] = useState<Set<string>>(new Set());

  const rows = useMemo(() => data?.leads || [], [data]);
  const visible = useMemo(() => {
    return rows.filter((row, index) => {
      const id = issueKey("DAY", index);
      const flagged = needsReview(row) && !seen.has(id);
      if (reviewOnly && !flagged) return false;
      if (cat === "website") return sourceOf(row).includes("website");
      if (cat === "finder") return sourceOf(row).includes("finder");
      if (cat === "review") return needsReview(row);
      if (cat === "report") return false;
      return true;
    });
  }, [cat, reviewOnly, rows, seen]);

  const countReview = (key: Cat) =>
    rows.filter((row, index) => {
      const id = issueKey("DAY", index);
      const flagged = needsReview(row) && !seen.has(id);
      if (!flagged) return false;
      if (key === "website") return sourceOf(row).includes("website");
      if (key === "finder") return sourceOf(row).includes("finder");
      if (key === "review") return needsReview(row);
      return true;
    }).length;

  function selectLead(row: LeadRow, index: number) {
    setLead(row);
    setSheet(false);
    setCat((current) => (current === "report" ? "all" : current));
    setSeen((prev) => new Set(prev).add(issueKey("DAY", index)));
  }

  return (
    <>
      <QueryGate loading={loading} error={error} empty={!data}>
        {data ? (
          <PageBody className="flex min-h-0 flex-1 flex-col">
            <div className="inbox-grid grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
              <div className="flex min-h-0 flex-col border-r">
                <div className="flex items-center gap-2 px-6 pt-5">
                  <h1 className="text-[19px] font-semibold tracking-[-0.015em]">Daily</h1>
                  <span className="flex-1" />
                  <label className="flex items-center gap-1.5 text-[12px] text-[#57544e] dark:text-[#b5b2ab]">
                    <Switch
                      checked={reviewOnly}
                      onCheckedChange={(checked) => setReviewOnly(Boolean(checked))}
                      aria-label="Needs review only"
                    />
                    Review
                  </label>
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    aria-label="Clear review marks"
                    onClick={() => setSeen(new Set(rows.map((_, index) => issueKey("DAY", index))))}
                  >
                    <CheckCheck />
                  </Button>
                </div>
                <div className="px-6 pt-3">
                  <DailyRunControl onDone={reload} />
                </div>
                <div className="flex flex-wrap gap-2 px-6 py-3">
                  {data.batches.map((batch) => {
                    const selected = batch.date === data.date;
                    return (
                      <Button
                        key={batch.date}
                        size="xs"
                        variant={selected ? "default" : "outline"}
                        onClick={() => {
                          setDate(batch.date);
                          setLead(null);
                        }}
                      >
                        <span className="font-medium tabular-nums">{batch.date}</span>
                        <span className="tabular-nums">
                          {batch.delivered}/{batch.target}
                        </span>
                        {batch.has_audit ? (
                          <Badge size="sm" variant={selected ? "secondary" : "success"}>
                            audit
                          </Badge>
                        ) : null}
                      </Button>
                    );
                  })}
                </div>
                <Tabs
                  value={cat}
                  onValueChange={(value) => {
                    const next = value as Cat;
                    setCat(next);
                    if (next === "report") setLead(null);
                  }}
                  className="gap-0 border-b px-4"
                >
                  <TabsList variant="underline" size="sm">
                    {(
                      [
                        ["all", "All"],
                        ["website", "Website"],
                        ["finder", "Finder"],
                        ["review", "Review"],
                        ["report", "Report"],
                      ] as const
                    ).map(([key, label]) => {
                      const flagged = key === "report" ? 0 : countReview(key);
                      return (
                        <TabsTab key={key} value={key}>
                          {label}
                          {flagged ? <span className="text-[11px] text-muted-foreground">{flagged}</span> : null}
                        </TabsTab>
                      );
                    })}
                  </TabsList>
                </Tabs>
                <DuplicateBanner items={data.duplicates} className="mx-6 mt-3" />
                <div className="min-h-0 flex-1 overflow-auto">
                  {cat === "report" ? (
                    <p className="px-6 py-3 text-[12.5px] text-muted-foreground">
                      Dated delivery folders: leads.csv, details, report, and optional spot-check.
                    </p>
                  ) : visible.length === 0 ? (
                    <EmptyState
                      compact
                      icon={Users}
                      title="No leads in this view"
                      description={
                        reviewOnly
                          ? "No leads still need review in this category."
                          : "New website, finder, and review leads will show up here."
                      }
                    />
                  ) : (
                    visible.map((row) => {
                      const index = rows.indexOf(row);
                      const id = issueKey("DAY", index);
                      const flagged = needsReview(row) && !seen.has(id);
                      const selected = lead === row;
                      return (
                        <button
                          key={id}
                          type="button"
                          onClick={() => selectLead(row, index)}
                          className={cn(
                            "relative flex min-h-10 w-full items-start gap-2.5 border-b border-[#f0eeea] px-6 py-3 text-left transition-colors dark:border-[#252523]",
                            selected ? "bg-[#f6f5f2] dark:bg-[#252524]" : "hover:bg-[#f6f5f2] dark:hover:bg-[#252524]",
                          )}
                        >
                          {flagged ? (
                            <span className="absolute top-[22px] left-1.5 size-1.5 rounded-full bg-primary" aria-label="Needs review" />
                          ) : null}
                          <EntityAvatar name={display(row["Full Name"])} size={22} />
                          <div className="min-w-0 flex-1 text-[13px] leading-[1.45]">
                            <div>
                              <span className="issue-title font-semibold">{display(row["Full Name"])}</span>{" "}
                              <span className="text-muted-foreground">{display(row.Title)}</span>
                            </div>
                            <div className="mt-0.5 truncate text-[12.5px] text-muted-foreground">
                              {display(row.Company)} · {display(row.Email)}
                            </div>
                            <div className="mt-1.5 flex items-center gap-1.5 text-[11.5px] text-[#6b6760]">
                              <QuietStatus value={row.Source} />
                              <span>·</span>
                              <QuietStatus value={row.Stage || "new"} />
                              <span>·</span>
                              <span>{display(row.State)}</span>
                            </div>
                          </div>
                        </button>
                      );
                    })
                  )}
                </div>
              </div>
              <div className="flex min-h-0 flex-col overflow-auto">
                {cat === "report" ? (
                  <div className="px-6 pt-6">
                    <ReportView data={data} />
                  </div>
                ) : lead ? (
                  <LeadPreview
                    row={lead}
                    issueId={issueKey("DAY", rows.indexOf(lead))}
                    onBack={() => setLead(null)}
                    onOpen={() => setSheet(true)}
                  />
                ) : (
                  <div className="grid min-h-full place-items-center p-10">
                    <EmptyState
                      icon={Users}
                      title="Select a lead"
                      description="Read the delivered lead and open details without leaving Daily."
                    />
                  </div>
                )}
              </div>
            </div>
            <DetailSheet
              open={sheet}
              onOpenChange={setSheet}
              title={display(lead?.["Full Name"])}
              description={display(lead?.Company)}
              issueId={lead ? issueKey("DAY", rows.indexOf(lead)) : undefined}
              sections={leadFieldSections(lead)}
              extraFirst
              extra={
                <LeadOpsPanel
                  row={lead}
                  onSaved={(next) => {
                    setLead(next);
                    reload();
                  }}
                />
              }
            />
          </PageBody>
        ) : null}
      </QueryGate>
    </>
  );
}

function LeadPreview({
  row,
  issueId,
  onBack,
  onOpen,
}: {
  row: LeadRow;
  issueId: string;
  onBack: () => void;
  onOpen: () => void;
}): React.ReactElement {
  const email = leadEmail(row);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex min-h-12 shrink-0 flex-wrap items-center gap-2 border-b px-4 py-2">
        <Button size="icon-sm" variant="ghost" aria-label="Back" onClick={onBack}>
          <ArrowLeft />
        </Button>
        <span className="size-1.5 rounded-[3px] bg-[#3570c4]" />
        <span className="truncate text-[12.5px] text-muted-foreground">{display(row.Company)}</span>
        <span className="font-mono text-[11.5px] text-muted-foreground">{issueId}</span>
        <span className="flex-1" />
        <ExportLeadsButton rows={[row]} filename="veinore-lead.csv" />
        <Button
          size="sm"
          variant="outline"
          disabled={!email}
          onClick={() => {
            void copyText(email).then((ok) => {
              toastManager.add({
                type: ok ? "success" : "error",
                title: ok ? "Email copied" : "Could not copy email",
                description: email,
              });
            });
          }}
        >
          Copy email
        </Button>
        <Button size="sm" variant="outline" onClick={onOpen}>
          <PanelRightOpen className="size-3.5" />
          Open details
        </Button>
      </div>
      <div className="w-full max-w-[720px] px-6 py-6">
        <h2 className="mb-2.5 text-[19px] font-semibold tracking-[-0.015em]">{display(row["Full Name"])}</h2>
        <div className="mb-4 flex flex-wrap items-center gap-1">
          <QuietStatus value={row.Source} />
          <QuietStatus value={row.Stage || "new"} />
          <LabelChip value={row.Company} />
          <QuietStatus value={row["Email Risk"]} />
          <span className="text-[12.5px] text-muted-foreground">{display(row.City)}</span>
        </div>
        <p className="mb-1 text-[11px] font-semibold tracking-[0.07em] text-muted-foreground uppercase">About this lead</p>
        <p className="text-[13.5px] leading-6 text-[#57544e] dark:text-[#b5b2ab]">
          {display(row.Title)} at {display(row.Company)}. Email {display(row.Email)} from {display(row.Source)}
          {row["Email Risk Reason"] ? ` — ${display(row["Email Risk Reason"])}` : "."}
        </p>
      </div>
    </div>
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
        <div className="px-4 pb-4">
          <p className="mb-3 text-[12.5px] text-muted-foreground">
            {report.this_run != null
              ? `This run added ${report.this_run} (already on file today: ${report.prior_delivered ?? 0}).`
              : report.execute
                ? "Executed delivery"
                : "Preview only"}
          </p>
          <span className="flex items-center gap-2">
            <ProgBar value={pct} />
            <span className="text-[11.5px] tabular-nums text-muted-foreground">{pct}%</span>
          </span>
        </div>
      </Panel>
      <div className="grid gap-3 md:grid-cols-2">
        <Panel>
          <PanelHeader title="Email sources" />
          <div className="px-4 pb-4">
            <CountBar counts={report.delivered_by_source} />
          </div>
        </Panel>
        <Panel>
          <PanelHeader title="Email risk" />
          <div className="px-4 pb-4">
            <CountBar counts={report.delivered_by_risk} />
          </div>
        </Panel>
      </div>
      {report.budgets_left ? (
        <Panel>
          <PanelHeader title="Budgets left" />
          <ul className="grid gap-1.5 px-4 pb-4 text-[13px] sm:grid-cols-2">
            {Object.entries(report.budgets_left).map(([name, left]) => (
              <li key={name} className="flex justify-between gap-3">
                <span className="truncate text-muted-foreground">{name}</span>
                <span className="font-mono tabular-nums">{left}</span>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}
      {data.audit ? (
        <div className="grid gap-3 sm:grid-cols-3">
          <ScoreCard label="Confirmed" value={data.audit.score?.confirmed_pct} tone="ok" />
          <ScoreCard label="Partial" value={data.audit.score?.partial_pct} tone="warn" />
          <ScoreCard label="Failed" value={data.audit.score?.failed_pct} tone="bad" />
        </div>
      ) : (
        <p className="text-[12.5px] text-muted-foreground">No audit.json. Run `daily audit --sample 20`.</p>
      )}
      {data.audit ? (
        <Alert variant="info">
          <AlertTitle>Spot-check</AlertTitle>
          <AlertDescription>
            {data.audit.sampled} sampled of {data.leads.length} delivered leads.
          </AlertDescription>
        </Alert>
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

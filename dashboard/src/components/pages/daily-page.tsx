"use client";

import { useMemo, useState } from "react";
import { ArrowLeft, CheckCheck, FileText, PanelRightOpen, Users } from "lucide-react";
import { CountBar } from "@/components/count-bar";
import { DailyRunControl } from "@/components/daily-run-control";
import { DetailSheet } from "@/components/detail-sheet";
import { DuplicateBanner } from "@/components/duplicate-banner";
import { EmptyState } from "@/components/empty-state";
import { EntityAvatar } from "@/components/entity-avatar";
import { LabelChip } from "@/components/label-chip";
import { ExportLeadsButton, LeadOpsPanel } from "@/components/lead-ops-panel";
import { PageHeader, PageShell, PageSplit, PageToolbar } from "@/components/page-header";
import { ProgBar } from "@/components/prog-bar";
import { QueryGate, useQuery } from "@/components/query-state";
import { QuietStatus } from "@/components/status-glyph";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsiblePanel, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTab } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { display, humanName, humanPool } from "@/lib/format";
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

  const countTab = (key: Cat) => {
    if (key === "report") return 0;
    if (key === "review") return countReview("review");
    return rows.filter((row, index) => {
      const id = issueKey("DAY", index);
      const flagged = needsReview(row) && !seen.has(id);
      if (reviewOnly && !flagged) return false;
      if (key === "website") return sourceOf(row).includes("website");
      if (key === "finder") return sourceOf(row).includes("finder");
      return true;
    }).length;
  };

  function selectLead(row: LeadRow, index: number) {
    setLead(row);
    setSheet(false);
    setCat((current) => (current === "report" ? "all" : current));
    setSeen((prev) => new Set(prev).add(issueKey("DAY", index)));
  }

  return (
    <PageShell>
      <PageHeader
        title="Daily"
        description="Today's delivery, review queue, and the run report."
        actions={
          <>
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <Switch
                checked={reviewOnly}
                onCheckedChange={(checked) => setReviewOnly(Boolean(checked))}
                aria-label="Show leads that still need review"
              />
              Needs review
            </label>
            <ExportLeadsButton rows={visible.length ? visible : rows} filename={`veinore-daily-${data?.date || "batch"}.csv`} />
            <Button
              size="sm"
              variant="outline"
              onClick={() => setSeen(new Set(rows.map((_, index) => issueKey("DAY", index))))}
            >
              <CheckCheck />
              Mark reviewed
            </Button>
          </>
        }
      />
      <QueryGate loading={loading} error={error} empty={!data}>
        {data ? (
          <>
            <PageSplit
              left={
                <>
                  <div className="pt-2">
                    <DailyRunControl onDone={reload} />
                  </div>
                  <div className="flex flex-wrap gap-2 py-4">
                    {data.batches.map((batch) => {
                      const selected = batch.date === data.date;
                      return (
                        <button
                          key={batch.date}
                          type="button"
                          onClick={() => {
                            setDate(batch.date);
                            setLead(null);
                          }}
                          className={cn(
                            "flex min-h-14 min-w-40 flex-col items-start justify-center gap-1 rounded-xl border px-3.5 py-2 text-left text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
                            selected ? "border-foreground bg-muted" : "bg-background hover:bg-muted",
                          )}
                        >
                          <span className="font-medium tabular-nums">{batch.date}</span>
                          <span className="flex items-center gap-2">
                            <span className="tabular-nums text-muted-foreground">
                              {batch.delivered}/{batch.target} delivered
                            </span>
                            {batch.has_audit ? <Badge variant="success">Audited</Badge> : null}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                  <PageToolbar>
                    <Tabs
                      value={cat}
                      onValueChange={(value) => {
                        const next = value as Cat;
                        setCat(next);
                        if (next === "report") setLead(null);
                      }}
                      className="min-w-0 flex-1 gap-0"
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
                          const count = countTab(key);
                          return (
                            <TabsTab key={key} value={key}>
                              {label}
                              {count ? (
                                <Badge render={<span />} variant={key === "review" ? "warning" : "secondary"}>
                                  {count}
                                </Badge>
                              ) : null}
                            </TabsTab>
                          );
                        })}
                      </TabsList>
                    </Tabs>
                  </PageToolbar>
                  <DuplicateBanner items={data.duplicates} className="mt-3" />
                  <div className="min-h-0 flex-1 overflow-auto">
                    {cat === "report" ? (
                      <ReportOutline data={data} />
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
                              "relative flex min-h-12 w-full items-start gap-3 border-b px-2.5 py-3.5 text-left transition-colors",
                              selected ? "bg-muted" : "hover:bg-muted/70",
                            )}
                          >
                            {flagged ? (
                              <span className="mt-2 size-1.5 shrink-0 rounded-full bg-primary" aria-label="Needs review" />
                            ) : (
                              <span className="mt-2 size-1.5 shrink-0" />
                            )}
                            <EntityAvatar name={display(row["Full Name"])} size={22} />
                            <div className="min-w-0 flex-1 text-sm leading-6">
                              <div>
                                <span className="issue-title font-semibold">{display(row["Full Name"])}</span>{" "}
                                <span className="text-muted-foreground">{display(row.Title)}</span>
                              </div>
                              <div className="mt-0.5 truncate text-sm text-muted-foreground">
                                {display(row.Company)} · {display(row.Email)}
                              </div>
                              <div className="mt-2 flex items-center gap-2 text-sm text-muted-foreground">
                                <QuietStatus value={humanName(String(row.Source || ""))} />
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
                </>
              }
              right={
                cat === "report" ? (
                  <div className="min-h-0 flex-1 overflow-auto py-6">
                    <ReportView data={data} />
                  </div>
                ) : lead ? (
                  <LeadPreview
                    row={lead}
                    issueId={issueKey("DAY", rows.indexOf(lead))}
                    onBack={() => setLead(null)}
                    onOpen={() => setSheet(true)}
                    onSaved={(next) => {
                      setLead(next);
                      reload();
                    }}
                  />
                ) : (
                  <div className="grid min-h-full place-items-center py-10">
                    <EmptyState
                      icon={Users}
                      title="Select a lead"
                      description="Read the delivered lead and open details without leaving Daily."
                    />
                  </div>
                )
              }
            />
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
          </>
        ) : null}
      </QueryGate>
    </PageShell>
  );
}

function LeadPreview({
  row,
  issueId,
  onBack,
  onOpen,
  onSaved,
}: {
  row: LeadRow;
  issueId: string;
  onBack: () => void;
  onOpen: () => void;
  onSaved: (next: LeadRow) => void;
}): React.ReactElement {
  const email = leadEmail(row);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex min-h-12 shrink-0 flex-wrap items-center gap-2 border-b py-2">
        <Button size="icon-sm" variant="ghost" aria-label="Back" onClick={onBack}>
          <ArrowLeft />
        </Button>
        <span className="size-1.5 rounded-sm bg-info" />
        <span className="truncate text-sm text-muted-foreground">{display(row.Company)}</span>
        <span className="font-mono text-xs text-muted-foreground">{issueId}</span>
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
      <div className="w-full max-w-3xl space-y-6 py-6">
        <div>
          <h2 className="mb-3 font-heading text-2xl font-semibold tracking-tight">{display(row["Full Name"])}</h2>
          <div className="mb-5 flex flex-wrap items-center gap-2">
            <QuietStatus value={humanName(String(row.Source || ""))} />
            <QuietStatus value={row.Stage || "new"} />
            <LabelChip value={row.Company} />
            <QuietStatus value={row["Email Risk"]} />
            <span className="text-sm text-muted-foreground">{display(row.City)}</span>
          </div>
          <p className="mb-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">About this lead</p>
          <p className="text-base leading-7 text-muted-foreground">
            {display(row.Title)} at {display(row.Company)}. Email {display(row.Email)} from {humanName(display(row.Source))}
            {row["Email Risk Reason"] ? ` — ${humanName(display(row["Email Risk Reason"]))}` : "."}
          </p>
        </div>
        <div className="rounded-xl border bg-card p-4">
          <LeadOpsPanel row={row} onSaved={onSaved} />
        </div>
      </div>
    </div>
  );
}

function reportSummary(data: DailyPayload): { delivered: number; target: number; pct: number; added: string } {
  const report = data.report || {};
  const delivered = Number(report.delivered ?? data.leads.length);
  const target = Number(report.target ?? 0);
  return {
    delivered,
    target,
    pct: target ? Math.min(100, Math.round((delivered / target) * 100)) : 0,
    added:
      report.this_run != null
        ? `This run added ${report.this_run}. ${report.prior_delivered ?? 0} were already on file.`
        : report.execute
          ? "Executed delivery"
          : "Preview only",
  };
}

function OutlineRows({
  title,
  items,
}: {
  title: string;
  items: Array<[string, string | number]>;
}): React.ReactElement | null {
  if (!items.length) return null;
  return (
    <section className="grid gap-2">
      <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">{title}</h3>
      <ul className="grid gap-2 text-sm">
        {items.map(([name, value]) => (
          <li key={name} className="flex justify-between gap-3">
            <span className="min-w-0 truncate text-muted-foreground">
              {name.includes(".") ? humanPool(name) : humanName(name)}
            </span>
            <span className="tabular-nums">{value}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ReportOutline({ data }: { data: DailyPayload }): React.ReactElement {
  const { delivered, target, pct, added } = reportSummary(data);
  const sources = Object.entries(data.report?.delivered_by_source || {}).filter(([, value]) => Number(value) > 0);
  const risks = Object.entries(data.report?.delivered_by_risk || {}).filter(([, value]) => Number(value) > 0);
  const budgets = Object.entries(data.report?.budgets_left || {});
  const score = data.audit?.score;
  return (
    <div className="grid gap-6 py-5">
      <div>
        <p className="font-heading text-lg font-semibold">{data.date}</p>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          {delivered} of {target || "—"} delivered. {added}
        </p>
      </div>
      <div className="flex items-center gap-3">
        <ProgBar value={pct} />
        <span className="text-sm tabular-nums text-muted-foreground">{pct}%</span>
      </div>
      <OutlineRows title="Sources" items={sources} />
      <OutlineRows title="Email risk" items={risks} />
      {score ? (
        <OutlineRows
          title="Spot-check"
          items={[
            ["Confirmed", `${score.confirmed_pct}%`],
            ["Partial", `${score.partial_pct}%`],
            ["Failed", `${score.failed_pct}%`],
            ["Sampled", `${data.audit?.sampled ?? 0} of ${data.leads.length}`],
          ]}
        />
      ) : (
        <p className="text-sm text-muted-foreground">No audit file for this day.</p>
      )}
      <OutlineRows title="Budgets left" items={budgets} />
    </div>
  );
}

function ReportView({ data }: { data: DailyPayload }): React.ReactElement {
  const report = data.report || {};
  const { delivered, target, pct, added } = reportSummary(data);
  const budgets = Object.entries(report.budgets_left || {});
  const maxLeft = Math.max(
    1,
    ...budgets.map(([, left]) => {
      const parsed = Number(left);
      return Number.isFinite(parsed) ? parsed : 0;
    }),
  );
  return (
    <div className="grid w-full gap-4 pb-16">
      <Card>
        <CardHeader>
          <CardTitle>
            Leads {data.date}: {delivered} of {target || "—"} delivered
          </CardTitle>
          <CardDescription>{added}</CardDescription>
        </CardHeader>
        <CardPanel className="grid gap-4">
          <div className="flex items-center gap-3">
            <ProgBar value={pct} />
            <span className="text-sm tabular-nums text-muted-foreground">{pct}%</span>
          </div>
          {report.advice?.length ? (
            <ul className="grid gap-2 text-sm text-muted-foreground">
              {report.advice.map((tip) => (
                <li key={tip}>{tip}</li>
              ))}
            </ul>
          ) : null}
        </CardPanel>
      </Card>
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Email sources</CardTitle>
            <CardDescription>Where delivered addresses came from.</CardDescription>
          </CardHeader>
          <CardPanel>
            <CountBar counts={report.delivered_by_source} />
          </CardPanel>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Email risk</CardTitle>
            <CardDescription>Verifier risk mix for this delivery.</CardDescription>
          </CardHeader>
          <CardPanel>
            <CountBar counts={report.delivered_by_risk} />
          </CardPanel>
        </Card>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        {data.audit ? (
          <>
            <ScoreCard label="Confirmed" value={data.audit.score?.confirmed_pct} tone="ok" />
            <ScoreCard label="Partial" value={data.audit.score?.partial_pct} tone="warn" />
            <ScoreCard label="Failed" value={data.audit.score?.failed_pct} tone="bad" />
          </>
        ) : (
          <Card className="sm:col-span-3">
            <CardHeader>
              <CardTitle>No audit yet</CardTitle>
              <CardDescription>Run `daily audit --sample 20` on the CLI to score this delivery.</CardDescription>
            </CardHeader>
          </Card>
        )}
      </div>
      {budgets.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Budgets left</CardTitle>
            <CardDescription>Remaining attested units after this run.</CardDescription>
          </CardHeader>
          <CardPanel>
            <ul className="grid gap-4 sm:grid-cols-2">
              {budgets.map(([name, left]) => {
                const parsed = Number(left);
                const value = Number.isFinite(parsed) ? parsed : 0;
                return (
                  <li key={name} className="grid gap-2">
                    <div className="flex items-center justify-between gap-3 text-sm">
                      <span className="min-w-0 truncate">{humanPool(name)}</span>
                      <span className="tabular-nums text-muted-foreground">{left}</span>
                    </div>
                    <ProgBar value={Math.round((value / maxLeft) * 100)} />
                  </li>
                );
              })}
            </ul>
          </CardPanel>
        </Card>
      ) : null}
      {report.delivered_by_company_type &&
      Object.values(report.delivered_by_company_type).some((value) => Number(value) > 0) ? (
        <Card>
          <CardHeader>
            <CardTitle>Company type</CardTitle>
            <CardDescription>How delivered companies were classified.</CardDescription>
          </CardHeader>
          <CardPanel>
            <CountBar counts={report.delivered_by_company_type} />
          </CardPanel>
        </Card>
      ) : null}
      {data.audit ? (
        <Card>
          <CardHeader>
            <CardTitle>Spot-check</CardTitle>
            <CardDescription>
              {data.audit.sampled} sampled of {data.leads.length} delivered leads.
            </CardDescription>
          </CardHeader>
          <CardPanel>
            {data.audit.top_reasons && Object.keys(data.audit.top_reasons).length ? (
              <CountBar counts={data.audit.top_reasons} />
            ) : (
              <p className="text-sm text-muted-foreground">No sample reasons were recorded for this audit.</p>
            )}
          </CardPanel>
        </Card>
      ) : null}
      <Card>
        <Collapsible>
          <CardHeader>
            <CardTitle>Raw report</CardTitle>
            <CardDescription>The markdown written beside this delivery folder.</CardDescription>
          </CardHeader>
          <CardPanel>
            <CollapsibleTrigger render={<Button size="sm" variant="outline" />}>
              <FileText />
              View raw report
            </CollapsibleTrigger>
            <CollapsiblePanel>
              <pre className="mt-4 whitespace-pre-wrap font-mono text-sm leading-6 text-muted-foreground">
                {data.markdown || "No report.md"}
              </pre>
            </CollapsiblePanel>
          </CardPanel>
        </Collapsible>
      </Card>
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
    <Card className="h-full">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle
          className={cn(
            "text-3xl tabular-nums",
            tone === "ok" && "text-success-foreground",
            tone === "warn" && "text-warning-foreground",
            tone === "bad" && "text-destructive-foreground",
          )}
        >
          {value ?? 0}%
        </CardTitle>
      </CardHeader>
      <CardPanel>
        <ProgBar
          value={value ?? 0}
          tone={tone === "ok" ? "ok" : tone === "bad" ? "bad" : undefined}
        />
      </CardPanel>
    </Card>
  );
}

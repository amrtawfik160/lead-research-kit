"use client";

import { useMemo, useState } from "react";
import { ArrowLeft, CheckCheck, Mail, MailOpen, PanelRightOpen } from "lucide-react";
import { CountBar } from "@/components/count-bar";
import { DetailSheet } from "@/components/detail-sheet";
import { EmptyState } from "@/components/empty-state";
import { EntityAvatar } from "@/components/entity-avatar";
import { LabelChip } from "@/components/label-chip";
import { PageBody } from "@/components/page-header";
import { Panel, PanelHeader } from "@/components/panel";
import { ProgBar } from "@/components/prog-bar";
import { QueryGate, useQuery } from "@/components/query-state";
import { QuietStatus } from "@/components/status-glyph";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { api } from "@/lib/api";
import { display } from "@/lib/format";
import { issueKey } from "@/lib/gr8r";
import { leadFieldSections } from "@/lib/lead-fields";
import type { DailyPayload, LeadRow } from "@/lib/types";
import { cn } from "@/lib/utils";

type Cat = "all" | "website" | "finder" | "review" | "report";

function needsReview(row: LeadRow): boolean {
  return /high|medium/.test(String(row["Email Risk"] || "").toLowerCase());
}

function sourceOf(row: LeadRow): string {
  return String(row.Source || row["Email Source"] || "").toLowerCase();
}

export function DailyPage({ initial = null }: { initial?: DailyPayload | null }): React.ReactElement {
  const [date, setDate] = useState<string | undefined>(undefined);
  const { data, error, loading } = useQuery(() => api.daily(date), [date], initial);
  const [lead, setLead] = useState<LeadRow | null>(null);
  const [sheet, setSheet] = useState(false);
  const [cat, setCat] = useState<Cat>("all");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [seen, setSeen] = useState<Set<string>>(new Set());

  const rows = useMemo(() => data?.leads || [], [data]);
  const visible = useMemo(() => {
    return rows.filter((row, index) => {
      const id = issueKey("DAY", index);
      const unread = needsReview(row) && !seen.has(id);
      if (unreadOnly && !unread) return false;
      if (cat === "website") return sourceOf(row).includes("website");
      if (cat === "finder") return sourceOf(row).includes("finder");
      if (cat === "review") return needsReview(row);
      if (cat === "report") return false;
      return true;
    });
  }, [cat, rows, seen, unreadOnly]);

  const countUnread = (key: Cat) =>
    rows.filter((row, index) => {
      const id = issueKey("DAY", index);
      const unread = needsReview(row) && !seen.has(id);
      if (!unread) return false;
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
                <div className="flex items-center gap-2 px-6 pt-[18px]">
                  <h1 className="text-[19px] font-semibold tracking-[-0.015em]">Inbox</h1>
                  <span className="flex-1" />
                  <label className="flex items-center gap-1.5 text-[12px] text-[#57544e] dark:text-[#b5b2ab]">
                    <Switch
                      checked={unreadOnly}
                      onCheckedChange={(checked) => setUnreadOnly(Boolean(checked))}
                      aria-label="Unread only"
                    />
                    Unread
                  </label>
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    aria-label="Mark all as read"
                    onClick={() => setSeen(new Set(rows.map((_, index) => issueKey("DAY", index))))}
                  >
                    <CheckCheck />
                  </Button>
                </div>
                <div className="flex flex-wrap gap-1.5 px-6 pt-3 pb-1">
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
                          "inline-flex h-[26px] items-center gap-1.5 rounded-[6px] border px-2 text-[12px] transition-colors",
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
                <div className="flex flex-wrap gap-1 border-b px-4" role="tablist">
                  {(
                    [
                      ["all", "All"],
                      ["website", "Website"],
                      ["finder", "Finder"],
                      ["review", "Review"],
                      ["report", "Report"],
                    ] as const
                  ).map(([key, label]) => {
                    const unread = key === "report" ? 0 : countUnread(key);
                    const on = cat === key;
                    return (
                      <button
                        key={key}
                        type="button"
                        role="tab"
                        aria-selected={on}
                        onClick={() => {
                          setCat(key);
                          if (key === "report") setLead(null);
                        }}
                        className={cn(
                          "inline-flex h-8 items-center gap-1.5 border-b-2 px-2 text-[12.5px]",
                          on ? "border-foreground font-medium" : "border-transparent text-muted-foreground hover:text-foreground",
                        )}
                      >
                        {label}
                        {unread ? <span className="text-[11px] text-muted-foreground">{unread}</span> : null}
                      </button>
                    );
                  })}
                </div>
                <div className="min-h-0 flex-1 overflow-auto">
                  {cat === "report" ? (
                    <p className="px-6 py-3 text-[12.5px] text-muted-foreground">
                      Dated delivery folders: leads.csv, details, report, and optional spot-check.
                    </p>
                  ) : visible.length === 0 ? (
                    <EmptyState
                      compact
                      icon={Mail}
                      title="You're all caught up."
                      description={
                        unreadOnly
                          ? "No unread deliveries in this category."
                          : "New website, finder, and review leads will show up here."
                      }
                    />
                  ) : (
                    visible.map((row) => {
                      const index = rows.indexOf(row);
                      const id = issueKey("DAY", index);
                      const unread = needsReview(row) && !seen.has(id);
                      const selected = lead === row;
                      return (
                        <button
                          key={id}
                          type="button"
                          onClick={() => selectLead(row, index)}
                          className={cn(
                            "relative flex w-full items-start gap-2.5 border-b border-[#f0eeea] px-6 py-3 text-left transition-colors dark:border-[#252523]",
                            selected ? "bg-[#f6f5f2] dark:bg-[#252524]" : "hover:bg-[#f6f5f2] dark:hover:bg-[#252524]",
                          )}
                        >
                          {unread ? (
                            <span className="absolute top-[22px] left-1.5 size-1.5 rounded-full bg-primary" aria-label="Unread" />
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
                              <span>{display(row.State)}</span>
                            </div>
                          </div>
                          <span className="text-muted-foreground" aria-hidden>
                            {unread ? <MailOpen className="size-3.5" /> : <Mail className="size-3.5" />}
                          </span>
                        </button>
                      );
                    })
                  )}
                </div>
              </div>
              <div className="flex min-h-0 flex-col overflow-auto">
                {cat === "report" ? (
                  <div className="px-7 pt-6">
                    <ReportView data={data} />
                  </div>
                ) : lead ? (
                  <InboxPreview
                    row={lead}
                    issueId={issueKey("DAY", rows.indexOf(lead))}
                    onBack={() => setLead(null)}
                    onOpen={() => setSheet(true)}
                  />
                ) : (
                  <div className="grid min-h-full place-items-center p-10">
                    <EmptyState
                      icon={MailOpen}
                      title="Select a delivery"
                      description="Read the lead and open the drawer without leaving your inbox."
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
            />
          </PageBody>
        ) : null}
      </QueryGate>
    </>
  );
}

function InboxPreview({
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
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-[46px] shrink-0 items-center gap-2 border-b px-4">
        <Button size="icon-sm" variant="ghost" aria-label="Back" onClick={onBack}>
          <ArrowLeft />
        </Button>
        <span className="size-1.5 rounded-full bg-[#3570c4]" />
        <span className="truncate text-[12.5px] text-muted-foreground">{display(row.Company)}</span>
        <span className="font-mono text-[11.5px] text-muted-foreground">{issueId}</span>
        <span className="flex-1" />
        <Button size="sm" variant="outline" className="h-[26px] text-[12px]" onClick={onOpen}>
          <PanelRightOpen className="size-3.5" />
          Open details
        </Button>
      </div>
      <div className="w-full max-w-[720px] px-7 py-6">
        <h2 className="mb-2.5 text-[19px] font-semibold tracking-[-0.015em]">{display(row["Full Name"])}</h2>
        <div className="mb-[18px] flex flex-wrap items-center gap-1">
          <QuietStatus value={row.Source} />
          <LabelChip value={row.Company} />
          <QuietStatus value={row["Email Risk"]} />
          <span className="text-[12.5px] text-muted-foreground">{display(row.City)}</span>
        </div>
        <p className="mb-1 text-[11px] font-semibold tracking-[0.07em] text-muted-foreground uppercase">Conversation</p>
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
        <div className="px-3.5 pb-4">
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

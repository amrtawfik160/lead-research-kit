"use client";

import { useMemo, useState } from "react";
import { DetailSheet } from "@/components/detail-sheet";
import { DuplicateBanner } from "@/components/duplicate-banner";
import { ExportLeadsButton, LeadOpsPanel } from "@/components/lead-ops-panel";
import { IssueList, type IssueItem } from "@/components/issue-list";
import { PageBody, PageHeader } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { api } from "@/lib/api";
import { display } from "@/lib/format";
import { issueKey } from "@/lib/gr8r";
import { leadFieldSections } from "@/lib/lead-fields";
import { isVerifiedLead, LEAD_STAGES, leadSource, leadStage } from "@/lib/lead-ops";
import type { DailyPayload, LeadRow } from "@/lib/types";

function toIssue(row: LeadRow, index: number): IssueItem<LeadRow> {
  const risk = String(row["Email Risk"] || "");
  return {
    id: issueKey("LEA", index),
    title: display(row["Full Name"]),
    project: display(row.Company),
    status: display(leadSource(row)),
    priority: display(row.Tier || row.Confidence),
    due: display(row.City),
    avatar: display(row.Company),
    group: display(leadStage(row)),
    done: risk.toLowerCase() === "low" && String(leadSource(row)).toLowerCase().includes("website"),
    raw: row,
  };
}

export function LeadsPage({ initial = null }: { initial?: DailyPayload | null }): React.ReactElement {
  const [date, setDate] = useState<string | undefined>(undefined);
  const { data, error, loading, reload } = useQuery(() => api.leads(date), [date], initial);
  const [lead, setLead] = useState<LeadRow | null>(null);
  const [source, setSource] = useState("all");
  const [verified, setVerified] = useState("all");
  const [stage, setStage] = useState("all");

  const rows = useMemo(() => {
    return (data?.leads || []).filter((row) => {
      if (source !== "all" && leadSource(row).toLowerCase() !== source) return false;
      if (verified === "verified" && !isVerifiedLead(row)) return false;
      if (verified === "unverified" && isVerifiedLead(row)) return false;
      if (stage !== "all" && leadStage(row) !== stage) return false;
      return true;
    });
  }, [data, source, stage, verified]);

  const issues = useMemo(() => rows.map((row) => toIssue(row, (data?.leads || []).indexOf(row))), [data, rows]);
  const selected = lead ? issues.find((item) => item.raw === lead || leadOpsMatch(item.raw, lead)) : undefined;
  const sources = useMemo(() => {
    return [...new Set((data?.leads || []).map((row) => leadSource(row).toLowerCase()).filter(Boolean))].sort();
  }, [data]);

  return (
    <>
      <PageHeader
        title="Leads"
        description={`${rows.length} shown · filter by source, verification, date, or stage`}
        actions={<ExportLeadsButton rows={rows} filename={`veinore-leads-${data?.date || "export"}.csv`} />}
      />
      <QueryGate
        loading={loading}
        error={error}
        empty={!data}
        emptyTitle="No delivered leads"
        emptyDescription="Run a daily execute, or open the sample workspace in Settings."
      >
        {data ? (
          <PageBody className="flex min-h-0 flex-1 flex-col">
            <div className="flex flex-wrap items-center gap-1.5 border-b px-6 py-2">
              <label className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
                Date
                <select
                  className="h-[26px] rounded-[6px] border bg-background px-2 text-[12px] text-foreground"
                  value={date || data.date}
                  onChange={(event) => setDate(event.target.value)}
                  aria-label="Filter leads by date"
                >
                  {(data.batches || []).map((batch) => (
                    <option key={batch.date} value={batch.date}>
                      {batch.date} · {batch.delivered}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
                Source
                <select
                  className="h-[26px] rounded-[6px] border bg-background px-2 text-[12px] text-foreground"
                  value={source}
                  onChange={(event) => setSource(event.target.value)}
                  aria-label="Filter leads by source"
                >
                  <option value="all">All sources</option>
                  {sources.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
                Verified
                <select
                  className="h-[26px] rounded-[6px] border bg-background px-2 text-[12px] text-foreground"
                  value={verified}
                  onChange={(event) => setVerified(event.target.value)}
                  aria-label="Filter leads by verification"
                >
                  <option value="all">All verification</option>
                  <option value="verified">Verified</option>
                  <option value="unverified">Unverified</option>
                </select>
              </label>
              <label className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
                Stage
                <select
                  className="h-[26px] rounded-[6px] border bg-background px-2 text-[12px] text-foreground"
                  value={stage}
                  onChange={(event) => setStage(event.target.value)}
                  aria-label="Filter leads by stage"
                >
                  <option value="all">All stages</option>
                  {LEAD_STAGES.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <DuplicateBanner items={data.duplicates} className="mx-6 mt-3 mb-1" />
            <IssueList
              items={issues}
              selectedId={selected?.id}
              onSelect={(item) => setLead(item?.raw ?? null)}
              emptyTitle="No leads match"
              emptyDescription="Clear source, verification, date, or stage filters."
            />
            <DetailSheet
              open={Boolean(lead)}
              onOpenChange={(open) => !open && setLead(null)}
              title={display(lead?.["Full Name"])}
              description={`${display(lead?.Title)} · ${display(lead?.Company)}`}
              issueId={selected?.id}
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

function leadOpsMatch(a: LeadRow, b: LeadRow): boolean {
  return String(a.Email || "") === String(b.Email || "") && String(a["Full Name"] || "") === String(b["Full Name"] || "");
}

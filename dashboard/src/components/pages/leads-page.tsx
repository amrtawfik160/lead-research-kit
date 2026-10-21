"use client";

import { useMemo, useState } from "react";
import { DetailSheet } from "@/components/detail-sheet";
import { FilterSelect } from "@/components/filter-select";
import { DuplicateBanner } from "@/components/duplicate-banner";
import { ExportLeadsButton, LeadOpsPanel } from "@/components/lead-ops-panel";
import { IssueList, type IssueItem } from "@/components/issue-list";
import { PageBody, PageHeader, PageShell, PageToolbar } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { api } from "@/lib/api";
import { display, humanName } from "@/lib/format";
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
    status: humanName(leadSource(row)),
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
    <PageShell>
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
          <PageBody>
            <PageToolbar>
              <FilterSelect
                label="Date"
                ariaLabel="Filter leads by date"
                value={date || data.date}
                onChange={setDate}
                options={(data.batches || []).map((batch) => ({
                  value: batch.date,
                  label: `${batch.date} · ${batch.delivered}`,
                }))}
              />
              <FilterSelect
                label="Source"
                ariaLabel="Filter leads by source"
                value={source}
                onChange={setSource}
                options={[
                  { value: "all", label: "All sources" },
                  ...sources.map((item) => ({ value: item, label: humanName(item) })),
                ]}
              />
              <FilterSelect
                label="Verified"
                ariaLabel="Filter leads by verification"
                value={verified}
                onChange={setVerified}
                options={[
                  { value: "all", label: "All verification" },
                  { value: "verified", label: "Verified" },
                  { value: "unverified", label: "Unverified" },
                ]}
              />
              <FilterSelect
                label="Stage"
                ariaLabel="Filter leads by stage"
                value={stage}
                onChange={setStage}
                options={[{ value: "all", label: "All stages" }, ...LEAD_STAGES]}
              />
            </PageToolbar>
            <DuplicateBanner items={data.duplicates} className="mt-3 mb-1" />
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
    </PageShell>
  );
}

function leadOpsMatch(a: LeadRow, b: LeadRow): boolean {
  return String(a.Email || "") === String(b.Email || "") && String(a["Full Name"] || "") === String(b["Full Name"] || "");
}

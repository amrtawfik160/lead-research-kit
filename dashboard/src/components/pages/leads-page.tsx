"use client";

import { useMemo, useState } from "react";
import { DetailSheet } from "@/components/detail-sheet";
import { IssueList, type IssueItem } from "@/components/issue-list";
import { PageBody, PageHeader } from "@/components/page-header";
import { SegTabs } from "@/components/panel";
import { QueryGate, useQuery } from "@/components/query-state";
import { api } from "@/lib/api";
import { display } from "@/lib/format";
import { issueKey } from "@/lib/gr8r";
import { leadFieldSections } from "@/lib/lead-fields";
import type { DailyPayload, LeadRow } from "@/lib/types";

function toIssue(row: LeadRow, index: number): IssueItem<LeadRow> {
  const risk = String(row["Email Risk"] || "");
  return {
    id: issueKey("LEA", index),
    title: display(row["Full Name"]),
    project: display(row.Company),
    status: display(row.Source),
    priority: display(row.Tier || row.Confidence),
    due: display(row.City),
    avatar: display(row.Company),
    group: display(row.Source),
    done: risk.toLowerCase() === "low" && String(row.Source || "").toLowerCase().includes("website"),
    raw: row,
  };
}

export function LeadsPage({ initial = null }: { initial?: DailyPayload | null }): React.ReactElement {
  const { data, error, loading } = useQuery(() => api.leads(), [], initial);
  const [lead, setLead] = useState<LeadRow | null>(null);
  const [tab, setTab] = useState<"all" | "website" | "finder" | "review">("all");

  const issues = useMemo(() => (data?.leads || []).map(toIssue), [data]);
  const website = issues.filter((item) => String(item.status).toLowerCase().includes("website"));
  const finder = issues.filter((item) => String(item.status).toLowerCase().includes("finder"));
  const review = issues.filter((item) => /high|medium|review/.test(String(item.raw["Email Risk"] || "").toLowerCase()));
  const visible = tab === "website" ? website : tab === "finder" ? finder : tab === "review" ? review : issues;
  const selected = lead ? issues.find((item) => item.raw === lead) : undefined;

  return (
    <>
      <PageHeader
        title="My Tasks"
        description={`${website.length} website · ${review.length} review · ${finder.length} finder`}
        actions={
          <SegTabs
            value={tab}
            onChange={setTab}
            items={[
              { value: "all", label: "All", count: issues.length },
              { value: "website", label: "Website", count: website.length },
              { value: "finder", label: "Finder", count: finder.length },
              { value: "review", label: "Review", count: review.length },
            ]}
          />
        }
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
            <IssueList
              items={visible}
              selectedId={selected?.id}
              onSelect={(item) => setLead(item?.raw ?? null)}
              emptyTitle="No leads match"
              emptyDescription="Clear the filter or change grouping."
            />
            <DetailSheet
              open={Boolean(lead)}
              onOpenChange={(open) => !open && setLead(null)}
              title={display(lead?.["Full Name"])}
              description={`${display(lead?.Title)} · ${display(lead?.Company)}`}
              issueId={selected?.id}
              sections={leadFieldSections(lead)}
            />
          </PageBody>
        ) : null}
      </QueryGate>
    </>
  );
}

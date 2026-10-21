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
  const visible = useMemo(() => {
    if (tab === "website") return issues.filter((item) => String(item.status).toLowerCase().includes("website"));
    if (tab === "finder") return issues.filter((item) => String(item.status).toLowerCase().includes("finder"));
    if (tab === "review") return issues.filter((item) => /high|medium|review/.test(String(item.raw["Email Risk"] || "").toLowerCase()));
    return issues;
  }, [issues, tab]);

  return (
    <>
      <PageHeader
        title="My Tasks"
        description={`${data?.leads.length ?? 0} delivered · latest daily file`}
        actions={
          <SegTabs
            value={tab}
            onChange={setTab}
            items={[
              { value: "all", label: "All", count: issues.length },
              { value: "website", label: "Website" },
              { value: "finder", label: "Finder" },
              { value: "review", label: "Review" },
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
              selectedId={lead ? issues.find((item) => item.raw === lead)?.id : undefined}
              onSelect={(item) => setLead(item.raw)}
              emptyTitle="No leads match"
              emptyDescription="Clear the filter or change grouping."
            />
            <DetailSheet
              open={Boolean(lead)}
              onOpenChange={(open) => !open && setLead(null)}
              title={display(lead?.["Full Name"])}
              description={`${display(lead?.Title)} · ${display(lead?.Company)}`}
              sections={leadFieldSections(lead)}
            />
          </PageBody>
        ) : null}
      </QueryGate>
    </>
  );
}

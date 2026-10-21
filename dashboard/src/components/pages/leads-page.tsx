"use client";

import { useState } from "react";
import { DataTable } from "@/components/data-table";
import { DetailSheet } from "@/components/detail-sheet";
import { PageBody, PageHeader } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import { display } from "@/lib/format";
import { leadFieldSections } from "@/lib/lead-fields";
import type { DailyPayload, LeadRow } from "@/lib/types";

export function LeadsPage({ initial = null }: { initial?: DailyPayload | null }): React.ReactElement {
  const { data, error, loading } = useQuery(() => api.leads(), [], initial);
  const [lead, setLead] = useState<LeadRow | null>(null);

  return (
    <>
      <PageHeader
        title="Leads"
        description="The latest daily file: the 6 clean columns plus city, type, source, confidence and risk."
        crumbs={[{ href: "/", label: "Overview" }, { label: "Leads" }]}
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
            <DataTable
              rows={data.leads}
              rowKey={(row) => `${row["Full Name"]}-${row.Email}-${row["Company Website"]}`}
              onRowClick={setLead}
              emptyTitle="No leads match"
              emptyDescription="Clear the filter or change grouping."
              groupOptions={[
                { value: "Source", label: "Source" },
                { value: "Company Type", label: "Company type" },
                { value: "Email Risk", label: "Email risk" },
                { value: "Tier", label: "Tier" },
              ]}
              columns={[
                { key: "Full Name", header: "Name", width: "11rem" },
                { key: "Title", header: "Title", width: "11rem" },
                { key: "Company", header: "Company", width: "13rem" },
                { key: "Company Website", header: "Website", width: "12rem", mono: true },
                { key: "Email", header: "Email", width: "14rem", mono: true },
                { key: "Company Type", header: "Type", width: "8rem" },
                { key: "Source", header: "Source", width: "7rem", render: (row) => <StatusBadge value={row.Source} /> },
                {
                  key: "Email Risk",
                  header: "Risk",
                  width: "6rem",
                  render: (row) => <StatusBadge value={row["Email Risk"]} />,
                },
                { key: "Tier", header: "Tier", width: "4.5rem", render: (row) => <StatusBadge value={row.Tier} /> },
              ]}
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

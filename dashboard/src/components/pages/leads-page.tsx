"use client";

import { useState } from "react";
import { DataTable } from "@/components/data-table";
import { DetailSheet } from "@/components/detail-sheet";
import { PageHeader } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import { display } from "@/lib/format";
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
      <QueryGate loading={loading} error={error} empty={!data}>
        {data ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <DataTable
              rows={data.leads}
              rowKey={(row) => `${row["Full Name"]}-${row.Email}-${row["Company Website"]}`}
              onRowClick={setLead}
              groupOptions={[
                { value: "Source", label: "Source" },
                { value: "Company Type", label: "Company type" },
                { value: "Email Risk", label: "Email risk" },
                { value: "Tier", label: "Tier" },
              ]}
              columns={[
                { key: "Full Name", header: "Name" },
                { key: "Title", header: "Title" },
                { key: "Company", header: "Company" },
                { key: "Company Website", header: "Website" },
                { key: "Email", header: "Email" },
                { key: "Company Type", header: "Type" },
                { key: "Source", header: "Source", render: (row) => <StatusBadge value={row.Source} /> },
                { key: "Email Risk", header: "Risk", render: (row) => <StatusBadge value={row["Email Risk"]} /> },
                { key: "Tier", header: "Tier", render: (row) => <StatusBadge value={row.Tier} /> },
              ]}
            />
            <DetailSheet
              open={Boolean(lead)}
              onOpenChange={(open) => !open && setLead(null)}
              title={display(lead?.["Full Name"])}
              description={`${display(lead?.Title)} · ${display(lead?.Company)}`}
              fields={Object.entries(lead || {}).map(([label, value]) => ({
                label,
                value: display(value),
              }))}
            />
          </div>
        ) : null}
      </QueryGate>
    </>
  );
}

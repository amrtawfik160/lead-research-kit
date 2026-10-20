"use client";

import { useState } from "react";
import { DataTable } from "@/components/data-table";
import { DetailSheet } from "@/components/detail-sheet";
import { PageHeader } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import { display, formatTime } from "@/lib/format";
import type { JobsPayload } from "@/lib/load";
import type { Attempt } from "@/lib/types";

export function JobsPage({ initial = null }: { initial?: JobsPayload | null }): React.ReactElement {
  const { data, error, loading } = useQuery(() => api.jobs(), [], initial);
  const [row, setRow] = useState<Attempt | null>(null);

  return (
    <>
      <PageHeader
        title="Jobs"
        description="Ledger attempts. Pending rows resume with GET only — the dashboard never resubmits."
        crumbs={[{ href: "/", label: "Overview" }, { label: "Jobs" }]}
      />
      <QueryGate loading={loading} error={error} empty={!data}>
        {data ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <DataTable
              rows={data.attempts}
              rowKey={(item) => item.id}
              onRowClick={setRow}
              groupOptions={[
                { value: "state", label: "State" },
                { value: "provider", label: "Provider" },
                { value: "operation", label: "Operation" },
              ]}
              columns={[
                { key: "id", header: "Attempt", render: (item) => item.id.slice(0, 10) },
                { key: "provider", header: "Provider" },
                { key: "operation", header: "Op" },
                { key: "state", header: "State", render: (item) => <StatusBadge value={item.state} /> },
                { key: "email", header: "Email" },
                { key: "assessment", header: "Result", render: (item) => <StatusBadge value={item.assessment} /> },
                { key: "created_at", header: "Created", render: (item) => formatTime(item.created_at) },
              ]}
            />
            <DetailSheet
              open={Boolean(row)}
              onOpenChange={(open) => !open && setRow(null)}
              title={row ? row.id : "Attempt"}
              description={row ? `${row.provider} · ${row.operation}` : undefined}
              fields={[
                { label: "State", value: display(row?.state) },
                { label: "Email", value: display(row?.email) },
                { label: "Lead", value: display(row?.lead_id) },
                { label: "Assessment", value: display(row?.assessment) },
                { label: "Risk", value: display(row?.risk_flags) },
                { label: "Error", value: display(row?.error) },
                { label: "Created", value: formatTime(row?.created_at) },
                { label: "Finished", value: formatTime(row?.finished_at) },
              ]}
            />
          </div>
        ) : null}
      </QueryGate>
    </>
  );
}

"use client";

import { useState } from "react";
import { DataTable } from "@/components/data-table";
import { DetailSheet } from "@/components/detail-sheet";
import { PageBody, PageHeader } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { QuietStatus } from "@/components/status-glyph";
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
      <QueryGate
        loading={loading}
        error={error}
        empty={!data}
        emptyTitle="No ledger attempts"
        emptyDescription="Dry-run or execute a job file on the CLI."
      >
        {data ? (
          <PageBody className="flex min-h-0 flex-1 flex-col">
            <DataTable
              rows={data.attempts}
              rowKey={(item) => item.id}
              onRowClick={setRow}
              emptyTitle="No attempts match"
              emptyDescription="Clear the filter or change grouping."
              groupOptions={[
                { value: "state", label: "State" },
                { value: "provider", label: "Provider" },
                { value: "operation", label: "Operation" },
              ]}
              columns={[
                {
                  key: "id",
                  header: "Attempt",
                  width: "7rem",
                  mono: true,
                  render: (item) => item.id.slice(0, 10),
                },
                { key: "provider", header: "Provider", width: "9rem" },
                { key: "operation", header: "Op", width: "7rem" },
                {
                  key: "state",
                  header: "State",
                  width: "7rem",
                  render: (item) => <QuietStatus value={item.state} />,
                },
                { key: "email", header: "Email", width: "14rem", mono: true },
                {
                  key: "assessment",
                  header: "Result",
                  width: "8rem",
                  render: (item) => <QuietStatus value={item.assessment} />,
                },
                {
                  key: "created_at",
                  header: "Created",
                  width: "10rem",
                  render: (item) => formatTime(item.created_at),
                },
              ]}
            />
            <DetailSheet
              open={Boolean(row)}
              onOpenChange={(open) => !open && setRow(null)}
              title={row ? row.id : "Attempt"}
              description={row ? `${row.provider} · ${row.operation}` : undefined}
              issueId={row?.id}
              sections={[
                {
                  title: "Result",
                  fields: [
                    { label: "State", value: display(row?.state) },
                    { label: "Email", value: display(row?.email) },
                    { label: "Lead", value: display(row?.lead_id) },
                    { label: "Assessment", value: display(row?.assessment) },
                    { label: "Risk", value: display(row?.risk_flags) },
                    { label: "Error", value: display(row?.error) },
                    { label: "Created", value: formatTime(row?.created_at) },
                    { label: "Finished", value: formatTime(row?.finished_at) },
                  ],
                },
              ]}
            />
          </PageBody>
        ) : null}
      </QueryGate>
    </>
  );
}

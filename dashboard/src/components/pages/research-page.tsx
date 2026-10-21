"use client";

import { useState } from "react";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
import { DataTable } from "@/components/data-table";
import { DetailSheet } from "@/components/detail-sheet";
import { PageHeader } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import { display } from "@/lib/format";
import type { ResearchPayload } from "@/lib/load";
import type { Contact } from "@/lib/types";

export function ResearchPage({ initial = null }: { initial?: ResearchPayload | null }): React.ReactElement {
  const { data, error, loading } = useQuery(() => api.research(), [], initial);
  const [contact, setContact] = useState<Contact | null>(null);
  const [record, setRecord] = useState<Record<string, unknown> | null>(null);

  return (
    <>
      <PageHeader
        title="Research"
        description="The CLI research-queue (max 40), plus recorded evidence and unlabeled candidates."
        crumbs={[{ href: "/", label: "Overview" }, { label: "Research" }]}
      />
      <QueryGate loading={loading} error={error} empty={!data}>
        {data ? (
          <Tabs defaultValue="queue" className="flex min-h-0 flex-1 flex-col px-4 py-3 sm:px-6">
            <TabsList variant="underline" size="sm">
              <TabsTab value="queue">Queue {data.queue.length}</TabsTab>
              <TabsTab value="evidence">Evidence {data.evidence.length}</TabsTab>
              <TabsTab value="candidates">Candidates {data.candidates.length}</TabsTab>
            </TabsList>
            <TabsPanel value="queue" className="min-h-0 flex-1 pt-3">
              <DataTable
                rows={data.queue}
                rowKey={(row) => row.id}
                onRowClick={setContact}
                columns={[
                  { key: "name", header: "Name" },
                  { key: "title", header: "Title" },
                  { key: "company", header: "Company" },
                  { key: "source_channel", header: "Channel", render: (row) => <StatusBadge value={row.source_channel} /> },
                  { key: "source_url", header: "Source" },
                ]}
              />
            </TabsPanel>
            <TabsPanel value="evidence" className="min-h-0 flex-1 pt-3">
              <DataTable
                rows={data.evidence}
                rowKey={(row) => String(row.id)}
                onRowClick={setRecord}
                groupOptions={[{ value: "classification", label: "Classification" }]}
                columns={[
                  { key: "lead_id", header: "Lead" },
                  { key: "email", header: "Email" },
                  { key: "classification", header: "Class", render: (row) => <StatusBadge value={String(row.classification)} /> },
                  { key: "source_url", header: "Source" },
                  { key: "excerpt", header: "Excerpt" },
                ]}
              />
            </TabsPanel>
            <TabsPanel value="candidates" className="min-h-0 flex-1 pt-3">
              <DataTable
                rows={data.candidates}
                rowKey={(row) => String(row.id)}
                onRowClick={setRecord}
                groupOptions={[{ value: "kind", label: "Kind" }]}
                columns={[
                  { key: "lead_id", header: "Lead" },
                  { key: "email", header: "Email" },
                  { key: "kind", header: "Kind", render: (row) => <StatusBadge value={String(row.kind)} /> },
                  { key: "identity_confirmed", header: "Identity", value: (row) => String(row.identity_confirmed) },
                ]}
              />
            </TabsPanel>
          </Tabs>
        ) : null}
      </QueryGate>
      <DetailSheet
        open={Boolean(contact)}
        onOpenChange={(open) => !open && setContact(null)}
        title={display(contact?.name)}
        description="Needs public evidence before a primary email is stored."
        fields={[
          { label: "Title", value: display(contact?.title) },
          { label: "Company", value: display(contact?.company) },
          { label: "Channel", value: display(contact?.source_channel) },
          { label: "Source", value: display(contact?.source_url) },
        ]}
      />
      <DetailSheet
        open={Boolean(record)}
        onOpenChange={(open) => !open && setRecord(null)}
        title={display(record?.email || record?.id)}
        fields={Object.entries(record || {}).map(([label, value]) => ({
          label,
          value: display(value),
        }))}
      />
    </>
  );
}

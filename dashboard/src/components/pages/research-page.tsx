"use client";

import { useState } from "react";
import { DataTable } from "@/components/data-table";
import { DetailSheet } from "@/components/detail-sheet";
import { PageBody, PageHeader, PageShell } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { QuietStatus } from "@/components/status-glyph";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { display } from "@/lib/format";
import type { ResearchPayload } from "@/lib/load";
import type { Contact } from "@/lib/types";

export function ResearchPage({ initial = null }: { initial?: ResearchPayload | null }): React.ReactElement {
  const { data, error, loading } = useQuery(() => api.research(), [], initial);
  const [contact, setContact] = useState<Contact | null>(null);
  const [record, setRecord] = useState<Record<string, unknown> | null>(null);

  return (
    <PageShell>
      <PageHeader
        title="Research"
        description="The CLI research-queue (max 40), plus recorded evidence and unlabeled candidates."
        crumbs={[{ href: "/", label: "Overview" }, { label: "Research" }]}
      />
      <QueryGate loading={loading} error={error} empty={!data}>
        {data ? (
          <PageBody>
            <Tabs defaultValue="queue" className="flex min-h-0 flex-1 flex-col pt-2">
              <TabsList variant="underline" size="sm">
                <TabsTab value="queue">Queue {data.queue.length}</TabsTab>
                <TabsTab value="evidence">Evidence {data.evidence.length}</TabsTab>
                <TabsTab value="candidates">Candidates {data.candidates.length}</TabsTab>
              </TabsList>
              <TabsPanel value="queue" className="min-h-0 flex-1 pt-4">
                <DataTable
                  rows={data.queue}
                  rowKey={(row) => row.id}
                  onRowClick={setContact}
                  emptyTitle="Research queue is empty"
                  emptyDescription="Everyone already has evidence, is held, or is complete."
                  columns={[
                    { key: "name", header: "Name", width: "11rem" },
                    { key: "title", header: "Title", width: "11rem" },
                    { key: "company", header: "Company", width: "13rem" },
                    {
                      key: "source_channel",
                      header: "Channel",
                      width: "7rem",
                      render: (row) => <QuietStatus value={row.source_channel} />,
                    },
                    { key: "source_url", header: "Source" },
                  ]}
                />
              </TabsPanel>
              <TabsPanel value="evidence" className="min-h-0 flex-1 pt-4">
                <DataTable
                  rows={data.evidence}
                  rowKey={(row) => String(row.id)}
                  onRowClick={setRecord}
                  emptyTitle="No evidence yet"
                  emptyDescription="Record a public source on the CLI."
                  groupOptions={[{ value: "classification", label: "Classification" }]}
                  columns={[
                    { key: "lead_id", header: "Lead", width: "10rem", mono: true },
                    { key: "email", header: "Email", width: "14rem", mono: true },
                    {
                      key: "classification",
                      header: "Class",
                      width: "10rem",
                      render: (row) => <QuietStatus value={String(row.classification)} />,
                    },
                    { key: "source_url", header: "Source", width: "16rem" },
                    { key: "excerpt", header: "Excerpt" },
                  ]}
                />
              </TabsPanel>
              <TabsPanel value="candidates" className="min-h-0 flex-1 pt-4">
                <DataTable
                  rows={data.candidates}
                  rowKey={(row) => String(row.id)}
                  onRowClick={setRecord}
                  emptyTitle="No email candidates"
                  emptyDescription="Generate candidates after a domain is known."
                  groupOptions={[{ value: "kind", label: "Kind" }]}
                  columns={[
                    { key: "lead_id", header: "Lead", width: "10rem", mono: true },
                    { key: "email", header: "Email", width: "14rem", mono: true },
                    {
                      key: "kind",
                      header: "Kind",
                      width: "12rem",
                      render: (row) => <QuietStatus value={String(row.kind)} />,
                    },
                    {
                      key: "identity_confirmed",
                      header: "Identity",
                      width: "7rem",
                      value: (row) => String(row.identity_confirmed),
                    },
                  ]}
                />
              </TabsPanel>
            </Tabs>
          </PageBody>
        ) : null}
      </QueryGate>
      <DetailSheet
        open={Boolean(contact)}
        onOpenChange={(open) => !open && setContact(null)}
        title={display(contact?.name)}
        description="Needs public evidence before a primary email is stored."
        issueId={contact?.id}
        sections={[
          {
            title: "Identity",
            fields: [
              { label: "Title", value: display(contact?.title) },
              { label: "Company", value: display(contact?.company) },
              { label: "Channel", value: display(contact?.source_channel) },
              { label: "Source", value: display(contact?.source_url) },
            ],
          },
        ]}
      />
      <DetailSheet
        open={Boolean(record)}
        onOpenChange={(open) => !open && setRecord(null)}
        title={display(record?.email || record?.id)}
        issueId={display(record?.id || record?.email)}
        fields={Object.entries(record || {}).map(([label, value]) => ({
          label,
          value: display(value),
        }))}
      />
    </PageShell>
  );
}

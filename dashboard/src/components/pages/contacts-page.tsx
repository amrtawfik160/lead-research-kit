"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { toastManager } from "@/components/ui/toast";
import { DataTable } from "@/components/data-table";
import { DetailSheet, FieldList } from "@/components/detail-sheet";
import { PageHeader } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import { display } from "@/lib/format";
import type { ContactsPayload } from "@/lib/load";
import type { Contact, ContactDetail } from "@/lib/types";

export function ContactsPage({ initial = null }: { initial?: ContactsPayload | null }): React.ReactElement {
  const { data, error, loading, reload } = useQuery(() => api.contacts(), [], initial);
  const [selected, setSelected] = useState<Contact | null>(null);
  const [detail, setDetail] = useState<ContactDetail | null>(null);
  const [reason, setReason] = useState("operator_hold");

  async function open(contact: Contact) {
    setSelected(contact);
    setDetail(await api.contact(contact.id));
  }

  async function hold() {
    if (!selected) return;
    const next = await api.hold(selected.id, reason, "dashboard");
    setDetail(next);
    toastManager.add({ type: "success", title: "Contact held", description: selected.name });
    reload();
  }

  return (
    <>
      <PageHeader
        title="Contacts"
        description="Imported workspace records. Holds stay held; LinkedIn channel is preserved."
        crumbs={[{ href: "/", label: "Overview" }, { label: "Contacts" }]}
      />
      <QueryGate loading={loading} error={error} empty={!data}>
        {data ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <DataTable
              rows={data.contacts}
              rowKey={(row) => row.id}
              onRowClick={(row) => void open(row)}
              selectedKey={selected?.id}
              groupOptions={[
                { value: "source_channel", label: "Channel" },
                { value: "held", label: "Held" },
                { value: "qualified", label: "Qualified" },
              ]}
              columns={[
                { key: "name", header: "Name" },
                { key: "title", header: "Title" },
                { key: "company", header: "Company" },
                { key: "source_channel", header: "Channel", render: (row) => <StatusBadge value={row.source_channel} /> },
                {
                  key: "qualified",
                  header: "Qualified",
                  value: (row) => (row.qualified ? "yes" : "no"),
                  render: (row) => <StatusBadge value={row.qualified ? "high" : "unknown"} />,
                },
                {
                  key: "held",
                  header: "Held",
                  value: (row) => (row.held ? "held" : "active"),
                  render: (row) => <StatusBadge value={row.held ? "held" : "enabled"} />,
                },
                { key: "email", header: "Email" },
                { key: "evidence_count", header: "Evidence" },
              ]}
            />
            <DetailSheet
              open={Boolean(selected)}
              onOpenChange={(openState) => {
                if (!openState) {
                  setSelected(null);
                  setDetail(null);
                }
              }}
              title={display(selected?.name)}
              description={display(selected?.id)}
              fields={[
                { label: "Title", value: display(detail?.contact.title || selected?.title) },
                { label: "Company", value: display(detail?.contact.company || selected?.company) },
                { label: "Channel", value: display(detail?.contact.source_channel) },
                { label: "Email", value: display(detail?.contact.email) },
                { label: "Provenance", value: display(detail?.contact.email_provenance) },
                { label: "Hold", value: display(detail?.contact.hold_reason) },
                { label: "Source", value: display(detail?.contact.source_url) },
              ]}
              extra={
                detail ? (
                  <div className="space-y-4">
                    <div>
                      <h3 className="mb-2 text-xs font-medium">Evidence</h3>
                      {detail.evidence.length === 0 ? (
                        <p className="text-xs text-muted-foreground">None recorded.</p>
                      ) : (
                        detail.evidence.map((item) => (
                          <FieldList
                            key={String(item.id)}
                            rows={[
                              { label: "Class", value: item.classification },
                              { label: "Email", value: item.email },
                              { label: "Excerpt", value: item.excerpt },
                              { label: "URL", value: item.source_url },
                            ]}
                          />
                        ))
                      )}
                    </div>
                    <div>
                      <h3 className="mb-2 text-xs font-medium">Candidates</h3>
                      {detail.candidates.length === 0 ? (
                        <p className="text-xs text-muted-foreground">None.</p>
                      ) : (
                        detail.candidates.map((item) => (
                          <p key={String(item.id)} className="text-xs">
                            {display(item.email)} · {display(item.kind)}
                          </p>
                        ))
                      )}
                    </div>
                  </div>
                ) : null
              }
              footer={
                selected && !detail?.contact.held ? (
                  <div className="flex w-full items-end gap-2">
                    <Field className="flex-1">
                      <FieldLabel>Hold reason</FieldLabel>
                      <Input size="sm" value={reason} onChange={(event) => setReason(event.target.value)} />
                    </Field>
                    <Button size="sm" variant="destructive" onClick={() => void hold()}>
                      Hold
                    </Button>
                  </div>
                ) : undefined
              }
            />
          </div>
        ) : null}
      </QueryGate>
    </>
  );
}

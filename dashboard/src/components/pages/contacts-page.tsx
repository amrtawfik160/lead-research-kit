"use client";

import { useMemo, useState } from "react";
import { DetailSheet, FieldList } from "@/components/detail-sheet";
import { IssueList, type IssueItem } from "@/components/issue-list";
import { PageBody, PageHeader } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { Button } from "@/components/ui/button";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { toastManager } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { display, explainError } from "@/lib/format";
import type { ContactsPayload } from "@/lib/load";
import type { Contact, ContactDetail } from "@/lib/types";

export function ContactsPage({ initial = null }: { initial?: ContactsPayload | null }): React.ReactElement {
  const { data, error, loading, reload } = useQuery(() => api.contacts(), [], initial);
  const [selected, setSelected] = useState<Contact | null>(null);
  const [detail, setDetail] = useState<ContactDetail | null>(null);
  const [reason, setReason] = useState("operator_hold");
  const issues = useMemo<Array<IssueItem<Contact>>>(
    () =>
      (data?.contacts || []).map((row) => ({
        id: row.id,
        title: display(row.name),
        project: display(row.company),
        status: row.held ? "held" : row.qualified ? "qualified" : "open",
        priority: display(row.source_channel),
        due: display(row.title),
        avatar: display(row.company || row.name),
        group: display(row.source_channel),
        raw: row,
      })),
    [data],
  );

  async function open(contact: Contact) {
    setSelected(contact);
    try {
      setDetail(await api.contact(contact.id));
    } catch (error) {
      setDetail(null);
      toastManager.add({
        type: "error",
        title: "Could not open contact",
        description: explainError(error instanceof Error ? error.message : "request_failed"),
      });
    }
  }

  async function hold() {
    if (!selected) return;
    try {
      const next = await api.hold(selected.id, reason, "dashboard");
      setDetail(next);
      toastManager.add({ type: "success", title: "Contact held", description: selected.name });
      reload();
    } catch (error) {
      toastManager.add({
        type: "error",
        title: "Could not hold contact",
        description: explainError(error instanceof Error ? error.message : "request_failed"),
      });
    }
  }

  return (
    <>
      <PageHeader
        title="Contacts"
        description="Imported workspace records. Holds stay held; LinkedIn channel is preserved."
        crumbs={[{ href: "/", label: "Overview" }, { label: "Contacts" }]}
      />
      <QueryGate
        loading={loading}
        error={error}
        empty={!data}
        emptyTitle="No contacts"
        emptyDescription="Import a list on the CLI, or rebuild the sample workspace."
      >
        {data ? (
          <PageBody className="flex min-h-0 flex-1 flex-col">
            <IssueList
              items={issues}
              selectedId={selected?.id}
              onSelect={(item) => {
                if (!item) {
                  setSelected(null);
                  setDetail(null);
                  return;
                }
                void open(item.raw);
              }}
              emptyTitle="No contacts match"
              emptyDescription="Clear the filter or change grouping."
              searchPlaceholder="Filter contacts…"
              searchLabel="Filter contacts"
              filterAllLabel="All statuses"
              columns={{
                title: "Contact",
                project: "Company",
                status: "Status",
                priority: "Channel",
                due: "Title",
              }}
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
              description={display(selected?.company)}
              issueId={selected?.id}
              sections={[
                {
                  title: "Identity",
                  fields: [
                    { label: "Title", value: display(detail?.contact.title || selected?.title) },
                    { label: "Company", value: display(detail?.contact.company || selected?.company) },
                    { label: "Channel", value: display(detail?.contact.source_channel) },
                    { label: "Source", value: display(detail?.contact.source_url) },
                  ],
                },
                {
                  title: "Email",
                  fields: [
                    { label: "Email", value: display(detail?.contact.email) },
                    { label: "Provenance", value: display(detail?.contact.email_provenance) },
                    { label: "Hold", value: display(detail?.contact.hold_reason) },
                  ],
                },
              ]}
              extra={
                detail ? (
                  <div className="space-y-4">
                    <div>
                      <h3 className="mb-2 text-[13.5px] font-semibold">Evidence</h3>
                      {detail.evidence.length === 0 ? (
                        <p className="text-[12px] text-muted-foreground">None recorded.</p>
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
                      <h3 className="mb-2 text-[13.5px] font-semibold">Candidates</h3>
                      {detail.candidates.length === 0 ? (
                        <p className="text-[12px] text-muted-foreground">None.</p>
                      ) : (
                        detail.candidates.map((item) => (
                          <p key={String(item.id)} className="text-[12px]">
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
          </PageBody>
        ) : null}
      </QueryGate>
    </>
  );
}

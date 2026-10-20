"use client";

import { useState } from "react";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
import { DataTable } from "@/components/data-table";
import { DetailSheet } from "@/components/detail-sheet";
import { PageHeader } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import { display, formatTime } from "@/lib/format";
import type { CompaniesPayload } from "@/lib/load";

export function CompaniesPage({ initial = null }: { initial?: CompaniesPayload | null }): React.ReactElement {
  const { data, error, loading } = useQuery(() => api.companies(), [], initial);
  const [row, setRow] = useState<Record<string, unknown> | null>(null);

  return (
    <>
      <PageHeader
        title="Companies"
        description="History domain backlog, crawl results, public-record firms, and stored prospects."
        crumbs={[{ href: "/", label: "Overview" }, { label: "Companies" }]}
      />
      <QueryGate loading={loading} error={error} empty={!data}>
        {data ? (
          <Tabs defaultValue="domains" className="flex min-h-0 flex-1 flex-col px-4 py-3 sm:px-6">
            <TabsList variant="underline" size="sm">
              <TabsTab value="domains">Domains {data.domains.length}</TabsTab>
              <TabsTab value="sites">Crawls {data.sites.length}</TabsTab>
              <TabsTab value="firms">Firms {data.firms.length}</TabsTab>
              <TabsTab value="prospects">Prospects {data.prospects.length}</TabsTab>
            </TabsList>
            <TabsPanel value="domains" className="min-h-0 flex-1 pt-3">
              <DataTable
                rows={data.domains}
                rowKey={(item) => String(item.domain)}
                onRowClick={setRow}
                groupOptions={[
                  { value: "status", label: "Status" },
                  { value: "source", label: "Source" },
                ]}
                columns={[
                  { key: "domain", header: "Domain" },
                  { key: "company", header: "Company" },
                  { key: "source", header: "Source" },
                  { key: "status", header: "Status", render: (item) => <StatusBadge value={String(item.status || "uncrawled")} /> },
                  { key: "people", header: "People" },
                  { key: "emails", header: "Emails" },
                  { key: "last_crawled", header: "Crawled", render: (item) => formatTime(Number(item.last_crawled) || null) },
                ]}
              />
            </TabsPanel>
            <TabsPanel value="sites" className="min-h-0 flex-1 pt-3">
              <DataTable
                rows={data.sites}
                rowKey={(item) => String(item.domain)}
                onRowClick={setRow}
                columns={[
                  { key: "domain", header: "Domain" },
                  { key: "status", header: "Status", render: (item) => <StatusBadge value={String(item.status)} /> },
                  { key: "people", header: "People" },
                  { key: "emails", header: "Emails" },
                  { key: "homepage_url", header: "Homepage" },
                ]}
              />
            </TabsPanel>
            <TabsPanel value="firms" className="min-h-0 flex-1 pt-3">
              <DataTable
                rows={data.firms}
                rowKey={(item) => String(item.key)}
                onRowClick={setRow}
                groupOptions={[{ value: "status", label: "Status" }]}
                columns={[
                  { key: "key", header: "Firm" },
                  { key: "status", header: "Status", render: (item) => <StatusBadge value={String(item.status)} /> },
                  { key: "domain", header: "Domain" },
                  { key: "added_at", header: "Added", render: (item) => formatTime(Number(item.added_at) || null) },
                ]}
              />
            </TabsPanel>
            <TabsPanel value="prospects" className="min-h-0 flex-1 pt-3">
              <DataTable
                rows={data.prospects}
                rowKey={(item) => `${item.full_name}-${item.company_website || item.company}`}
                onRowClick={setRow}
                columns={[
                  { key: "full_name", header: "Name" },
                  { key: "title", header: "Title" },
                  { key: "company", header: "Company" },
                  { key: "company_website", header: "Website" },
                  { key: "email", header: "Email" },
                ]}
              />
            </TabsPanel>
          </Tabs>
        ) : null}
      </QueryGate>
      <DetailSheet
        open={Boolean(row)}
        onOpenChange={(open) => !open && setRow(null)}
        title={display(row?.domain || row?.key || row?.full_name)}
        fields={Object.entries(row || {}).map(([label, value]) => ({ label, value: display(value) }))}
      />
    </>
  );
}

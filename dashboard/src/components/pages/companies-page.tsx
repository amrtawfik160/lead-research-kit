"use client";

import { useState } from "react";
import { DataTable } from "@/components/data-table";
import { DetailSheet } from "@/components/detail-sheet";
import { PageBody, PageHeader, PageShell } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { QuietStatus } from "@/components/status-glyph";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { display, formatTime, humanName } from "@/lib/format";
import type { CompaniesPayload } from "@/lib/load";

export function CompaniesPage({ initial = null }: { initial?: CompaniesPayload | null }): React.ReactElement {
  const { data, error, loading } = useQuery(() => api.companies(), [], initial);
  const [row, setRow] = useState<Record<string, unknown> | null>(null);

  return (
    <PageShell>
      <PageHeader
        title="Companies"
        description="History domain backlog, crawl results, public-record firms, and stored prospects."
        crumbs={[{ href: "/", label: "Overview" }, { label: "Companies" }]}
      />
      <QueryGate loading={loading} error={error} empty={!data}>
        {data ? (
          <PageBody>
            <Tabs defaultValue="domains" className="flex min-h-0 flex-1 flex-col pt-2">
              <TabsList variant="underline" size="sm">
                <TabsTab value="domains">Domains {data.domains.length}</TabsTab>
                <TabsTab value="sites">Crawls {data.sites.length}</TabsTab>
                <TabsTab value="firms">Firms {data.firms.length}</TabsTab>
                <TabsTab value="prospects">Prospects {data.prospects.length}</TabsTab>
              </TabsList>
              <TabsPanel value="domains" className="min-h-0 flex-1 pt-4">
                <DataTable
                  rows={data.domains}
                  rowKey={(item) => String(item.domain)}
                  onRowClick={setRow}
                  exportFilename="veinore-domains.csv"
                  emptyTitle="No domains yet"
                  emptyDescription="Discover companies on the CLI, then they land here."
                  groupOptions={[
                    { value: "status", label: "Status" },
                    { value: "source", label: "Source" },
                  ]}
                  columns={[
                    { key: "domain", header: "Domain", width: "14rem", mono: true },
                    { key: "company", header: "Company", width: "14rem" },
                    { key: "source", header: "Source", width: "9rem", value: (item) => humanName(String(item.source || "")) },
                    {
                      key: "status",
                      header: "Status",
                      width: "8rem",
                      render: (item) => <QuietStatus value={String(item.status || "uncrawled")} />,
                    },
                    { key: "people", header: "People", width: "6.5rem", align: "right" },
                    { key: "emails", header: "Emails", width: "6.5rem", align: "right" },
                    {
                      key: "last_crawled",
                      header: "Crawled",
                      width: "10rem",
                      render: (item) => formatTime(Number(item.last_crawled) || null),
                    },
                  ]}
                />
              </TabsPanel>
              <TabsPanel value="sites" className="min-h-0 flex-1 pt-4">
                <DataTable
                  rows={data.sites}
                  rowKey={(item) => String(item.domain)}
                  onRowClick={setRow}
                  exportFilename="veinore-crawls.csv"
                  emptyTitle="No crawls stored"
                  emptyDescription="Run crawl --execute to fill this tab."
                  columns={[
                    { key: "domain", header: "Domain", width: "14rem", mono: true },
                    {
                      key: "status",
                      header: "Status",
                      width: "8rem",
                      render: (item) => <QuietStatus value={String(item.status)} />,
                    },
                    { key: "people", header: "People", width: "6.5rem", align: "right" },
                    { key: "emails", header: "Emails", width: "6.5rem", align: "right" },
                    { key: "homepage_url", header: "Homepage" },
                  ]}
                />
              </TabsPanel>
              <TabsPanel value="firms" className="min-h-0 flex-1 pt-4">
                <DataTable
                  rows={data.firms}
                  rowKey={(item) => String(item.key)}
                  onRowClick={setRow}
                  exportFilename="veinore-firms.csv"
                  emptyTitle="No public-record firms"
                  emptyDescription="Record sources add firms here after a daily run."
                  groupOptions={[{ value: "status", label: "Status" }]}
                  columns={[
                    { key: "key", header: "Firm", width: "16rem" },
                    {
                      key: "status",
                      header: "Status",
                      width: "8rem",
                      render: (item) => <QuietStatus value={String(item.status)} />,
                    },
                    { key: "domain", header: "Domain", width: "14rem", mono: true },
                    {
                      key: "added_at",
                      header: "Added",
                      width: "10rem",
                      render: (item) => formatTime(Number(item.added_at) || null),
                    },
                  ]}
                />
              </TabsPanel>
              <TabsPanel value="prospects" className="min-h-0 flex-1 pt-4">
                <DataTable
                  rows={data.prospects}
                  rowKey={(item) => `${item.full_name}-${item.company_website || item.company}`}
                  onRowClick={setRow}
                  exportFilename="veinore-prospects.csv"
                  emptyTitle="No stored prospects"
                  emptyDescription="Collect or import prospects to see them here."
                  columns={[
                    { key: "full_name", header: "Name", width: "11rem" },
                    { key: "title", header: "Title", width: "11rem" },
                    { key: "company", header: "Company", width: "13rem" },
                    { key: "company_website", header: "Website", width: "12rem", mono: true },
                    { key: "email", header: "Email", width: "14rem", mono: true },
                  ]}
                />
              </TabsPanel>
            </Tabs>
          </PageBody>
        ) : null}
      </QueryGate>
      <DetailSheet
        open={Boolean(row)}
        onOpenChange={(open) => !open && setRow(null)}
        title={display(row?.domain || row?.key || row?.full_name)}
        issueId={display(row?.domain || row?.key || row?.id)}
        fields={Object.entries(row || {}).map(([label, value]) => ({ label, value: display(value) }))}
      />
    </PageShell>
  );
}

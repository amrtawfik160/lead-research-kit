"use client";

import { useState } from "react";
import { CellText } from "@/components/cell-text";
import { DataTable } from "@/components/data-table";
import { DetailSheet } from "@/components/detail-sheet";
import { PageBody, PageHeader, PageShell } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { QuietStatus } from "@/components/status-glyph";
import { Switch } from "@/components/ui/switch";
import { toastManager } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { display, explainError, humanName } from "@/lib/format";
import type { ProvidersPayload } from "@/lib/load";
import type { ProviderRow } from "@/lib/types";

export function ProvidersPage({ initial = null }: { initial?: ProvidersPayload | null }): React.ReactElement {
  const { data, error, loading, reload } = useQuery(() => api.providers(), [], initial);
  const [row, setRow] = useState<ProviderRow | null>(null);

  async function toggle(provider: ProviderRow, enabled: boolean) {
    try {
      await api.setEnabled(provider.id, enabled);
      toastManager.add({
        type: "success",
        title: enabled ? "Provider enabled" : "Provider disabled",
        description: `${provider.id} configuration only — no keys stored`,
      });
      reload();
    } catch (error) {
      toastManager.add({
        type: "error",
        title: "Could not update provider",
        description: explainError(error instanceof Error ? error.message : "request_failed"),
      });
    }
  }

  return (
    <PageShell>
      <PageHeader
        title="Providers"
        description="Enabled flags, attested free pools, and whether the required env var is present. Values are never shown."
        crumbs={[{ href: "/", label: "Overview" }, { label: "Providers" }]}
      />
      <QueryGate loading={loading} error={error} empty={!data}>
        {data ? (
          <PageBody>
            <DataTable
              rows={data.providers}
              rowKey={(item) => item.id}
              onRowClick={setRow}
              selectedKey={row?.id}
              defaultSort={{ key: "enabled", dir: "desc" }}
              exportFilename="veinore-providers.csv"
              emptyTitle="No providers"
              emptyDescription="The kit registry did not load."
              groupOptions={[
                { value: "enabled", label: "Enabled" },
                { value: "paused", label: "Paused" },
                { value: "key_present", label: "Key present" },
              ]}
              columns={[
                { key: "id", header: "Provider", width: "11rem", value: (item) => humanName(item.id) },
                {
                  key: "enabled",
                  header: "Enabled",
                  width: "6.5rem",
                  value: (item) => (item.enabled ? 1 : 0),
                  render: (item) => (
                    <Switch
                      checked={item.enabled}
                      onClick={(event) => event.stopPropagation()}
                      onCheckedChange={(checked) => void toggle(item, Boolean(checked))}
                    />
                  ),
                },
                {
                  key: "key_present",
                  header: "Key",
                  width: "6.5rem",
                  value: (item) => (item.key_present ? "present" : "missing"),
                  render: (item) => <QuietStatus value={item.key_present ? "present" : "missing"} />,
                },
                {
                  key: "paused",
                  header: "Pause",
                  width: "14rem",
                  value: (item) => (item.paused ? item.pause_reason || "paused" : "clear"),
                  render: (item) => (
                    <QuietStatus value={item.paused ? item.pause_reason || "paused" : "clear"} />
                  ),
                },
                {
                  key: "operations",
                  header: "Ops",
                  width: "13rem",
                  value: (item) => item.operations.map(humanName).join(", "),
                  render: (item) => <CellText value={item.operations.map(humanName).join(", ")} />,
                },
                {
                  key: "budgets",
                  header: "Pools",
                  value: (item) => item.budgets.map((budget) => `${budget.pool}=${budget.remaining}`).join(" "),
                  render: (item) => (
                    <CellText
                      value={
                        item.budgets.length
                          ? item.budgets
                              .map((budget) => `${humanName(budget.pool)} ${budget.remaining}`)
                              .join(" · ")
                          : "—"
                      }
                    />
                  ),
                },
              ]}
            />
            <DetailSheet
              open={Boolean(row)}
              onOpenChange={(open) => !open && setRow(null)}
              title={row ? humanName(row.id) : "Provider"}
              description={row?.auth_env.length ? `Env: ${row.auth_env.join(", ")}` : "No API key required"}
              issueId={row?.id}
              sections={[
                {
                  title: "Status",
                  fields: [
                    { label: "Enabled", value: display(row?.enabled) },
                    { label: "Paused", value: display(row?.pause_reason || row?.paused) },
                    { label: "Key present", value: display(row?.key_present) },
                    { label: "Operations", value: display(row?.operations) },
                    {
                      label: "Budgets",
                      value: row?.budgets.map((budget) => `${budget.pool} ${budget.remaining}`).join(" · ") || "—",
                    },
                  ],
                },
              ]}
            />
          </PageBody>
        ) : null}
      </QueryGate>
    </PageShell>
  );
}

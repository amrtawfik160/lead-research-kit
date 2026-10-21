"use client";

import { useState } from "react";
import { Switch } from "@/components/ui/switch";
import { toastManager } from "@/components/ui/toast";
import { DataTable } from "@/components/data-table";
import { DetailSheet } from "@/components/detail-sheet";
import { PageHeader } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import { display, explainError } from "@/lib/format";
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
    <>
      <PageHeader
        title="Providers"
        description="Enabled flags, attested free pools, and whether the required env var is present. Values are never shown."
        crumbs={[{ href: "/", label: "Overview" }, { label: "Providers" }]}
      />
      <QueryGate loading={loading} error={error} empty={!data}>
        {data ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <DataTable
              rows={data.providers}
              rowKey={(item) => item.id}
              onRowClick={setRow}
              selectedKey={row?.id}
              groupOptions={[
                { value: "enabled", label: "Enabled" },
                { value: "paused", label: "Paused" },
                { value: "key_present", label: "Key present" },
              ]}
              columns={[
                { key: "id", header: "Provider" },
                {
                  key: "enabled",
                  header: "Enabled",
                  value: (item) => (item.enabled ? "enabled" : "off"),
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
                  value: (item) => (item.key_present ? "present" : "missing"),
                  render: (item) => <StatusBadge value={item.key_present ? "present" : "missing"} />,
                },
                {
                  key: "paused",
                  header: "Pause",
                  value: (item) => (item.paused ? item.pause_reason || "paused" : "clear"),
                  render: (item) => (
                    <StatusBadge
                      value={item.paused ? item.pause_reason || "paused" : "clear"}
                      fallback={item.paused ? "warning" : "secondary"}
                    />
                  ),
                },
                {
                  key: "operations",
                  header: "Ops",
                  value: (item) => item.operations.join(", "),
                },
                {
                  key: "budgets",
                  header: "Pools",
                  value: (item) => item.budgets.map((b) => `${b.pool}=${b.remaining}`).join(" "),
                },
              ]}
            />
            <DetailSheet
              open={Boolean(row)}
              onOpenChange={(open) => !open && setRow(null)}
              title={display(row?.id)}
              description={row?.auth_env.length ? `Env: ${row.auth_env.join(", ")}` : "No API key required"}
              fields={[
                { label: "Enabled", value: display(row?.enabled) },
                { label: "Paused", value: display(row?.pause_reason || row?.paused) },
                { label: "Operations", value: display(row?.operations) },
                { label: "Key present", value: display(row?.key_present) },
                {
                  label: "Budgets",
                  value: row?.budgets.map((b) => `${b.pool} ${b.remaining}`).join(" · ") || "—",
                },
              ]}
            />
          </div>
        ) : null}
      </QueryGate>
    </>
  );
}

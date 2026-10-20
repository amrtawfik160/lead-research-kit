"use client";

import { useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { toastManager } from "@/components/ui/toast";
import { PageHeader } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { api } from "@/lib/api";
import type { SettingsPayload } from "@/lib/load";

export function SettingsPage({ initial = null }: { initial?: SettingsPayload | null }): React.ReactElement {
  const { data, error, loading, reload } = useQuery(() => api.settings(), [], initial);
  const [workspace, setWorkspace] = useState("");
  const [busy, setBusy] = useState(false);

  async function setSample(sample: boolean) {
    setBusy(true);
    try {
      await api.saveSettings({ sample });
      toastManager.add({ type: "success", title: sample ? "Sample mode on" : "Sample mode off" });
      reload();
    } finally {
      setBusy(false);
    }
  }

  async function saveWorkspace() {
    setBusy(true);
    try {
      await api.saveSettings({ workspace, sample: false });
      toastManager.add({ type: "success", title: "Workspace set", description: workspace });
      reload();
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    try {
      await api.resetSample();
      toastManager.add({ type: "success", title: "Sample workspace rebuilt" });
      reload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Settings"
        description="Point at a private workspace or use the synthetic sample. API keys stay in the environment."
        crumbs={[{ href: "/", label: "Overview" }, { label: "Settings" }]}
      />
      <QueryGate loading={loading} error={error} empty={!data}>
        {data ? (
          <div className="flex max-w-xl flex-col gap-5 p-4 sm:p-6">
            <Alert variant="info">
              <AlertTitle>Never commit the workspace</AlertTitle>
              <AlertDescription>
                This UI reads {data.workspace}. Sample mode seeds reserved example.org / .example contacts only.
              </AlertDescription>
            </Alert>
            <label className="flex items-center justify-between gap-3 text-sm">
              <span>
                Sample data
                <span className="mt-1 block text-xs text-muted-foreground">
                  Opt-in synthetic workspace built with Store, History and quality — not mock tables.
                </span>
              </span>
              <Switch
                checked={data.sample}
                disabled={busy}
                onCheckedChange={(checked) => void setSample(Boolean(checked))}
              />
            </label>
            <Field>
              <FieldLabel>Kit version</FieldLabel>
              <Input readOnly value={data.kit_version} size="sm" />
            </Field>
            <Field>
              <FieldLabel>Active workspace</FieldLabel>
              <Input readOnly value={data.workspace} size="sm" className="font-mono" />
              <FieldDescription>
                {data.initialized ? "providers.json found" : "Run `lead-research --workspace PATH init` first"}
              </FieldDescription>
            </Field>
            <Field>
              <FieldLabel>Use a real workspace</FieldLabel>
              <FieldDescription>Absolute local path. No secrets. LEAD_RESEARCH_WORKSPACE overrides this.</FieldDescription>
              <Input
                value={workspace}
                onChange={(event) => setWorkspace(event.target.value)}
                placeholder="/private/path/research"
                size="sm"
                className="font-mono"
              />
            </Field>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" disabled={busy || !workspace} onClick={() => void saveWorkspace()}>
                Use this workspace
              </Button>
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => void reset()}>
                Rebuild sample
              </Button>
            </div>
          </div>
        ) : null}
      </QueryGate>
    </>
  );
}

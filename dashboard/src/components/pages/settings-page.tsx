"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { toastManager } from "@/components/ui/toast";
import { PageBody, PageHeader, PageShell } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { Card, CardPanel } from "@/components/ui/card";
import { ThemeCards } from "@/components/theme-cards";
import { api } from "@/lib/api";
import { explainError } from "@/lib/format";
import type { SettingsPayload } from "@/lib/load";

function failureText(error: unknown): string {
  return explainError(error instanceof Error ? error.message : "request_failed");
}

export function SettingsPage({ initial = null }: { initial?: SettingsPayload | null }): React.ReactElement {
  const router = useRouter();
  const { data, error, loading, reload } = useQuery(() => api.settings(), [], initial);
  const [workspace, setWorkspace] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    const saved = data?.settings.workspace;
    if (saved) setWorkspace((current) => current || saved);
  }, [data]);

  async function run(action: () => Promise<SettingsPayload>, success: string, description?: string) {
    setBusy(true);
    setFormError(null);
    try {
      const result = await action();
      if (result.env_locked) {
        toastManager.add({
          type: "warning",
          title: "Saved, environment still wins",
          description: "LEAD_RESEARCH_WORKSPACE is set, so this process keeps that folder.",
        });
      } else {
        toastManager.add({ type: "success", title: success, description });
      }
      reload();
      router.refresh();
    } catch (err) {
      const text = failureText(err);
      setFormError(text);
      toastManager.add({ type: "error", title: "Settings were not changed", description: text });
    } finally {
      setBusy(false);
    }
  }

  async function setSample(sample: boolean) {
    if (!sample) {
      const path = workspace.trim() || data?.settings.workspace || "";
      if (!path) {
        const text = explainError("workspace_required");
        setFormError(text);
        toastManager.add({ type: "error", title: "Workspace path required", description: text });
        return;
      }
      await run(() => api.saveSettings({ sample: false, workspace: path }), "Sample mode off", path);
      return;
    }
    await run(() => api.saveSettings({ sample: true }), "Sample mode on");
  }

  const locked = Boolean(data?.env_locked);

  return (
    <PageShell width="wide">
      <PageHeader
        title="Settings"
        description="Point Veinore at a private workspace, or use the synthetic sample. API keys stay in the environment."
        crumbs={[{ href: "/", label: "Overview" }, { label: "Settings" }]}
      />
      <QueryGate loading={loading} error={error} empty={!data} skeleton="form">
        {data ? (
          <PageBody className="grid gap-4 pb-16 lg:grid-cols-2 lg:items-start">
            <Card>
              <CardPanel className="flex flex-col gap-4 p-4">
                <div>
                  <h2 className="font-heading text-lg font-semibold">Workspace & data source</h2>
                  <p className="mt-1 text-sm text-muted-foreground">
                    Configure which local research directory Veinore reads from.
                  </p>
                </div>
                {locked ? (
                  <Alert variant="warning">
                    <AlertTitle>Environment workspace is active</AlertTitle>
                    <AlertDescription>
                      LEAD_RESEARCH_WORKSPACE selects the folder for this process. A path saved here is kept for the next launch without that variable.
                    </AlertDescription>
                  </Alert>
                ) : null}
                {data.settings_error ? (
                  <Alert variant="error">
                    <AlertTitle>Saved workspace is not usable</AlertTitle>
                    <AlertDescription>{explainError(data.settings_error)}</AlertDescription>
                  </Alert>
                ) : null}
                {formError ? (
                  <Alert variant="error">
                    <AlertTitle>That change was rejected</AlertTitle>
                    <AlertDescription>{formError}</AlertDescription>
                  </Alert>
                ) : null}
                <Alert variant="info">
                  <AlertTitle>Never commit the workspace</AlertTitle>
                  <AlertDescription>
                    This UI reads {data.workspace || "no folder"}. Sample mode seeds reserved example.org / .example contacts only.
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
                    disabled={busy || locked}
                    onCheckedChange={(checked) => void setSample(Boolean(checked))}
                    aria-label="Sample data"
                  />
                </label>
                <Field>
                  <FieldLabel>Kit version</FieldLabel>
                  <Input readOnly value={data.kit_version} size="sm" />
                </Field>
                <Field>
                  <FieldLabel>Active workspace</FieldLabel>
                  <Input readOnly value={data.workspace || "—"} size="sm" className="font-mono" />
                  <FieldDescription>
                    {data.sample
                      ? "Sample workspace"
                      : data.initialized
                        ? "providers.json found"
                        : "Run `lead-research --workspace PATH init` first"}
                  </FieldDescription>
                </Field>
                <Field>
                  <FieldLabel>Use a real workspace</FieldLabel>
                  <FieldDescription>Absolute local path. No secrets. LEAD_RESEARCH_WORKSPACE overrides this while it is set.</FieldDescription>
                  <Input
                    value={workspace}
                    onChange={(event) => setWorkspace(event.target.value)}
                    placeholder="/private/path/research"
                    size="sm"
                    className="font-mono"
                    disabled={locked}
                    aria-label="Workspace path"
                  />
                </Field>
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    disabled={busy || locked || !workspace.trim()}
                    onClick={() => void run(() => api.saveSettings({ workspace: workspace.trim(), sample: false }), "Workspace set", workspace.trim())}
                  >
                    Use this workspace
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy || locked}
                    onClick={() => void run(() => api.resetSample().then(() => api.settings()), "Sample workspace rebuilt")}
                  >
                    Rebuild sample
                  </Button>
                </div>
              </CardPanel>
            </Card>
            <div className="flex flex-col gap-4">
              <Card>
                <CardPanel className="flex flex-col gap-4 p-4">
                  <div>
                    <h2 className="font-heading text-lg font-semibold">Theme</h2>
                    <p className="mt-1 text-sm text-muted-foreground">Customize how Veinore looks on this device.</p>
                  </div>
                  <ThemeCards />
                </CardPanel>
              </Card>
              <Card>
                <CardPanel className="flex flex-col gap-3 p-4">
                  <div>
                    <h2 className="font-heading text-lg font-semibold">Operator guardrails & shortcuts</h2>
                    <p className="mt-1 text-sm text-muted-foreground">
                      Built-in keyboard controls and network safety policies.
                    </p>
                  </div>
                  <dl className="grid grid-cols-[120px_minmax(0,1fr)] items-center gap-y-2 text-sm">
                    <dt className="text-muted-foreground">Command palette</dt>
                    <dd className="font-mono text-xs">⌘K / Ctrl+K</dd>
                    <dt className="text-muted-foreground">Shortcut guide</dt>
                    <dd className="font-mono text-xs">?</dd>
                    <dt className="text-muted-foreground">Row navigation</dt>
                    <dd className="font-mono text-xs">J / K or ↑ / ↓, Enter</dd>
                    <dt className="text-muted-foreground">Network policy</dt>
                    <dd className="text-muted-foreground">Dry-run by default; no live provider calls from UI</dd>
                  </dl>
                </CardPanel>
              </Card>
            </div>
          </PageBody>
        ) : null}
      </QueryGate>
    </PageShell>
  );
}

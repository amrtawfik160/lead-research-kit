"use client";

import { useState } from "react";
import { Users } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardPanel } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { toastManager } from "@/components/ui/toast";
import { DataTable } from "@/components/data-table";
import { PageBody, PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import type { CleanResult } from "@/lib/types";

const SAMPLE_CSV = `Full Name,Title,Company,Company Website,LinkedIn URL,Email
Jane Sample,Founder & CEO,Harbor Realty Group,harborrealty.example,https://www.linkedin.com/in/jane-sample/,jane@harborrealty.example
Not A Person LLC,Agent,Random Association,association.example,,info@example.org
Robin Taylor,CEO,Example Holdings,example.net,,robin@example.net
`;

export function QualityPage(): React.ReactElement {
  const [csv, setCsv] = useState(SAMPLE_CSV);
  const [minTier, setMinTier] = useState("B");
  const [allowPersonal, setAllowPersonal] = useState(false);
  const [keepOthers, setKeepOthers] = useState(false);
  const [result, setResult] = useState<CleanResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const next = await api.clean({
        csv,
        min_tier: minTier,
        allow_personal_email: allowPersonal,
        keep_non_decision_makers: keepOthers,
      });
      setResult(next);
      toastManager.add({
        type: "success",
        title: "Cleaned",
        description: `${next.summary.clean} clean · ${next.summary.review} review · ${next.summary.rejected} rejected`,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "clean_failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Quality"
        description="Runs the offline quality engine (clean / review / rejected). Nothing is sent to providers."
        crumbs={[{ href: "/", label: "Overview" }, { label: "Quality" }]}
        actions={
          <Button size="sm" onClick={() => void run()} disabled={busy}>
            {busy ? "Cleaning…" : "Clean list"}
          </Button>
        }
      />
      <PageBody className="grid gap-4 px-6 pb-16 lg:grid-cols-[minmax(0,22rem)_1fr]">
        <Card>
        <CardPanel className="flex flex-col gap-4 p-4">
          <Field>
            <FieldLabel>Lead list (CSV or JSON)</FieldLabel>
            <FieldDescription>Same loose column matching as `lead-research clean`.</FieldDescription>
            <Textarea value={csv} onChange={(event) => setCsv(event.target.value)} rows={14} className="font-mono text-xs" />
          </Field>
          <Field>
            <FieldLabel>Minimum tier for clean</FieldLabel>
            <Select value={minTier} onValueChange={(value) => setMinTier(String(value))}>
              <SelectTrigger size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectPopup>
                <SelectItem value="A">A</SelectItem>
                <SelectItem value="B">B</SelectItem>
                <SelectItem value="C">C</SelectItem>
              </SelectPopup>
            </Select>
          </Field>
          <label className="flex items-center justify-between gap-3 text-sm">
            Allow personal email
            <Switch checked={allowPersonal} onCheckedChange={setAllowPersonal} />
          </label>
          <label className="flex items-center justify-between gap-3 text-sm">
            Keep non-decision-makers
            <Switch checked={keepOthers} onCheckedChange={setKeepOthers} />
          </label>
        </CardPanel>
        </Card>
        <div className="min-w-0">
          {error ? (
            <Alert variant="error">
              <AlertTitle>Clean failed</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          {result ? (
            <div className="flex min-h-0 flex-col gap-3">
              <div className="flex flex-wrap gap-2 text-xs">
                <StatusBadge value={`${result.summary.clean} clean`} />
                <StatusBadge value={`${result.summary.review} review`} />
                <StatusBadge value={`${result.summary.rejected} rejected`} />
                <span className="text-muted-foreground">
                  {result.summary.with_work_email} work emails · {result.summary.duplicates_merged} dupes merged
                </span>
              </div>
              <Tabs defaultValue="clean">
                <TabsList variant="underline" size="sm">
                  <TabsTab value="clean">Clean</TabsTab>
                  <TabsTab value="review">Review</TabsTab>
                  <TabsTab value="rejected">Rejected</TabsTab>
                </TabsList>
                <TabsPanel value="clean" className="pt-3">
                  <DataTable
                    rows={result.clean}
                    rowKey={(row) => `${row.full_name}-${row.email}`}
                    columns={[
                      { key: "full_name", header: "Name", width: "11rem" },
                      { key: "title", header: "Title", width: "11rem" },
                      { key: "company", header: "Company", width: "13rem" },
                      { key: "email", header: "Email", width: "14rem", mono: true },
                      { key: "tier", header: "Tier", width: "4.5rem", render: (row) => <StatusBadge value={String(row.tier)} /> },
                      { key: "score", header: "Score", width: "5rem", align: "right" },
                    ]}
                  />
                </TabsPanel>
                <TabsPanel value="review" className="pt-3">
                  <DataTable
                    rows={result.review}
                    rowKey={(row) => `${row.full_name}-${row.email}-review`}
                    columns={[
                      { key: "full_name", header: "Name" },
                      { key: "title", header: "Title" },
                      { key: "issues", header: "Issues" },
                      { key: "tier", header: "Tier" },
                    ]}
                  />
                </TabsPanel>
                <TabsPanel value="rejected" className="pt-3">
                  <DataTable
                    rows={result.rejected}
                    rowKey={(row) => `${row.full_name}-${row.email}-rej`}
                    columns={[
                      { key: "full_name", header: "Name" },
                      { key: "reject_reasons", header: "Reasons" },
                      { key: "issues", header: "Issues" },
                    ]}
                  />
                </TabsPanel>
              </Tabs>
            </div>
          ) : (
            <EmptyState
              className="min-h-64 rounded-[10px] border"
              icon={Users}
              title="Paste a list"
              description="Sample CSV is already loaded. Run Clean — nothing is sent to providers."
            />
          )}
        </div>
      </PageBody>
    </>
  );
}

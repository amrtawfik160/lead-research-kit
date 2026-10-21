"use client";

import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { PageHeader } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import { display } from "@/lib/format";
import type { Overview } from "@/lib/types";

export function OverviewPage({ initial = null }: { initial?: Overview | null }): React.ReactElement {
  const { data, error, loading } = useQuery(() => api.overview(), [], initial);
  const delivered = data?.latest_daily?.delivered ?? 0;
  const target = data?.latest_daily?.target ?? 0;
  const pct = target ? Math.min(100, Math.round((delivered / target) * 100)) : 0;

  return (
    <>
      <PageHeader
        title="Overview"
        description="Today's delivery, ledger health, and what the kit says to do next."
        crumbs={[{ label: "Overview" }]}
        actions={
          <Button size="sm" variant="outline" render={<Link href="/daily" />}>
            Open daily
          </Button>
        }
      />
      <QueryGate loading={loading} error={error} empty={!data}>
        {data ? (
          <div className="flex flex-col gap-4 p-4 sm:p-6">
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <Stat
                label="Delivered"
                value={`${delivered} / ${target || "—"}`}
                hint={data.latest_daily?.date || "No daily file yet"}
              />
              <Stat
                label="Contacts"
                value={String(data.status.contacts)}
                hint={`${data.status.held} held · ${data.research_queue_count} in research queue`}
              />
              <Stat
                label="Domain backlog"
                value={String(data.history.domains_uncrawled ?? 0)}
                hint={`${data.history.domains_known ?? 0} known · ${data.history.sites_stored ?? 0} crawled`}
              />
              <Stat
                label="Audit chain"
                value={data.status.audit_ok ? "Intact" : "Broken"}
                hint={data.meta.sample ? "Sample workspace" : data.meta.workspace}
              />
            </div>
            <Card>
              <CardHeader>
                <CardTitle>Latest daily run</CardTitle>
                <CardDescription>
                  {data.latest_daily
                    ? `${data.latest_daily.date} · ${data.latest_daily.execute ? "executed" : "preview"}`
                    : "No daily/<date> folder yet"}
                </CardDescription>
              </CardHeader>
              <CardPanel className="flex flex-col gap-3">
                <Progress value={pct} />
                <div className="flex flex-wrap gap-2 text-xs">
                  {Object.entries(data.report?.delivered_by_source || {}).map(([key, count]) => (
                    <span key={key} className="rounded-md border px-2 py-1">
                      {key} {count}
                    </span>
                  ))}
                  {Object.entries(data.report?.delivered_by_risk || {}).map(([key, count]) => (
                    <StatusBadge key={key} value={`${key || "none"} ${count}`} />
                  ))}
                </div>
              </CardPanel>
            </Card>
            {data.advice.length ? (
              <Alert variant="info">
                <AlertTitle>Shortfall advice</AlertTitle>
                <AlertDescription>
                  <ul className="mt-2 list-disc space-y-1 ps-4">
                    {data.advice.map((tip) => (
                      <li key={tip}>{tip}</li>
                    ))}
                  </ul>
                </AlertDescription>
              </Alert>
            ) : null}
            <div className="grid gap-3 lg:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>Unresolved attempts</CardTitle>
                  <CardDescription>Reserved, sent, pending, or uncertain ledger rows</CardDescription>
                </CardHeader>
                <CardPanel>
                  {data.status.unresolved_attempts.length === 0 ? (
                    <p className="text-xs text-muted-foreground">None. Resume is only for saved GET-pollable jobs.</p>
                  ) : (
                    <ul className="space-y-2 text-xs">
                      {data.status.unresolved_attempts.map((attempt) => (
                        <li key={attempt.id} className="flex items-center justify-between gap-2">
                          <span className="truncate font-mono">{attempt.id.slice(0, 10)}</span>
                          <span>{attempt.provider}</span>
                          <StatusBadge value={attempt.state} />
                        </li>
                      ))}
                    </ul>
                  )}
                </CardPanel>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>Budgets</CardTitle>
                  <CardDescription>Confirmed free pools only — keys stay in the environment</CardDescription>
                </CardHeader>
                <CardPanel>
                  {data.status.budgets.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No attested pools. Use `budget confirm` on the CLI.</p>
                  ) : (
                    <ul className="space-y-2 text-xs">
                      {data.status.budgets.map((budget) => (
                        <li key={`${budget.provider}-${budget.pool}`} className="flex justify-between gap-2">
                          <span>
                            {budget.provider}.{budget.pool}
                          </span>
                          <span className="font-mono">{display(budget.remaining)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardPanel>
              </Card>
            </div>
          </div>
        ) : null}
      </QueryGate>
    </>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint: string }): React.ReactElement {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl font-medium tracking-tight">{value}</CardTitle>
      </CardHeader>
      <CardPanel className="pt-0 text-xs text-muted-foreground">{hint}</CardPanel>
    </Card>
  );
}

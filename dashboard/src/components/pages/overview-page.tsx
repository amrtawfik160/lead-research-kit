"use client";

import { Activity, Inbox, Link as LinkIcon, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { CountBar } from "@/components/count-bar";
import { PageBody, PageHeader } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { StatusBadge } from "@/components/status-badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";
import { Progress, ProgressIndicator, ProgressTrack } from "@/components/ui/progress";
import { api } from "@/lib/api";
import { display } from "@/lib/format";
import type { Overview } from "@/lib/types";
import { cn } from "@/lib/utils";

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
      <QueryGate loading={loading} error={error} empty={!data} skeleton="cards">
        {data ? (
          <PageBody className="flex flex-col gap-4 p-4 sm:p-6">
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <Stat
                icon={Inbox}
                label="Delivered"
                value={`${delivered} / ${target || "—"}`}
                hint={data.latest_daily?.date || "No daily file yet"}
              />
              <Stat
                icon={Activity}
                label="Contacts"
                value={String(data.status.contacts)}
                hint={`${data.status.held} held · ${data.research_queue_count} in research queue`}
              />
              <Stat
                icon={LinkIcon}
                label="Domain backlog"
                value={String(data.history.domains_uncrawled ?? 0)}
                hint={`${data.history.domains_known ?? 0} known · ${data.history.sites_stored ?? 0} crawled`}
              />
              <Stat
                icon={ShieldCheck}
                label="Audit chain"
                value={data.status.audit_ok ? "Intact" : "Broken"}
                hint={data.meta.sample ? "Sample workspace" : data.meta.workspace}
                tone={data.status.audit_ok ? "ok" : "bad"}
              />
            </div>
            <Card>
              <CardHeader className="px-5 py-4">
                <CardTitle className="text-sm font-medium">Latest daily run</CardTitle>
                <CardDescription>
                  {data.latest_daily
                    ? `${data.latest_daily.date} · ${data.latest_daily.execute ? "executed" : "preview"} · ${pct}% of target`
                    : "No daily/<date> folder yet"}
                </CardDescription>
              </CardHeader>
              <CardPanel className="flex flex-col gap-4 px-5 pb-5">
                <Progress value={pct}>
                  <ProgressTrack className="h-1.5">
                    <ProgressIndicator className="bg-[#5e6ad2]" />
                  </ProgressTrack>
                </Progress>
                <div className="grid gap-4 md:grid-cols-2">
                  <div>
                    <p className="mb-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                      By source
                    </p>
                    <CountBar counts={data.report?.delivered_by_source} />
                  </div>
                  <div>
                    <p className="mb-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                      By email risk
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {Object.entries(data.report?.delivered_by_risk || {}).map(([key, count]) => (
                        <span key={key} className="inline-flex items-center gap-1.5 text-[12px]">
                          <StatusBadge value={key || "none"} />
                          <span className="tabular-nums text-muted-foreground">{count}</span>
                        </span>
                      ))}
                    </div>
                  </div>
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
                <CardHeader className="px-5 py-4">
                  <CardTitle className="text-sm font-medium">Unresolved attempts</CardTitle>
                  <CardDescription>Reserved, sent, pending, or uncertain ledger rows</CardDescription>
                </CardHeader>
                <CardPanel className="px-5 pb-5">
                  {data.status.unresolved_attempts.length === 0 ? (
                    <p className="text-[12px] text-muted-foreground">
                      None. Resume is only for saved GET-pollable jobs.
                    </p>
                  ) : (
                    <ul className="space-y-2 text-[12px]">
                      {data.status.unresolved_attempts.map((attempt) => (
                        <li key={attempt.id} className="flex items-center justify-between gap-2">
                          <span className="truncate font-mono text-[11px]">{attempt.id.slice(0, 10)}</span>
                          <span className="text-muted-foreground">{attempt.provider}</span>
                          <StatusBadge value={attempt.state} />
                        </li>
                      ))}
                    </ul>
                  )}
                </CardPanel>
              </Card>
              <Card>
                <CardHeader className="px-5 py-4">
                  <CardTitle className="text-sm font-medium">Budgets</CardTitle>
                  <CardDescription>Confirmed free pools only — keys stay in the environment</CardDescription>
                </CardHeader>
                <CardPanel className="px-5 pb-5">
                  {data.status.budgets.length === 0 ? (
                    <p className="text-[12px] text-muted-foreground">
                      No attested pools. Use `budget confirm` on the CLI.
                    </p>
                  ) : (
                    <ul className="space-y-1.5 text-[12px]">
                      {data.status.budgets.map((budget) => (
                        <li
                          key={`${budget.provider}-${budget.pool}`}
                          className="flex justify-between gap-3 rounded-md px-1 py-0.5 hover:bg-muted/60"
                        >
                          <span className="truncate text-muted-foreground">
                            {budget.provider}.{budget.pool}
                          </span>
                          <span className="font-mono tabular-nums">{display(budget.remaining)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardPanel>
              </Card>
            </div>
          </PageBody>
        ) : null}
      </QueryGate>
    </>
  );
}

function Stat({
  icon: Icon,
  label,
  value,
  hint,
  tone,
}: {
  icon: typeof Inbox;
  label: string;
  value: string;
  hint: string;
  tone?: "ok" | "bad";
}): React.ReactElement {
  return (
    <Card>
      <CardHeader className="gap-2 px-4 py-3.5 pb-2">
        <div className="flex items-center justify-between gap-2">
          <CardDescription className="text-[11px] tracking-wide uppercase">{label}</CardDescription>
          <Icon className="size-3.5 text-muted-foreground" />
        </div>
        <CardTitle
          className={cn(
            "text-[1.65rem] font-medium tracking-tight tabular-nums",
            tone === "ok" && "text-success-foreground",
            tone === "bad" && "text-destructive-foreground",
          )}
        >
          {value}
        </CardTitle>
      </CardHeader>
      <CardPanel className="px-4 pt-0 pb-3.5 text-[12px] text-muted-foreground">{hint}</CardPanel>
    </Card>
  );
}

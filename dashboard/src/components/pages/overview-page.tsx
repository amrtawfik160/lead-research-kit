"use client";

import { useEffect, useState } from "react";
import { ArrowUpRight, Calendar, CircleCheck, CircleDashed, ClockAlert, Users } from "lucide-react";
import Link from "next/link";
import { DuplicateBanner } from "@/components/duplicate-banner";
import { EmptyState } from "@/components/empty-state";
import { EntityAvatar } from "@/components/entity-avatar";
import { LabelChip } from "@/components/label-chip";
import { ChartCard, HorizontalBarChart, LeadsPerDayChart, QuotaChart, VerificationChart } from "@/components/workspace-charts";
import { PageBody, PageHeader, PageShell } from "@/components/page-header";
import { Panel, PanelHeader, SegTabs } from "@/components/panel";
import { ProgBar } from "@/components/prog-bar";
import { QueryGate, useQuery } from "@/components/query-state";
import { QuietStatus, PriorityGlyph, StatusGlyph } from "@/components/status-glyph";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { chartPool, display, humanName } from "@/lib/format";
import { greeting, longDate } from "@/lib/gr8r";
import { isVerifiedLead } from "@/lib/lead-ops";
import type { LeadRow, Overview } from "@/lib/types";
import { cn } from "@/lib/utils";

export function OverviewPage({ initial = null }: { initial?: Overview | null }): React.ReactElement {
  const { data, error, loading } = useQuery(() => api.overview(), [], initial);
  const [leads, setLeads] = useState<LeadRow[] | null>(null);
  const [tab, setTab] = useState<"verified" | "review" | "website">("verified");

  useEffect(() => {
    let cancelled = false;
    api
      .leads()
      .then((payload) => {
        if (!cancelled) setLeads(payload.leads);
      })
      .catch(() => {
        if (!cancelled) setLeads([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const delivered = data?.latest_daily?.delivered ?? 0;
  const target = data?.latest_daily?.target ?? 0;
  const held = data?.status.held ?? 0;
  const charts = data?.charts;
  const lists = {
    verified: (leads || []).filter((row) => isVerifiedLead(row)),
    review: (leads || []).filter((row) => /high|medium/.test(String(row["Email Risk"] || "").toLowerCase())),
    website: (leads || []).filter((row) => String(row.Source || row["Email Source"] || "").toLowerCase().includes("website")),
  };

  return (
    <PageShell width="wide">
      <QueryGate loading={loading} error={error} empty={!data} skeleton="cards">
        {data ? (
          <PageBody className="gap-4 pb-16">
            <PageHeader
              title={`${greeting()}`}
              description={`${longDate()} · Lead deliveries and provider quota across this workspace.`}
              actions={
                <>
                  <Button size="sm" variant="outline" render={<Link href="/leads" />}>
                    Leads
                  </Button>
                  <Button size="sm" variant="outline" render={<Link href="/quality" />}>
                    Clean list
                  </Button>
                  <Button size="sm" render={<Link href="/daily" />}>
                    Open daily
                  </Button>
                </>
              }
            />
            <DuplicateBanner items={data.duplicates} className="mb-0" />
            <div className="grid grid-cols-2 overflow-hidden rounded-xl border sm:grid-cols-4">
              <Stat
                href="/daily"
                icon={Users}
                label="Delivered"
                value={`${delivered}`}
                hint={target ? `${target} target` : "No daily file yet"}
              />
              <Stat
                href="/contacts"
                icon={CircleDashed}
                label="Open contacts"
                value={String(data.status.contacts)}
                hint={`${held} held · ${data.research_queue_count} in research`}
              />
              <Stat
                href="/quality"
                icon={CircleCheck}
                label="Audit"
                value={data.status.audit_ok ? "Intact" : "Broken"}
                hint={data.meta.sample ? "Sample workspace" : data.meta.workspace}
                tone={data.status.audit_ok ? "ok" : "bad"}
              />
              <Stat
                href="/history"
                icon={ClockAlert}
                label="Domain backlog"
                value={String(data.history.domains_uncrawled ?? 0)}
                hint={`${data.history.domains_known ?? 0} known`}
                tone={(data.history.domains_uncrawled ?? 0) > 0 ? "bad" : undefined}
              />
            </div>
            <div className="grid gap-4 lg:grid-cols-6">
              <ChartCard className="lg:col-span-3" title="Leads per day" hint={`${charts?.leads_per_day.length || 0} days`}>
                <LeadsPerDayChart points={charts?.leads_per_day || []} />
              </ChartCard>
              <ChartCard className="lg:col-span-3" title="Verification rate" hint={`${charts?.verification.verified || 0} verified`}>
                <VerificationChart
                  verified={charts?.verification.verified || 0}
                  unverified={charts?.verification.unverified || 0}
                />
              </ChartCard>
              <ChartCard className="lg:col-span-2" title="Lead sources">
                <HorizontalBarChart
                  rows={(charts?.sources || []).slice(0, 6).map((row) => ({
                    label: humanName(row.name),
                    value: row.count,
                  }))}
                  emptyTitle="No source mix"
                  emptyDescription="Deliver a daily file to see where emails came from."
                />
              </ChartCard>
              <ChartCard className="lg:col-span-2" title="Provider quota">
                <QuotaChart
                  rows={(charts?.provider_quota || []).slice(0, 6).map((row) => ({
                    label: chartPool(`${row.id}.${row.pool}`),
                    remaining: displayUnits(row.remaining),
                    spent: displayUnits(row.spent),
                  }))}
                />
              </ChartCard>
              <ChartCard className="lg:col-span-2" title="Budget spent" hint={charts?.budget_spent.length ? `${charts.budget_spent.length} pools` : "none"}>
                <HorizontalBarChart
                  rows={(charts?.budget_spent || []).slice(0, 6).map((row) => ({
                    label: chartPool(`${row.provider}.${row.pool}`),
                    value: displayUnits(row.spent),
                  }))}
                  emptyTitle="No provider spend"
                  emptyDescription="Completed attempts in this workspace have not charged a pool."
                />
              </ChartCard>
            </div>
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
              <div className="flex flex-col gap-4">
                <Panel>
                  <PanelHeader
                    title="Latest leads"
                    actions={
                      <>
                        <SegTabs
                          value={tab}
                          onChange={setTab}
                          items={[
                            { value: "verified", label: "Verified", count: lists.verified.length },
                            { value: "review", label: "Needs review", count: lists.review.length },
                            { value: "website", label: "Website", count: lists.website.length },
                          ]}
                        />
                        <Button size="icon-xs" variant="ghost" render={<Link href="/leads" />} aria-label="Open leads">
                          <ArrowUpRight />
                        </Button>
                      </>
                    }
                  />
                  {lists[tab].length ? (
                    lists[tab].slice(0, 7).map((row, index) => <MiniLead key={`${row.Email}-${index}`} row={row} />)
                  ) : (
                    <EmptyState
                      compact
                      title={tab === "review" ? "Nothing needs review" : "No leads here"}
                      description={
                        tab === "review"
                          ? "Every lead in this view is low risk."
                          : "Run a daily delivery to populate this list."
                      }
                    />
                  )}
                </Panel>
                <Panel>
                  <PanelHeader
                    title="Provider usage"
                    actions={
                      <Button size="xs" variant="ghost" render={<Link href="/providers" />}>
                        All providers
                      </Button>
                    }
                  />
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-xl text-sm">
                      <thead>
                        <tr className="border-t text-left text-xs font-medium text-muted-foreground">
                          <th className="px-4 py-2 font-medium">Provider</th>
                          <th className="py-2 font-medium">Status</th>
                          <th className="py-2 font-medium">Free quota</th>
                          <th className="px-4 py-2 text-right font-medium">Pools</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.values(data.status.providers)
                          .slice(0, 6)
                          .map((provider) => {
                            const pools = provider.budgets.length;
                            const remaining = provider.budgets.reduce((sum, budget) => sum + (budget.remaining_units || 0), 0);
                            const pct = provider.enabled ? (provider.key_present ? (remaining ? 100 : 64) : 30) : 8;
                            return (
                              <tr key={provider.id} className="border-t">
                                <td className="px-4 py-2.5">
                                  <LabelChip value={provider.id} />
                                </td>
                                <td className="py-2.5">
                                  <QuietStatus value={provider.paused ? "paused" : provider.enabled ? "enabled" : "off"} />
                                </td>
                                <td className="py-2.5">
                                  <span className="flex items-center gap-2">
                                    <ProgBar
                                      value={pct}
                                      tone={provider.paused ? "bad" : provider.enabled && provider.key_present ? "ok" : undefined}
                                    />
                                    <span className="w-8 text-xs tabular-nums text-muted-foreground">{pct}%</span>
                                  </span>
                                </td>
                                <td className="px-4 py-2.5 text-right font-mono text-xs tabular-nums text-muted-foreground">
                                  {pools || "—"}
                                </td>
                              </tr>
                            );
                          })}
                      </tbody>
                    </table>
                  </div>
                </Panel>
              </div>
              <div className="flex flex-col gap-4">
                <Panel>
                  <PanelHeader
                    title="Today's delivery"
                    actions={
                      <Button size="xs" variant="ghost" render={<Link href="/daily" />}>
                        <Calendar className="size-3.5" />
                        Daily
                      </Button>
                    }
                  />
                  <div className="px-4 pb-3">
                    <DeadlineGroup label="This run" count={data.report?.this_run} />
                    <div className="flex min-h-8 items-center gap-2 text-sm">
                      <span className="size-2 rounded-sm bg-info" />
                      <span className="issue-title min-w-0 flex-1 truncate">{data.latest_daily?.date || "No daily file"}</span>
                      <span className="tabular-nums text-muted-foreground">
                        {delivered}/{target || "—"}
                      </span>
                    </div>
                    <DeadlineGroup label="Advice" count={data.advice.length} />
                    {data.advice.slice(0, 4).map((tip) => (
                      <div key={tip} className="flex min-h-8 items-center gap-2 text-sm">
                        <span className="size-2 rounded-sm bg-warning" />
                        <span className="min-w-0 flex-1 truncate">{tip}</span>
                      </div>
                    ))}
                  </div>
                </Panel>
                <Panel>
                  <PanelHeader
                    title="Recent attempts"
                    actions={
                      <Button size="xs" variant="ghost" render={<Link href="/jobs" />}>
                        View all
                      </Button>
                    }
                  />
                  <div className="px-4 pb-3">
                    {data.status.unresolved_attempts.length === 0 ? (
                      <p className="py-2 text-sm text-muted-foreground">
                        No unresolved attempts. Resume is only for saved GET-pollable jobs.
                      </p>
                    ) : (
                      data.status.unresolved_attempts.map((attempt) => (
                        <div key={attempt.id} className="flex min-h-10 items-center gap-2.5 border-t first:border-t-0">
                          <EntityAvatar name={attempt.provider} size={22} />
                          <div className="min-w-0 flex-1 text-sm">
                            <span className="font-medium">{humanName(attempt.provider)}</span>{" "}
                            <span className="text-muted-foreground">{humanName(attempt.operation)}</span>
                          </div>
                          <QuietStatus value={attempt.state} />
                        </div>
                      ))
                    )}
                  </div>
                </Panel>
              </div>
            </div>
          </PageBody>
        ) : null}
      </QueryGate>
    </PageShell>
  );
}

function displayUnits(value?: string | number | null): number {
  const text = value == null ? "" : String(value);
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : 0;
}

function DeadlineGroup({ label, count }: { label: string; count?: number }): React.ReactElement {
  return (
    <div className="mb-1 mt-1 flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
      {label}
      <span className="font-medium text-muted-foreground">{count ?? 0}</span>
    </div>
  );
}

function MiniLead({ row }: { row: LeadRow }): React.ReactElement {
  return (
    <Link
      href="/leads"
      className="flex min-h-10 items-center gap-2.5 border-t px-4 text-sm transition-colors hover:bg-muted"
    >
      <StatusGlyph value={row["Email Risk"]} />
      <span className="issue-title min-w-0 flex-1 truncate">{display(row["Full Name"])}</span>
      <LabelChip value={row.Company} className="hidden max-w-36 sm:inline-flex" />
      <QuietStatus value={row.Stage || "new"} />
      <PriorityGlyph value={row.Tier} />
      <span className="w-14 truncate text-right text-xs text-muted-foreground">{display(row.City)}</span>
    </Link>
  );
}

function Stat({
  href,
  icon: Icon,
  label,
  value,
  hint,
  tone,
}: {
  href: string;
  icon: typeof Users;
  label: string;
  value: string;
  hint: string;
  tone?: "ok" | "bad";
}): React.ReactElement {
  return (
    <Link
      href={href}
      className="flex flex-col gap-2 border-l px-4 py-4 first:border-l-0 transition-colors hover:bg-muted sm:first:border-l-0"
    >
      <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
        <Icon className="size-3.5" />
        {label}
      </span>
      <span
        className={cn(
          "text-2xl font-semibold tracking-tight tabular-nums leading-none",
          tone === "ok" && "text-success-foreground",
          tone === "bad" && "text-destructive-foreground",
        )}
      >
        {value}
      </span>
      <span className={cn("text-sm text-muted-foreground", tone === "ok" && "text-success-foreground", tone === "bad" && "text-destructive-foreground")}>
        {hint}
      </span>
    </Link>
  );
}

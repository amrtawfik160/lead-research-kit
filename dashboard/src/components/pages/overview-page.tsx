"use client";

import { useEffect, useState } from "react";
import { ArrowUpRight, Calendar, CircleCheck, CircleDashed, ClockAlert, FolderKanban } from "lucide-react";
import Link from "next/link";
import { EmptyState } from "@/components/empty-state";
import { EntityAvatar } from "@/components/entity-avatar";
import { LabelChip } from "@/components/label-chip";
import { PageBody, PageHeader } from "@/components/page-header";
import { Panel, PanelHeader, SegTabs } from "@/components/panel";
import { ProgBar } from "@/components/prog-bar";
import { QueryGate, useQuery } from "@/components/query-state";
import { QuietStatus, PriorityGlyph, StatusGlyph } from "@/components/status-glyph";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { display } from "@/lib/format";
import { greeting, longDate } from "@/lib/gr8r";
import type { LeadRow, Overview } from "@/lib/types";
import { cn } from "@/lib/utils";

export function OverviewPage({ initial = null }: { initial?: Overview | null }): React.ReactElement {
  const { data, error, loading } = useQuery(() => api.overview(), [], initial);
  const [leads, setLeads] = useState<LeadRow[] | null>(null);
  const [tab, setTab] = useState<"upcoming" | "overdue" | "completed">("upcoming");

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

  const lists = {
    upcoming: (leads || []).filter((row) => String(row["Email Risk"] || "").toLowerCase() === "low"),
    overdue: (leads || []).filter((row) => /high|medium/.test(String(row["Email Risk"] || "").toLowerCase())),
    completed: (leads || []).filter((row) => String(row.Source || "").toLowerCase().includes("website")),
  };

  return (
    <>
      <QueryGate loading={loading} error={error} empty={!data} skeleton="cards">
        {data ? (
          <PageBody className="mx-auto w-full max-w-[1160px] px-6 pb-16">
            <PageHeader
              title={`${greeting()}`}
              description={`${longDate()} · Here's what's happening across your workspace.`}
              actions={
                <>
                  <Button size="sm" variant="outline" className="h-[26px] rounded-[6px] text-[12px]" render={<Link href="/contacts" />}>
                    Contacts
                  </Button>
                  <Button size="sm" variant="outline" className="h-[26px] rounded-[6px] text-[12px]" render={<Link href="/quality" />}>
                    Clean list
                  </Button>
                  <Button size="sm" className="h-[26px] rounded-[6px] text-[12px]" render={<Link href="/daily" />}>
                    Open inbox
                  </Button>
                </>
              }
            />
            <div className="mb-4 grid grid-cols-2 overflow-hidden rounded-[10px] border sm:grid-cols-4">
              <Stat
                href="/daily"
                icon={FolderKanban}
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
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
              <div className="flex flex-col gap-4">
                <Panel>
                  <PanelHeader
                    title="My tasks"
                    actions={
                      <>
                        <SegTabs
                          value={tab}
                          onChange={setTab}
                          items={[
                            { value: "upcoming", label: "Upcoming", count: lists.upcoming.length },
                            { value: "overdue", label: "Overdue", count: lists.overdue.length },
                            { value: "completed", label: "Completed", count: lists.completed.length },
                          ]}
                        />
                        <Button size="icon-xs" variant="ghost" render={<Link href="/leads" />} aria-label="Open My Tasks">
                          <ArrowUpRight />
                        </Button>
                      </>
                    }
                  />
                  {lists[tab].length ? (
                    lists[tab].slice(0, 7).map((row, index) => <MiniTask key={`${row.Email}-${index}`} row={row} />)
                  ) : (
                    <EmptyState
                      compact
                      title={tab === "overdue" ? "Nothing overdue" : "No tasks here"}
                      description={
                        tab === "overdue"
                          ? "Every lead in this view is on schedule."
                          : "Deliver a daily file to get things moving."
                      }
                    />
                  )}
                </Panel>
                <Panel>
                  <PanelHeader
                    title="Project progress"
                    actions={
                      <Button size="sm" variant="ghost" className="h-[26px] text-[12px]" render={<Link href="/providers" />}>
                        All providers
                      </Button>
                    }
                  />
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[560px] text-[13px]">
                      <thead>
                        <tr className="border-t text-left text-[12px] font-medium text-muted-foreground">
                          <th className="px-3.5 py-2 font-medium">Provider</th>
                          <th className="py-2 font-medium">Status</th>
                          <th className="py-2 font-medium">Progress</th>
                          <th className="px-3.5 py-2 text-right font-medium">Pools</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.values(data.status.providers)
                          .slice(0, 6)
                          .map((provider) => {
                            const pools = provider.budgets.length;
                            const pct = provider.enabled ? (provider.key_present ? (pools ? 100 : 64) : 30) : 8;
                            return (
                              <tr key={provider.id} className="border-t">
                                <td className="px-3.5 py-2.5">
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
                                    <span className="w-8 text-[11.5px] tabular-nums text-muted-foreground">{pct}%</span>
                                  </span>
                                </td>
                                <td className="px-3.5 py-2.5 text-right font-mono text-[12px] tabular-nums text-muted-foreground">
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
                    title="Upcoming deadlines"
                    actions={
                      <Button size="sm" variant="ghost" className="h-[26px] text-[12px]" render={<Link href="/daily" />}>
                        <Calendar className="size-3.5" />
                        Daily
                      </Button>
                    }
                  />
                  <div className="px-3.5 pb-3">
                    <DeadlineGroup label="This run" count={data.report?.this_run} />
                    <div className="flex h-[30px] items-center gap-2 text-[13px]">
                      <span className="size-2 rounded-[3px] bg-[#3570c4]" />
                      <span className="issue-title min-w-0 flex-1 truncate">{data.latest_daily?.date || "No daily file"}</span>
                      <span className="tabular-nums text-muted-foreground">
                        {delivered}/{target || "—"}
                      </span>
                    </div>
                    <DeadlineGroup label="Advice" count={data.advice.length} />
                    {data.advice.slice(0, 4).map((tip) => (
                      <div key={tip} className="flex min-h-[30px] items-center gap-2 text-[13px]">
                        <span className="size-2 rounded-[3px] bg-[#8f5e0e]" />
                        <span className="min-w-0 flex-1 truncate">{tip}</span>
                      </div>
                    ))}
                  </div>
                </Panel>
                <Panel>
                  <PanelHeader
                    title="Recent activity"
                    actions={
                      <Button size="sm" variant="ghost" className="h-[26px] text-[12px]" render={<Link href="/jobs" />}>
                        View all
                      </Button>
                    }
                  />
                  <div className="px-3.5 pb-3">
                    {data.status.unresolved_attempts.length === 0 ? (
                      <p className="py-2 text-[12.5px] text-muted-foreground">
                        No unresolved attempts. Resume is only for saved GET-pollable jobs.
                      </p>
                    ) : (
                      data.status.unresolved_attempts.map((attempt) => (
                        <div key={attempt.id} className="flex min-h-[38px] items-center gap-2.5 border-t first:border-t-0">
                          <EntityAvatar name={attempt.provider} size={22} />
                          <div className="min-w-0 flex-1 text-[13px]">
                            <span className="font-medium">{attempt.provider}</span>{" "}
                            <span className="text-muted-foreground">{attempt.operation}</span>
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
    </>
  );
}

function DeadlineGroup({ label, count }: { label: string; count?: number }): React.ReactElement {
  return (
    <div className="mb-1 mt-1 flex items-center gap-1.5 text-[11.5px] font-semibold text-[#57544e] dark:text-[#b5b2ab]">
      {label}
      <span className="font-medium text-muted-foreground">{count ?? 0}</span>
    </div>
  );
}

function MiniTask({ row }: { row: LeadRow }): React.ReactElement {
  return (
    <Link
      href="/leads"
      className="flex min-h-[38px] items-center gap-2.5 border-t px-3.5 text-[13.5px] transition-colors hover:bg-[#f6f5f2] dark:hover:bg-[#252524]"
    >
      <StatusGlyph value={row["Email Risk"]} />
      <span className="issue-title min-w-0 flex-1 truncate">{display(row["Full Name"])}</span>
      <LabelChip value={row.Company} className="hidden max-w-[140px] sm:inline-flex" />
      <PriorityGlyph value={row.Tier} />
      <span className="w-14 truncate text-right text-[12px] text-muted-foreground">{display(row.City)}</span>
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
  icon: typeof FolderKanban;
  label: string;
  value: string;
  hint: string;
  tone?: "ok" | "bad";
}): React.ReactElement {
  return (
    <Link
      href={href}
      className="flex flex-col gap-1.5 border-l px-4 py-3.5 first:border-l-0 transition-colors hover:bg-[#f6f5f2] dark:hover:bg-[#252524] sm:first:border-l-0"
    >
      <span className="inline-flex items-center gap-1.5 text-[12.5px] text-[#57544e] dark:text-[#b5b2ab]">
        <Icon className="size-3.5" />
        {label}
      </span>
      <span
        className={cn(
          "text-[22px] font-semibold tracking-[-0.02em] tabular-nums leading-none",
          tone === "ok" && "text-success-foreground",
          tone === "bad" && "text-destructive-foreground",
        )}
      >
        {value}
      </span>
      <span className={cn("text-[12px] text-muted-foreground", tone === "ok" && "text-success-foreground", tone === "bad" && "text-destructive-foreground")}>
        {hint}
      </span>
    </Link>
  );
}

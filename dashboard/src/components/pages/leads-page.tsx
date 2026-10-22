"use client";

import { useMemo, useState } from "react";
import { Pin, X } from "lucide-react";
import { DetailSheet } from "@/components/detail-sheet";
import { FilterSelect } from "@/components/filter-select";
import { DuplicateBanner } from "@/components/duplicate-banner";
import { ExportLeadsButton, LeadOpsPanel } from "@/components/lead-ops-panel";
import { IssueList, type IssueItem } from "@/components/issue-list";
import { PageBody, PageHeader, PageShell, PageToolbar } from "@/components/page-header";
import { QueryGate, useQuery } from "@/components/query-state";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { display, humanName } from "@/lib/format";
import { issueKey } from "@/lib/gr8r";
import { leadFieldSections } from "@/lib/lead-fields";
import { isVerifiedLead, LEAD_STAGES, leadSource, leadStage } from "@/lib/lead-ops";
import type { DailyPayload, LeadRow, SavedFilter } from "@/lib/types";

function toIssue(row: LeadRow, index: number): IssueItem<LeadRow> {
  const stage = leadStage(row);
  const risk = String(row["Email Risk"] || "low");
  const owner = String(row.Owner || "").trim();
  const foundBy = String(row["Found By"] || "").trim();
  return {
    id: issueKey("LEA", index),
    title: display(row["Full Name"]),
    subtitle: owner ? `Owner: ${owner}` : foundBy ? `Found by ${foundBy}` : undefined,
    leadGlyph: stage !== "new" ? stage : risk,
    project: display(row.Company),
    status: humanName(leadSource(row)),
    priority: display(row.Tier || row.Confidence),
    due: display(row.City),
    avatar: display(row.Company),
    group: display(stage),
    done: stage === "disqualified",
    raw: row,
  };
}

function batchLabel(date: string, delivered: number): string {
  if (date === "team") return `Team list · ${delivered}`;
  return `${date} · ${delivered}`;
}

export function LeadsPage({ initial = null }: { initial?: DailyPayload | null }): React.ReactElement {
  const [date, setDate] = useState<string | undefined>(undefined);
  const { data, error, loading, reload } = useQuery(() => api.leads(date), [date], initial);
  const [lead, setLead] = useState<LeadRow | null>(null);
  const [source, setSource] = useState("all");
  const [verified, setVerified] = useState("all");
  const [stage, setStage] = useState("all");
  const [ownerFilter, setOwnerFilter] = useState("all");
  const [area, setArea] = useState("all");
  const [risk, setRisk] = useState("all");
  const [foundBy, setFoundBy] = useState("all");

  const owners = useMemo(() => {
    return [
      ...new Set(
        (data?.leads || [])
          .map((row) => String(row.Owner || "").trim())
          .filter(Boolean),
      ),
    ].sort();
  }, [data]);

  const areas = useMemo(() => {
    return [...new Set((data?.leads || []).map((row) => String(row.Area || "").trim()).filter(Boolean))].sort();
  }, [data]);

  const finders = useMemo(() => {
    return [...new Set((data?.leads || []).map((row) => String(row["Found By"] || "").trim()).filter(Boolean))].sort();
  }, [data]);

  const rows = useMemo(() => {
    return (data?.leads || []).filter((row) => {
      if (source !== "all" && leadSource(row).toLowerCase() !== source) return false;
      if (verified === "verified" && !isVerifiedLead(row)) return false;
      if (verified === "unverified" && isVerifiedLead(row)) return false;
      if (stage !== "all" && leadStage(row) !== stage) return false;
      const rowOwner = String(row.Owner || "").trim();
      if (ownerFilter === "assigned" && !rowOwner) return false;
      if (ownerFilter === "unassigned" && rowOwner) return false;
      if (ownerFilter !== "all" && ownerFilter !== "assigned" && ownerFilter !== "unassigned" && rowOwner !== ownerFilter) {
        return false;
      }
      if (area !== "all" && String(row.Area || "").trim() !== area) return false;
      if (risk !== "all" && String(row["Email Risk"] || "").toLowerCase() !== risk) return false;
      if (foundBy !== "all" && String(row["Found By"] || "").trim() !== foundBy) return false;
      return true;
    });
  }, [area, data, foundBy, ownerFilter, risk, source, stage, verified]);

  const issues = useMemo(() => rows.map((row) => toIssue(row, (data?.leads || []).indexOf(row))), [data, rows]);
  const selected = lead ? issues.find((item) => item.raw === lead || leadOpsMatch(item.raw, lead)) : undefined;
  const sources = useMemo(() => {
    return [...new Set((data?.leads || []).map((row) => leadSource(row).toLowerCase()).filter(Boolean))].sort();
  }, [data]);
  const hasActiveFilters =
    source !== "all" ||
    verified !== "all" ||
    stage !== "all" ||
    ownerFilter !== "all" ||
    area !== "all" ||
    risk !== "all" ||
    foundBy !== "all";

  const pinned = data?.pinned_filters || [];

  function applySaved(filter: SavedFilter) {
    const query = filter.query || {};
    if (query.date) setDate(query.date);
    setStage(query.stage || "all");
    setOwnerFilter(query.owner || "all");
    setArea(query.area || "all");
    setSource(query.source || "all");
    setRisk(query.risk || "all");
    setVerified(query.verified || "all");
    setFoundBy(query.found_by || "all");
  }

  function isActivePin(filter: SavedFilter): boolean {
    const query = filter.query || {};
    const currentDate = date || data?.date || "";
    return (
      (query.date ? query.date === currentDate : true) &&
      (query.stage || "all") === stage &&
      (query.owner || "all") === ownerFilter &&
      (query.area || "all") === area &&
      (query.source || "all") === source &&
      (query.risk || "all") === risk &&
      (query.verified || "all") === verified &&
      (query.found_by || "all") === foundBy
    );
  }

  function clearFilters() {
    setSource("all");
    setVerified("all");
    setStage("all");
    setOwnerFilter("all");
    setArea("all");
    setRisk("all");
    setFoundBy("all");
  }

  return (
    <PageShell>
      <PageHeader
        title="Leads"
        description={`${rows.length} shown · filter by source, verification, date, stage, owner, area, or risk`}
        actions={<ExportLeadsButton rows={rows} filename={`veinore-leads-${data?.date || "export"}.csv`} />}
      />
      <QueryGate
        loading={loading}
        error={error}
        empty={!data}
        emptyTitle="No delivered leads"
        emptyDescription="Run a daily execute, or open the sample workspace in Settings."
      >
        {data ? (
          <PageBody>
            {pinned.length ? (
              <PageToolbar>
                <span className="flex items-center gap-1 text-sm text-muted-foreground">
                  <Pin className="size-3.5" />
                  Pinned
                </span>
                {pinned.map((filter) => (
                  <Button
                    key={filter.name}
                    size="sm"
                    variant={isActivePin(filter) ? "default" : "outline"}
                    aria-pressed={isActivePin(filter)}
                    aria-label={`Apply pinned filter ${filter.name}`}
                    onClick={() => applySaved(filter)}
                  >
                    {filter.name}
                  </Button>
                ))}
              </PageToolbar>
            ) : null}
            <PageToolbar>
              <FilterSelect
                label="Date"
                ariaLabel="Filter leads by date"
                value={date || data.date}
                onChange={setDate}
                options={(data.batches || []).map((batch) => ({
                  value: batch.date,
                  label: batchLabel(batch.date, batch.delivered),
                }))}
              />
              <FilterSelect
                label="Source"
                ariaLabel="Filter leads by source"
                value={source}
                onChange={setSource}
                options={[
                  { value: "all", label: "All sources" },
                  ...sources.map((item) => ({ value: item, label: humanName(item) })),
                ]}
              />
              <FilterSelect
                label="Verified"
                ariaLabel="Filter leads by verification"
                value={verified}
                onChange={setVerified}
                options={[
                  { value: "all", label: "All verification" },
                  { value: "verified", label: "Verified" },
                  { value: "unverified", label: "Unverified" },
                ]}
              />
              <FilterSelect
                label="Stage"
                ariaLabel="Filter leads by stage"
                value={stage}
                onChange={setStage}
                options={[{ value: "all", label: "All stages" }, ...LEAD_STAGES]}
              />
              <FilterSelect
                label="Owner"
                ariaLabel="Filter leads by owner"
                value={ownerFilter}
                onChange={setOwnerFilter}
                options={[
                  { value: "all", label: "All owners" },
                  { value: "assigned", label: "Assigned" },
                  { value: "unassigned", label: "Unassigned" },
                  ...owners.map((item) => ({ value: item, label: item })),
                ]}
              />
              <FilterSelect
                label="Area"
                ariaLabel="Filter leads by area"
                value={area}
                onChange={setArea}
                options={[
                  { value: "all", label: "All areas" },
                  ...areas.map((item) => ({ value: item, label: item })),
                ]}
              />
              <FilterSelect
                label="Risk"
                ariaLabel="Filter leads by email risk"
                value={risk}
                onChange={setRisk}
                options={[
                  { value: "all", label: "All risk" },
                  { value: "low", label: "Low" },
                  { value: "medium", label: "Medium" },
                  { value: "high", label: "High" },
                ]}
              />
              {finders.length ? (
                <FilterSelect
                  label="Found by"
                  ariaLabel="Filter leads by who found them"
                  value={foundBy}
                  onChange={setFoundBy}
                  options={[
                    { value: "all", label: "Anyone" },
                    ...finders.map((item) => ({ value: item, label: item })),
                  ]}
                />
              ) : null}
              {hasActiveFilters ? (
                <Button size="sm" variant="ghost" onClick={clearFilters}>
                  <X className="size-3.5" />
                  Clear filters
                </Button>
              ) : null}
            </PageToolbar>
            <DuplicateBanner items={data.duplicates} className="mt-3 mb-1" />
            <IssueList
              items={issues}
              selectedId={selected?.id}
              onSelect={(item) => setLead(item?.raw ?? null)}
              emptyTitle="No leads match"
              emptyDescription="Clear source, verification, date, stage, or pinned filters."
            />
            <DetailSheet
              open={Boolean(lead)}
              onOpenChange={(open) => !open && setLead(null)}
              title={display(lead?.["Full Name"])}
              description={`${display(lead?.Title)} · ${display(lead?.Company)}`}
              issueId={selected?.id}
              sections={leadFieldSections(lead)}
              extraFirst
              extra={
                <LeadOpsPanel
                  row={lead}
                  onSaved={(next) => {
                    setLead(next);
                    reload();
                  }}
                />
              }
            />
          </PageBody>
        ) : null}
      </QueryGate>
    </PageShell>
  );
}

function leadOpsMatch(a: LeadRow, b: LeadRow): boolean {
  return String(a.Email || "") === String(b.Email || "") && String(a["Full Name"] || "") === String(b["Full Name"] || "");
}

export type Meta = {
  kit_version: string;
  workspace: string;
  sample: boolean;
  initialized: boolean;
  today: string;
};

export type Budget = {
  provider: string;
  pool: string;
  remaining: string;
  remaining_units: number;
  confirmed_at: number;
  source: string;
};

export type ProviderRow = {
  id: string;
  enabled: boolean;
  operations: string[];
  auth_env: string[];
  key_present: boolean;
  paused: boolean;
  pause_reason?: string | null;
  charges: Record<string, Record<string, string>>;
  budgets: Budget[];
  last_request_at?: number | null;
};

export type Status = {
  initialized: boolean;
  contacts: number;
  held: number;
  qualified?: number;
  with_published_email?: number;
  budgets: Budget[];
  providers: Record<string, ProviderRow>;
  unresolved_attempts: Array<{
    id: string;
    provider: string;
    state: string;
    operation: string;
  }>;
  audit_ok: boolean;
};

export type HistoryStats = {
  delivered_keys?: number;
  batches?: number;
  domains_known?: number;
  domains_uncrawled?: number;
  sites_stored?: number;
  prospects_stored?: number;
  suppressed?: number;
  outcomes?: number;
  firms_pending_website?: number;
  firms_resolved?: number;
  patterns_distrusted?: number;
  results_rows_imported?: number;
};

export type DailyBatch = {
  date: string;
  target: number;
  delivered: number;
  execute: boolean;
  shortfall?: number;
  has_audit: boolean;
  has_leads: boolean;
};

export type DailyReport = {
  date?: string;
  execute?: boolean;
  target?: number;
  delivered?: number;
  this_run?: number;
  prior_delivered?: number;
  shortfall?: number;
  fresh_available?: number;
  delivered_by_source?: Record<string, number>;
  delivered_by_risk?: Record<string, number>;
  delivered_by_company_type?: Record<string, number>;
  advice?: string[];
  budgets_left?: Record<string, string>;
  steps?: Record<string, Record<string, unknown>>;
  files?: Record<string, string>;
};

export type LeadStage = "new" | "contacted" | "replied" | "qualified" | "disqualified";

export type OverviewChartPoint = { date: string; delivered: number; target: number };
export type SourceSlice = { name: string; count: number };
export type QuotaRow = {
  id: string;
  pool: string;
  remaining?: string;
  remaining_units: number;
  spent: string;
  spent_units: number;
  quota: string;
  quota_units: number;
};
export type SpentRow = {
  provider: string;
  pool: string;
  spent: string;
  spent_units: number;
};
export type OverviewCharts = {
  leads_per_day: OverviewChartPoint[];
  verification: { verified: number; unverified: number; rate: number };
  provider_quota: QuotaRow[];
  sources: SourceSlice[];
  budget_spent: SpentRow[];
};
export type DuplicateWarning = {
  kind: "within_file" | "across_batches" | "history";
  email: string;
  name?: string;
  also_in?: string;
};

export type Overview = {
  meta: Meta;
  status: Status;
  history: HistoryStats;
  latest_daily: DailyBatch | null;
  report: DailyReport | null;
  research_queue_count: number;
  advice: string[];
  charts?: OverviewCharts;
  duplicates?: DuplicateWarning[];
};

export type Contact = {
  id: string;
  name?: string;
  company?: string;
  title?: string;
  profile_url?: string;
  source_channel?: string;
  source_url?: string;
  qualified?: boolean;
  held?: boolean;
  hold_reason?: string;
  hold_source?: string;
  email?: string;
  email_provenance?: string;
  company_inbox?: string;
  affiliation_review?: boolean;
  research_complete?: boolean;
  synthetic?: boolean;
  evidence_count?: number;
  candidate_count?: number;
  in_research_queue?: boolean;
};

export type ContactDetail = {
  contact: Contact;
  evidence: Array<Record<string, unknown>>;
  candidates: Array<Record<string, unknown>>;
  assessments: Record<string, Record<string, unknown>>;
};

export type LeadRow = Record<string, string | number | null | undefined>;

export type DailyPayload = {
  date: string;
  report: DailyReport;
  markdown: string;
  audit: {
    date?: string;
    sampled?: number;
    score?: { confirmed_pct: number; partial_pct: number; failed_pct: number };
    top_reasons?: Record<string, number>;
    leads?: Array<Record<string, unknown>>;
  } | null;
  leads: LeadRow[];
  batches: DailyBatch[];
  duplicates?: DuplicateWarning[];
};

export type LeadOpsRecord = {
  stage?: LeadStage;
  notes?: string;
  owner?: string;
  updated_at?: number;
};

export type BudgetView = {
  budgets: Array<
    Budget & {
      spent?: string;
      spent_units?: number;
      quota?: string;
      quota_units?: number;
    }
  >;
  refills: Array<Record<string, unknown>>;
  providers: ProviderRow[];
  spent: SpentRow[];
};

export type Attempt = {
  id: string;
  provider: string;
  operation: string;
  state: string;
  created_at?: number;
  sent_at?: number | null;
  finished_at?: number;
  email?: string;
  lead_id?: string;
  assessment?: string;
  risk_flags: string[];
  error?: string | null;
  charges: Record<string, string>;
};

export type CleanResult = {
  summary: {
    input_rows: number;
    duplicates_merged: number;
    unique_leads: number;
    clean: number;
    review: number;
    rejected: number;
    with_work_email: number;
    with_linkedin: number;
    with_website: number;
    by_tier: Record<string, number>;
    top_issues: Record<string, number>;
    reject_reasons: Record<string, number>;
    min_tier: string;
  };
  clean: Array<Record<string, unknown>>;
  review: Array<Record<string, unknown>>;
  rejected: Array<Record<string, unknown>>;
};

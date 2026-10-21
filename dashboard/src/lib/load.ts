import type {
  Attempt,
  BudgetView,
  Contact,
  DailyPayload,
  Meta,
  Overview,
  ProviderRow,
} from "@/lib/types";

export type SettingsPayload = Meta & {
  settings: { sample?: boolean; workspace?: string };
  env_locked?: boolean;
  settings_error?: string | null;
};
export type ContactsPayload = { contacts: Contact[] };
export type ResearchPayload = {
  queue: Contact[];
  evidence: Array<Record<string, unknown>>;
  candidates: Array<Record<string, unknown>>;
};
export type CompaniesPayload = {
  domains: Array<Record<string, unknown>>;
  firms: Array<Record<string, unknown>>;
  prospects: Array<Record<string, unknown>>;
  sites: Array<Record<string, unknown>>;
};
export type ProvidersPayload = {
  providers: ProviderRow[];
  refills: Array<Record<string, unknown>>;
  budgets: Array<Record<string, unknown>>;
};
export type JobsPayload = { attempts: Attempt[] };
export type BudgetPayload = BudgetView;
export type HistoryPayload = {
  stats: Record<string, number>;
  delivered: Array<Record<string, unknown>>;
  suppressed: Array<Record<string, unknown>>;
  outcomes: Array<Record<string, unknown>>;
  patterns: Record<string, Record<string, unknown>>;
  source_quality: Record<string, Record<string, number>>;
  refills: Array<Record<string, unknown>>;
};

const ORIGIN = process.env.DASHBOARD_API_ORIGIN || "http://127.0.0.1:8787";

export async function loadApi<T>(path: string): Promise<T | null> {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await fetch(`${ORIGIN}${path}`, { cache: "no-store" });
      if (response.ok) return (await response.json()) as T;
      if (![502, 503, 504].includes(response.status) || attempt === 2) return null;
    } catch {
      if (attempt === 2) return null;
    }
    await new Promise((resolve) => setTimeout(resolve, 200 * (attempt + 1)));
  }
  return null;
}

export async function loadDaily(date?: string): Promise<DailyPayload | null> {
  const path = date ? `/api/daily/${encodeURIComponent(date)}` : "/api/daily";
  const payload = await loadApi<DailyPayload | { today: DailyPayload }>(path);
  if (!payload) return null;
  if ("today" in payload && payload.today) return payload.today;
  return payload as DailyPayload;
}

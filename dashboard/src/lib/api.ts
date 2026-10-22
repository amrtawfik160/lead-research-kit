import type { SettingsPayload } from "@/lib/load";
import type {
  Attempt,
  BudgetView,
  CleanResult,
  Contact,
  ContactDetail,
  DailyPayload,
  LeadOpsRecord,
  Overview,
  ProviderRow,
  SavedFilter,
  TeamView,
} from "@/lib/types";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

async function requestOnce<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  let data: T & { error?: string };
  try {
    data = (await response.json()) as T & { error?: string };
  } catch {
    throw new Error(response.ok ? "invalid_response" : "api_unreachable");
  }
  if (!response.ok || (data && typeof data === "object" && "error" in data && data.error)) {
    throw new Error(
      (data && typeof data === "object" && data.error) || `request_failed_${response.status}`,
    );
  }
  return data;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method || "GET").toUpperCase();
  const tries = method === "GET" ? 3 : 1;
  let last: Error | null = null;
  for (let attempt = 0; attempt < tries; attempt += 1) {
    try {
      return await requestOnce<T>(path, init);
    } catch (err) {
      last = err instanceof Error ? err : new Error("request_failed");
      const transient =
        last.message === "api_unreachable" || /request_failed_50[234]/.test(last.message);
      if (method !== "GET" || !transient || attempt === tries - 1) {
        throw last;
      }
      await sleep(200 * (attempt + 1));
    }
  }
  throw last || new Error("request_failed");
}

export const api = {
  overview: () => request<Overview>("/api/overview"),
  settings: () => request<SettingsPayload>("/api/settings"),
  saveSettings: (body: { sample?: boolean; workspace?: string }) =>
    request<SettingsPayload>("/api/settings", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  resetSample: () => request<Overview>("/api/sample/reset", { method: "POST", body: "{}" }),
  contacts: () => request<{ contacts: Contact[] }>("/api/contacts"),
  contact: (id: string) => request<ContactDetail>(`/api/contacts/${encodeURIComponent(id)}`),
  hold: (id: string, reason: string, source: string) =>
    request<ContactDetail>(`/api/contacts/${encodeURIComponent(id)}/hold`, {
      method: "POST",
      body: JSON.stringify({ reason, source }),
    }),
  research: () =>
    request<{
      queue: Contact[];
      evidence: Array<Record<string, unknown>>;
      candidates: Array<Record<string, unknown>>;
    }>("/api/research"),
  daily: (date?: string) =>
    request<DailyPayload>(date ? `/api/daily/${encodeURIComponent(date)}` : "/api/daily").then(
      (payload) => ("today" in payload ? (payload as { today: DailyPayload }).today : payload),
    ),
  leads: (date?: string) =>
    request<DailyPayload>(date ? `/api/leads?date=${encodeURIComponent(date)}` : "/api/leads"),
  companies: () =>
    request<{
      domains: Array<Record<string, unknown>>;
      firms: Array<Record<string, unknown>>;
      prospects: Array<Record<string, unknown>>;
      sites: Array<Record<string, unknown>>;
    }>("/api/companies"),
  providers: () =>
    request<{
      providers: ProviderRow[];
      refills: Array<Record<string, unknown>>;
      budgets: Array<Record<string, unknown>>;
    }>("/api/providers"),
  setEnabled: (id: string, enabled: boolean) =>
    request<{ providers: ProviderRow[] }>(`/api/providers/${encodeURIComponent(id)}/enabled`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  jobs: () => request<{ attempts: Attempt[] }>("/api/jobs"),
  history: () =>
    request<{
      stats: Record<string, number>;
      delivered: Array<Record<string, unknown>>;
      suppressed: Array<Record<string, unknown>>;
      outcomes: Array<Record<string, unknown>>;
      patterns: Record<string, Record<string, unknown>>;
      source_quality: Record<string, Record<string, number>>;
      refills: Array<Record<string, unknown>>;
    }>("/api/history"),
  clean: (body: {
    csv?: string;
    rows?: Array<Record<string, unknown>>;
    min_tier?: string;
    allow_personal_email?: boolean;
    keep_non_decision_makers?: boolean;
  }) => request<CleanResult>("/api/clean", { method: "POST", body: JSON.stringify(body) }),
  previewDaily: (target: number, sources?: string[]) =>
    request<Record<string, unknown>>("/api/daily/preview", {
      method: "POST",
      body: JSON.stringify({ target, sources }),
    }),
  runDaily: (target: number, execute = false, sources?: string[]) =>
    request<Record<string, unknown>>("/api/daily/run", {
      method: "POST",
      body: JSON.stringify({ target, execute, sources }),
    }),
  ops: () => request<{ ops: Record<string, LeadOpsRecord>; stages: string[] }>("/api/ops"),
  saveOps: (body: { key: string; stage?: string; notes?: string; owner?: string }) =>
    request<{ key: string; ops: LeadOpsRecord }>("/api/ops", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  budget: () => request<BudgetView>("/api/budget"),
  team: () => request<TeamView>("/api/team"),
  filters: () => request<{ filters: SavedFilter[]; pinned: SavedFilter[]; count: number }>("/api/filters"),
};

export function formatTime(value?: number | null): string {
  if (!value) return "—";
  const date = new Date(value * (value < 1e12 ? 1000 : 1));
  if (Number.isNaN(date.getTime())) return "—";
  const iso = date.toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
}

export function text(value: unknown): string {
  if (value == null || value === "") return "";
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function display(value: unknown): string {
  return text(value) || "—";
}

const ERROR_TEXT: Record<string, string> = {
  workspace_not_found: "That folder does not exist. Check the path in Settings.",
  workspace_required: "Enter a workspace folder, or leave sample data on.",
  invalid_workspace_path: "Use a local folder path without '..'.",
  run_init_first: "That folder has no providers.json. Initialize it with lead-research init.",
  api_unreachable: "The dashboard API is not running.",
  invalid_response: "The dashboard API returned a response the UI could not read.",
  contact_not_found: "That contact is not in this workspace.",
  unknown_provider: "That provider is not in the kit.",
  provider_paused: "That provider is paused. Review it on the CLI before sending more work.",
  quota: "That provider returned a quota or credit error. Leave it paused.",
  auth: "That provider rejected the API key. Check the environment variable, not the UI.",
  sec_access_denied:
    "SEC EDGAR refused this host. Confirm SEC_USER_AGENT is 'Name you@domain' and that this IP is allowed.",
  missing_auth: "That provider is enabled but its environment variable is empty.",
};

export function explainError(raw: string): string {
  const text = raw.trim();
  if (ERROR_TEXT[text]) return ERROR_TEXT[text];
  if (/failed to fetch|networkerror|econnrefused|api_unreachable/i.test(text)) {
    return ERROR_TEXT.api_unreachable;
  }
  return text || "request_failed";
}

export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

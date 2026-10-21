/** Helpers only. Visual tokens live in the official coss theme. */

export const LABEL_COLORS = [
  "#3570c4",
  "#7652c2",
  "#1e8580",
  "#bc4270",
  "#c0612b",
  "#8f5e0e",
  "#25754b",
  "#4b5bd6",
] as const;

export function issueKey(prefix: string, index: number): string {
  return `${prefix}-${String(index + 1).padStart(2, "0")}`;
}

export function labelColor(value: string): string {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
  return LABEL_COLORS[hash % LABEL_COLORS.length];
}

export function initials(value: string): string {
  const parts = value
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2);
  if (!parts.length) return "V";
  return parts.map((part) => part[0]?.toUpperCase() || "").join("");
}

export function greeting(now = new Date()): string {
  const hour = now.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

export function longDate(now = new Date()): string {
  return now.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
}

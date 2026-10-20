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

export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

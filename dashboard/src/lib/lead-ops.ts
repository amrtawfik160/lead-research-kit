import type { DuplicateWarning, LeadRow, LeadStage } from "@/lib/types";

export const LEAD_STAGES: Array<{ value: LeadStage; label: string }> = [
  { value: "new", label: "New" },
  { value: "contacted", label: "Contacted" },
  { value: "replied", label: "Replied" },
  { value: "qualified", label: "Qualified" },
  { value: "disqualified", label: "Disqualified" },
];

export function leadEmail(row: LeadRow | null | undefined): string {
  return String(row?.Email || row?.email || "").trim();
}

export function leadOpsKey(row: LeadRow | null | undefined): string {
  if (!row) return "";
  if (row.ops_key) return String(row.ops_key);
  const email = leadEmail(row).toLowerCase();
  if (email) return email;
  const name = String(row["Full Name"] || row.full_name || row.name || "")
    .trim()
    .toLowerCase();
  const company = String(row.Company || row.company || "")
    .trim()
    .toLowerCase();
  return `${name}|${company}`;
}

export function leadStage(row: LeadRow | null | undefined): LeadStage {
  const value = String(row?.Stage || "new").toLowerCase();
  return LEAD_STAGES.some((item) => item.value === value) ? (value as LeadStage) : "new";
}

export function isVerifiedLead(row: LeadRow): boolean {
  const verification = String(row["Email Verification"] || row.email_verification || "").toLowerCase();
  if (["valid", "verified", "provider_valid"].includes(verification)) return true;
  return String(row["Email Risk"] || row.email_risk || "").toLowerCase() === "low";
}

export function leadSource(row: LeadRow): string {
  return String(row.Source || row["Email Source"] || row.email_source || "");
}

export function duplicateSummary(items: DuplicateWarning[] | undefined): string {
  if (!items?.length) return "";
  const first = items[0];
  const extra = items.length > 1 ? ` and ${items.length - 1} more` : "";
  if (first.kind === "within_file") return `${first.email} appears twice in this file${extra}`;
  if (first.kind === "across_batches") return `${first.email} also appears in ${first.also_in}${extra}`;
  return `${first.email} was already delivered${first.also_in ? ` on ${first.also_in}` : ""}${extra}`;
}

const CSV_HEADERS = [
  "Full Name",
  "Title",
  "Company",
  "Company Website",
  "LinkedIn URL",
  "Email",
  "Source",
  "Email Risk",
  "Email Verification",
  "City",
  "State",
  "Stage",
  "Owner",
  "Notes",
  "Found By",
  "Area",
];

function csvCell(value: unknown): string {
  const text = value == null ? "" : String(value);
  if (/[",\n]/.test(text)) return `"${text.replace(/"/g, '""')}"`;
  return text;
}

export function leadsToCsv(rows: LeadRow[]): string {
  const lines = [CSV_HEADERS.join(",")];
  for (const row of rows) {
    lines.push(
      CSV_HEADERS.map((header) => {
        if (header === "Source") return csvCell(leadSource(row));
        return csvCell(row[header]);
      }).join(","),
    );
  }
  return `${lines.join("\n")}\n`;
}

export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export async function copyText(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    return false;
  }
}

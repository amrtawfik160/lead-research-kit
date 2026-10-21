import { display } from "@/lib/format";
import type { LeadRow } from "@/lib/types";

const GROUPS: Array<{ title: string; keys: string[] }> = [
  {
    title: "Identity",
    keys: [
      "Full Name",
      "Title",
      "Company",
      "Company Website",
      "LinkedIn URL",
      "City",
      "State",
      "Company Type",
    ],
  },
  {
    title: "Email",
    keys: [
      "Email",
      "Email Source",
      "Source",
      "Email Risk",
      "Email Risk Reason",
      "Email Verification",
      "MX Provider",
    ],
  },
  {
    title: "Quality",
    keys: ["Confidence", "Tier", "Score", "Issues"],
  },
  {
    title: "Evidence",
    keys: ["Source URL", "email_evidence_url"],
  },
];

export type FieldSection = {
  title: string;
  fields: Array<{ label: string; value: string }>;
};

function present(value: unknown): boolean {
  return value != null && value !== "";
}

export function leadFieldSections(lead: LeadRow | null): FieldSection[] {
  if (!lead) return [];
  const used = new Set<string>();
  const sections: FieldSection[] = [];
  for (const group of GROUPS) {
    const fields = group.keys
      .filter((key) => present(lead[key]))
      .map((key) => {
        used.add(key);
        return { label: key, value: display(lead[key]) };
      });
    if (fields.length) sections.push({ title: group.title, fields });
  }
  const rest = Object.entries(lead)
    .filter(([key, value]) => !used.has(key) && present(value))
    .map(([label, value]) => ({ label, value: display(value) }));
  if (rest.length) sections.push({ title: "More", fields: rest });
  return sections;
}

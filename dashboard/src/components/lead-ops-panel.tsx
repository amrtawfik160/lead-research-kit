"use client";

import { Copy, Download } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { toastManager } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { explainError } from "@/lib/format";
import { copyText, downloadCsv, LEAD_STAGES, leadEmail, leadOpsKey, leadsToCsv, leadStage } from "@/lib/lead-ops";
import type { LeadRow, LeadStage } from "@/lib/types";

export function LeadOpsPanel({
  row,
  onSaved,
}: {
  row: LeadRow | null;
  onSaved?: (next: LeadRow) => void;
}): React.ReactElement | null {
  const [stage, setStage] = useState<LeadStage>("new");
  const [owner, setOwner] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setStage(leadStage(row));
    setOwner(String(row?.Owner || ""));
    setNotes(String(row?.Notes || ""));
  }, [row]);

  if (!row) return null;
  const email = leadEmail(row);
  const key = leadOpsKey(row);

  async function save(patch: { stage?: LeadStage; notes?: string; owner?: string }) {
    if (!key) return;
    setBusy(true);
    try {
      const saved = await api.saveOps({ key, ...patch });
      onSaved?.({
        ...row,
        ops_key: key,
        Stage: saved.ops.stage || patch.stage || stage,
        Notes: saved.ops.notes ?? (patch.notes ?? notes),
        Owner: saved.ops.owner ?? (patch.owner ?? owner),
      });
    } catch (error) {
      toastManager.add({
        type: "error",
        title: "Lead notes were not saved",
        description: explainError(error instanceof Error ? error.message : "request_failed"),
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-3">
      <h3 className="text-[13.5px] font-semibold">Outreach</h3>
      <div className="grid grid-cols-[112px_minmax(0,1fr)] items-center gap-x-0 gap-y-1.5 text-[12.5px]">
        <span className="text-[#57544e] dark:text-[#b5b2ab]">Stage</span>
        <Select
          items={LEAD_STAGES}
          value={stage}
          disabled={busy}
          onValueChange={(value) => {
            const next = String(value) as LeadStage;
            setStage(next);
            void save({ stage: next });
          }}
        >
          <SelectTrigger size="sm" aria-label="Lead stage" className="w-full min-w-0">
            <SelectValue />
          </SelectTrigger>
          <SelectPopup>
            {LEAD_STAGES.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectPopup>
        </Select>
        <span className="text-[#57544e] dark:text-[#b5b2ab]">Owner</span>
        <Input
          size="sm"
          value={owner}
          disabled={busy}
          placeholder="Who owns this lead"
          aria-label="Lead owner"
          onChange={(event) => setOwner(event.target.value)}
          onBlur={() => void save({ owner })}
        />
      </div>
      <Field>
        <FieldLabel>Notes</FieldLabel>
        <Textarea
          size="sm"
          value={notes}
          disabled={busy}
          rows={3}
          placeholder="Local notes for this lead. Not written to leads.csv."
          aria-label="Lead notes"
          onChange={(event) => setNotes(event.target.value)}
          onBlur={() => void save({ notes })}
        />
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={!email}
          onClick={() => {
            void copyText(email).then((ok) => {
              toastManager.add({
                type: ok ? "success" : "error",
                title: ok ? "Email copied" : "Could not copy email",
                description: email,
              });
            });
          }}
        >
          <Copy className="size-3.5" />
          Copy email
        </Button>
      </div>
    </div>
  );
}

export function ExportLeadsButton({
  rows,
  filename,
}: {
  rows: LeadRow[];
  filename: string;
}): React.ReactElement {
  return (
    <Button
      size="sm"
      variant="outline"
      disabled={!rows.length}
      onClick={() => {
        downloadCsv(filename, leadsToCsv(rows));
        toastManager.add({ type: "success", title: "CSV exported", description: `${rows.length} leads` });
      }}
    >
      <Download className="size-3.5" />
      Export CSV
    </Button>
  );
}

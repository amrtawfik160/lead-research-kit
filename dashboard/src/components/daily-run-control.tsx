"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toastManager } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { explainError } from "@/lib/format";

export function DailyRunControl({
  onDone,
}: {
  onDone?: () => void;
}): React.ReactElement {
  const router = useRouter();
  const [target, setTarget] = useState("20");
  const [busy, setBusy] = useState(false);

  async function run(execute: boolean) {
    const count = Number(target);
    if (!Number.isFinite(count) || count < 1) {
      toastManager.add({ type: "error", title: "Enter a target between 1 and 200" });
      return;
    }
    if (
      execute &&
      !window.confirm(
        "Execute a local daily run from stored sites only? This writes today's delivery folder. No live provider calls.",
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      const report = await api.runDaily(Math.max(1, Math.min(200, Math.round(count))), execute);
      const delivered = Number(report.delivered ?? 0);
      const shortfall = Number(report.shortfall ?? 0);
      toastManager.add({
        type: "success",
        title: execute ? "Daily run written" : "Daily preview ready",
        description: `${delivered} leads${shortfall ? ` · ${shortfall} short` : ""}`,
      });
      onDone?.();
      router.push("/daily");
      router.refresh();
    } catch (error) {
      toastManager.add({
        type: "error",
        title: execute ? "Daily run did not execute" : "Daily preview failed",
        description: explainError(error instanceof Error ? error.message : "request_failed"),
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Input
        size="sm"
        inputMode="numeric"
        value={target}
        disabled={busy}
        onChange={(event) => setTarget(event.target.value.replace(/[^\d]/g, "").slice(0, 3))}
        aria-label="Daily lead target"
        className="h-[26px] w-14 px-2 text-center tabular-nums"
      />
      <Button size="sm" className="h-[26px] rounded-[6px] text-[12px]" disabled={busy} onClick={() => void run(false)}>
        {busy ? "Running…" : "Preview daily"}
      </Button>
      <Button
        size="sm"
        variant="outline"
        className="h-[26px] rounded-[6px] text-[12px]"
        disabled={busy}
        onClick={() => void run(true)}
      >
        Run locally
      </Button>
    </div>
  );
}

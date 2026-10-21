"use client";

import { Users } from "lucide-react";
import { useEffect, useState } from "react";
import { EmptyState } from "@/components/empty-state";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { explainError } from "@/lib/format";

export function useQuery<T>(
  loader: () => Promise<T>,
  deps: unknown[] = [],
  initial: T | null = null,
): {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
} {
  const [data, setData] = useState<T | null>(initial);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(initial == null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    if (data == null) setLoading(true);
    loader()
      .then((value) => {
        if (!cancelled) {
          setData(value);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "request_failed");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps]);

  return { data, error, loading, reload: () => setTick((n) => n + 1) };
}

function Sk({ className }: { className: string }): React.ReactElement {
  return <span className={`sk ${className}`} />;
}

function TableSkeleton(): React.ReactElement {
  return (
    <div className="flex flex-col gap-0 px-6 py-4" aria-hidden>
      <div className="mb-3 flex items-center gap-2">
        <Sk className="h-[26px] w-48" />
        <Sk className="h-[26px] w-16" />
        <Sk className="h-[26px] w-16" />
      </div>
      <Sk className="mb-2 h-8 w-full" />
      {Array.from({ length: 8 }, (_, index) => (
        <div key={index} className="flex h-10 items-center gap-3 border-b border-[#f0eeea] dark:border-[#252523]">
          <Sk className="h-3.5 w-3.5 rounded-full" />
          <Sk className="h-3 w-12" />
          <Sk className="h-3.5 w-[38%]" />
          <span className="flex-1" />
          <Sk className="h-3 w-20" />
          <Sk className="h-3 w-16" />
        </div>
      ))}
    </div>
  );
}

function CardsSkeleton(): React.ReactElement {
  return (
    <div className="mx-auto flex w-full max-w-[1160px] flex-col gap-4 px-6 pt-6" aria-hidden>
      <div className="flex items-end justify-between">
        <div className="flex flex-col gap-2">
          <Sk className="h-7 w-56" />
          <Sk className="h-3.5 w-80" />
        </div>
        <div className="flex gap-2">
          <Sk className="h-[26px] w-20" />
          <Sk className="h-[26px] w-24" />
        </div>
      </div>
      <div className="grid grid-cols-4 overflow-hidden rounded-[10px] border">
        {Array.from({ length: 4 }, (_, index) => (
          <div key={index} className="flex flex-col gap-2 border-l px-4 py-3.5 first:border-l-0">
            <Sk className="h-3 w-24" />
            <Sk className="h-6 w-12" />
            <Sk className="h-3 w-28" />
          </div>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
        <Sk className="h-64 w-full rounded-[10px]" />
        <Sk className="h-64 w-full rounded-[10px]" />
      </div>
    </div>
  );
}

export function QueryGate({
  loading,
  error,
  empty,
  emptyTitle = "No leads yet",
  emptyDescription = "Point the dashboard at a workspace or keep sample mode on.",
  skeleton = "table",
  children,
}: {
  loading: boolean;
  error: string | null;
  empty?: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
  skeleton?: "table" | "cards" | "form";
  children: React.ReactNode;
}): React.ReactElement {
  if (loading) {
    if (skeleton === "cards") return <CardsSkeleton />;
    if (skeleton === "form") {
      return (
        <div className="flex max-w-xl flex-col gap-3 px-6 pt-4" aria-hidden>
          <Sk className="h-16 w-full" />
          <Sk className="h-24 w-full" />
          <Sk className="h-10 w-48" />
        </div>
      );
    }
    return <TableSkeleton />;
  }
  if (error) {
    return (
      <div className="p-6">
        <Alert variant="error">
          <AlertTitle>Could not read the workspace</AlertTitle>
          <AlertDescription>{explainError(error)}</AlertDescription>
        </Alert>
        <Button size="sm" variant="outline" className="mt-3" onClick={() => window.location.reload()}>
          Retry
        </Button>
      </div>
    );
  }
  if (empty) {
    return <EmptyState icon={Users} title={emptyTitle} description={emptyDescription} />;
  }
  return <>{children}</>;
}

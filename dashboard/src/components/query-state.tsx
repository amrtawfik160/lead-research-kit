"use client";

import { useEffect, useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";

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

export function QueryGate({
  loading,
  error,
  empty,
  emptyTitle = "Nothing here yet",
  emptyDescription = "Point the dashboard at a workspace or keep sample mode on.",
  children,
}: {
  loading: boolean;
  error: string | null;
  empty?: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
  children: React.ReactNode;
}): React.ReactElement {
  if (loading) {
    return (
      <div className="flex flex-col gap-3 p-6">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Spinner />
          Loading workspace
        </div>
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="p-6">
        <Alert variant="error">
          <AlertTitle>Could not read the workspace</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      </div>
    );
  }
  if (empty) {
    return (
      <Empty>
        <EmptyHeader>
          <EmptyTitle>{emptyTitle}</EmptyTitle>
          <EmptyDescription>{emptyDescription}</EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }
  return <>{children}</>;
}

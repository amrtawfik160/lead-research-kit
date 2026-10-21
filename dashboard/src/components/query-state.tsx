"use client";

import { Inbox } from "lucide-react";
import { useEffect, useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
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

function TableSkeleton(): React.ReactElement {
  return (
    <div className="flex flex-col gap-3 p-4 sm:p-6">
      <div className="flex items-center justify-between gap-3">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-8 w-36" />
      </div>
      <div className="overflow-hidden rounded-lg border">
        <Skeleton className="h-8 w-full rounded-none" />
        {Array.from({ length: 8 }, (_, index) => (
          <Skeleton key={index} className="mt-px h-9 w-full rounded-none opacity-80" />
        ))}
      </div>
    </div>
  );
}

function CardsSkeleton(): React.ReactElement {
  return (
    <div className="flex flex-col gap-4 p-4 sm:p-6">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, index) => (
          <Skeleton key={index} className="h-[5.5rem] w-full rounded-xl" />
        ))}
      </div>
      <Skeleton className="h-36 w-full rounded-xl" />
      <div className="grid gap-3 lg:grid-cols-2">
        <Skeleton className="h-40 w-full rounded-xl" />
        <Skeleton className="h-40 w-full rounded-xl" />
      </div>
    </div>
  );
}

export function QueryGate({
  loading,
  error,
  empty,
  emptyTitle = "Nothing here yet",
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
        <div className="flex max-w-xl flex-col gap-3 p-4 sm:p-6">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-10 w-48" />
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
    return (
      <Empty className="py-16">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <Inbox />
          </EmptyMedia>
          <EmptyTitle className="text-base font-medium">{emptyTitle}</EmptyTitle>
          <EmptyDescription>{emptyDescription}</EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }
  return <>{children}</>;
}

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function PageShell({
  children,
  width = "full",
}: {
  children: ReactNode;
  width?: "full" | "wide" | "narrow";
}): React.ReactElement {
  return (
    <div
      data-slot="page-shell"
      className={cn(
        "page-enter flex min-h-0 min-w-0 flex-1 flex-col px-(--page-gutter)",
        width === "wide" && "w-full max-w-(--page-max-wide)",
        width === "narrow" && "w-full max-w-(--page-max-narrow)",
      )}
    >
      <div data-slot="page-content" className="flex min-h-0 min-w-0 flex-1 flex-col">
        {children}
      </div>
    </div>
  );
}

export function PageHeader({
  title,
  description,
  crumbs: _crumbs,
  actions,
}: {
  title: string;
  description?: string;
  crumbs?: Array<{ href?: string; label: string }>;
  actions?: ReactNode;
}): React.ReactElement {
  return (
    <header data-slot="page-header" className="flex flex-wrap items-start gap-3 pt-6 pb-4">
      <div className="min-w-0">
        <h1 className="font-heading text-2xl font-semibold tracking-tight text-balance">{title}</h1>
        {description ? (
          <p className="mt-1.5 max-w-2xl text-sm leading-6 text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="ms-auto flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}

export function PageToolbar({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}): React.ReactElement {
  return (
    <div data-slot="page-toolbar" className={cn("flex flex-wrap items-center gap-2 border-b py-3", className)}>
      {children}
    </div>
  );
}

export function PageBody({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}): React.ReactElement {
  return (
    <div data-slot="page-body" className={cn("flex min-h-0 min-w-0 flex-1 flex-col pb-6", className)}>
      {children}
    </div>
  );
}

export function PageSplit({
  left,
  right,
}: {
  left: ReactNode;
  right: ReactNode;
}): React.ReactElement {
  return (
    <div
      data-slot="page-split"
      className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[var(--page-pane)_minmax(0,1fr)]"
    >
      <section data-slot="page-pane" className="flex min-h-0 min-w-0 flex-col border-r">
        {left}
      </section>
      <section data-slot="page-pane" className="flex min-h-0 min-w-0 flex-col ps-(--page-gutter)">
        {right}
      </section>
    </div>
  );
}

import type { ReactNode } from "react";
import Link from "next/link";
import { cn } from "@/lib/utils";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";

export function PageHeader({
  title,
  description,
  crumbs,
  actions,
}: {
  title: string;
  description?: string;
  crumbs?: Array<{ href?: string; label: string }>;
  actions?: ReactNode;
}): React.ReactElement {
  return (
    <div className="flex flex-col gap-2 border-b px-4 py-3 sm:px-6">
      {crumbs && crumbs.length > 0 ? (
        <Breadcrumb>
          <BreadcrumbList className="text-[11px] text-muted-foreground">
            {crumbs.map((crumb, index) => (
              <span key={`${crumb.label}-${index}`} className="contents">
                {index > 0 ? <BreadcrumbSeparator /> : null}
                <BreadcrumbItem>
                  {crumb.href ? (
                    <BreadcrumbLink render={<Link href={crumb.href} />}>{crumb.label}</BreadcrumbLink>
                  ) : (
                    <BreadcrumbPage className="text-foreground/80">{crumb.label}</BreadcrumbPage>
                  )}
                </BreadcrumbItem>
              </span>
            ))}
          </BreadcrumbList>
        </Breadcrumb>
      ) : null}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-heading text-[15px] font-medium tracking-tight">{title}</h1>
          {description ? (
            <p className="mt-0.5 max-w-2xl text-[12px] leading-5 text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
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
  return <div className={cn("page-enter min-h-0 flex-1", className)}>{children}</div>;
}

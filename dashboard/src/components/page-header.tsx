import type { ReactNode } from "react";
import Link from "next/link";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Separator } from "@/components/ui/separator";

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
    <div className="flex flex-col gap-3 border-b px-4 py-3 sm:px-6">
      {crumbs && crumbs.length > 0 ? (
        <Breadcrumb>
          <BreadcrumbList>
            {crumbs.map((crumb, index) => (
              <span key={crumb.label} className="contents">
                {index > 0 ? <BreadcrumbSeparator /> : null}
                <BreadcrumbItem>
                  {crumb.href ? (
                    <BreadcrumbLink render={<Link href={crumb.href} />}>{crumb.label}</BreadcrumbLink>
                  ) : (
                    <BreadcrumbPage>{crumb.label}</BreadcrumbPage>
                  )}
                </BreadcrumbItem>
              </span>
            ))}
          </BreadcrumbList>
        </Breadcrumb>
      ) : null}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-heading text-lg font-medium tracking-tight">{title}</h1>
          {description ? (
            <p className="mt-0.5 max-w-2xl text-xs text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      <Separator className="hidden" />
    </div>
  );
}

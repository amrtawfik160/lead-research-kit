import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

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
    <div className="flex flex-wrap items-start gap-3 px-6 pt-7 pb-5">
      <div className="min-w-0">
        <h1 className="font-heading text-[23px] font-semibold tracking-[-0.022em] leading-[1.2] text-balance">
          {title}
        </h1>
        {description ? (
          <p className="mt-1 max-w-2xl text-[13.5px] leading-5 text-[#57544e] dark:text-[#b5b2ab]">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="ms-auto flex flex-wrap items-center gap-1.5">{actions}</div> : null}
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

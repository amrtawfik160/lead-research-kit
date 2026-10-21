"use client";

import type { LucideIcon } from "lucide-react";
import {
  Building2,
  Calendar,
  CircleDot,
  Link2,
  Mail,
  Shield,
  Signal,
  Tag,
  User,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetDescription,
  SheetFooter,
  SheetPanel,
  SheetPopup,
  SheetTitle,
} from "@/components/ui/sheet";
import { display } from "@/lib/format";
import type { FieldSection } from "@/lib/lead-fields";
import { cn } from "@/lib/utils";

function iconFor(label: string): LucideIcon {
  const raw = label.toLowerCase();
  if (/status|source|state|risk|verification/.test(raw)) return CircleDot;
  if (/priority|tier|confidence|score/.test(raw)) return Signal;
  if (/name|title|assignee|channel/.test(raw)) return User;
  if (/due|date|city/.test(raw)) return Calendar;
  if (/company|project|folder|provider/.test(raw)) return Building2;
  if (/email|mail/.test(raw)) return Mail;
  if (/url|website|linkedin/.test(raw)) return Link2;
  if (/hold|quality|mx|auth/.test(raw)) return Shield;
  if (/type|label|tag/.test(raw)) return Tag;
  return CircleDot;
}

export function DetailSheet({
  open,
  onOpenChange,
  title,
  description,
  issueId,
  fields,
  sections,
  extra,
  extraFirst = false,
  footer,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  issueId?: string;
  fields?: Array<{ label: string; value: React.ReactNode }>;
  sections?: FieldSection[];
  extra?: React.ReactNode;
  extraFirst?: boolean;
  footer?: React.ReactNode;
}): React.ReactElement {
  const blocks =
    sections && sections.length ? sections : fields?.length ? [{ title: "", fields }] : [];

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetPopup
        side="right"
        showCloseButton={false}
        className="w-[min(620px,100vw)] max-w-[620px] shadow-[-18px_0_40px_-24px_rgba(0,0,0,0.25)] sm:max-w-[620px]"
      >
        <div className="flex h-[46px] shrink-0 items-center gap-1 border-b px-2.5 ps-4">
          {description ? (
            <span className="truncate text-sm text-muted-foreground">{description}</span>
          ) : null}
          {description && issueId ? <span className="text-muted-foreground">/</span> : null}
          {issueId ? (
            <span className="font-mono text-xs text-muted-foreground">{issueId}</span>
          ) : null}
          <SheetTitle className="sr-only">{title}</SheetTitle>
          {description ? <SheetDescription className="sr-only">{description}</SheetDescription> : null}
          <span className="flex-1" />
          <Button size="icon-sm" variant="ghost" aria-label="Close" onClick={() => onOpenChange(false)}>
            <X />
          </Button>
        </div>
        <SheetPanel className="px-6 pt-5 pb-8">
          <p className="mb-3 font-heading text-2xl font-semibold tracking-tight">{title}</p>
          <div className="flex flex-col">
            {extra && extraFirst ? <div className="mb-[26px]">{extra}</div> : null}
            {blocks.map((block, index) => (
              <section key={block.title || `fields-${index}`} className={cn(index > 0 && "mt-[26px]")}>
                {block.title ? <h3 className="mb-2 text-sm font-semibold">{block.title}</h3> : null}
                <PropertyGrid rows={block.fields} />
              </section>
            ))}
            {extra && !extraFirst ? <div className="mt-[26px]">{extra}</div> : null}
          </div>
        </SheetPanel>
        {footer ? <SheetFooter className="px-6">{footer}</SheetFooter> : null}
      </SheetPopup>
    </Sheet>
  );
}

export function PropertyGrid({
  rows,
}: {
  rows: Array<{ label: string; value: React.ReactNode }>;
}): React.ReactElement {
  return (
    <dl className="grid grid-cols-[112px_minmax(0,1fr)] items-center gap-x-0 gap-y-0.5 text-sm">
      {rows.map((row) => {
        const Icon = iconFor(row.label);
        return (
          <div key={row.label} className="contents">
            <dt className="flex h-8 items-center gap-2 text-muted-foreground">
              <Icon className="size-3.5" />
              {row.label}
            </dt>
            <dd className="flex min-h-8 min-w-0 items-center wrap-break-word text-foreground">
              {row.value ?? "—"}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

export function FieldGrid({
  rows,
}: {
  rows: Array<{ label: string; value: React.ReactNode }>;
}): React.ReactElement {
  return <PropertyGrid rows={rows} />;
}

export function FieldList({
  rows,
  className,
}: {
  rows: Array<{ label: string; value: unknown }>;
  className?: string;
}): React.ReactElement {
  return (
    <dl className={cn("grid grid-cols-[112px_minmax(0,1fr)] items-center gap-y-0.5 text-sm", className)}>
      {rows.map((row) => {
        const Icon = iconFor(row.label);
        return (
          <div key={row.label} className="contents">
            <dt className="flex h-8 items-center gap-2 text-muted-foreground">
              <Icon className="size-3.5" />
              {row.label}
            </dt>
            <dd className="flex min-h-8 min-w-0 items-center wrap-break-word">{display(row.value)}</dd>
          </div>
        );
      })}
    </dl>
  );
}

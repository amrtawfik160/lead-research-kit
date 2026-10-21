"use client";

import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  Sheet,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetPanel,
  SheetPopup,
  SheetTitle,
} from "@/components/ui/sheet";
import { display } from "@/lib/format";
import type { FieldSection } from "@/lib/lead-fields";
import { cn } from "@/lib/utils";

export function DetailSheet({
  open,
  onOpenChange,
  title,
  description,
  fields,
  sections,
  extra,
  footer,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  fields?: Array<{ label: string; value: React.ReactNode }>;
  sections?: FieldSection[];
  extra?: React.ReactNode;
  footer?: React.ReactNode;
}): React.ReactElement {
  const blocks =
    sections && sections.length
      ? sections
      : fields?.length
        ? [{ title: "", fields }]
        : [];

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetPopup side="right" className="max-w-md sm:max-w-lg">
        <SheetHeader className="gap-1.5 px-5 py-4">
          <SheetTitle className="pr-8 text-base font-medium tracking-tight">{title}</SheetTitle>
          {description ? (
            <SheetDescription className="text-[12px] leading-5">{description}</SheetDescription>
          ) : null}
        </SheetHeader>
        <SheetPanel className="px-5 py-3">
          <div className="flex flex-col gap-5">
            {blocks.map((block, index) => (
              <section key={block.title || `fields-${index}`} className="min-w-0">
                {block.title ? (
                  <h3 className="mb-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                    {block.title}
                  </h3>
                ) : null}
                <FieldGrid rows={block.fields} />
              </section>
            ))}
            {extra ? (
              <>
                <Separator />
                {extra}
              </>
            ) : null}
          </div>
        </SheetPanel>
        <SheetFooter className="px-5">
          {footer}
          <Button size="sm" variant="ghost" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </SheetFooter>
      </SheetPopup>
    </Sheet>
  );
}

export function FieldGrid({
  rows,
}: {
  rows: Array<{ label: string; value: React.ReactNode }>;
}): React.ReactElement {
  return (
    <dl className="grid grid-cols-[7.25rem_1fr] gap-x-3 gap-y-2 text-[12px] leading-5">
      {rows.map((row) => (
        <div key={row.label} className="contents">
          <dt className="text-muted-foreground">{row.label}</dt>
          <dd className="min-w-0 wrap-break-word text-foreground">{row.value ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function FieldList({
  rows,
  className,
}: {
  rows: Array<{ label: string; value: unknown }>;
  className?: string;
}): React.ReactElement {
  return (
    <dl className={cn("grid grid-cols-[7.25rem_1fr] gap-x-3 gap-y-2 text-[12px] leading-5", className)}>
      {rows.map((row) => (
        <div key={row.label} className="contents">
          <dt className="text-muted-foreground">{row.label}</dt>
          <dd className="min-w-0 wrap-break-word">{display(row.value)}</dd>
        </div>
      ))}
    </dl>
  );
}

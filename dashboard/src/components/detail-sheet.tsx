"use client";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
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

export function DetailSheet({
  open,
  onOpenChange,
  title,
  description,
  fields,
  extra,
  footer,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  fields: Array<{ label: string; value: React.ReactNode }>;
  extra?: React.ReactNode;
  footer?: React.ReactNode;
}): React.ReactElement {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetPopup side="right" className="max-w-lg">
        <SheetHeader>
          <SheetTitle>{title}</SheetTitle>
          {description ? <SheetDescription>{description}</SheetDescription> : null}
        </SheetHeader>
        <SheetPanel>
          <ScrollArea className="h-full">
            <dl className="grid grid-cols-[7.5rem_1fr] gap-x-3 gap-y-2 text-xs">
              {fields.map((field) => (
                <div key={field.label} className="contents">
                  <dt className="text-muted-foreground">{field.label}</dt>
                  <dd className="min-w-0 wrap-break-word text-foreground">{field.value ?? "—"}</dd>
                </div>
              ))}
            </dl>
            {extra ? (
              <>
                <Separator className="my-4" />
                {extra}
              </>
            ) : null}
          </ScrollArea>
        </SheetPanel>
        {footer ? (
          <SheetFooter>
            {footer}
            <Button size="sm" variant="ghost" onClick={() => onOpenChange(false)}>
              Close
            </Button>
          </SheetFooter>
        ) : null}
      </SheetPopup>
    </Sheet>
  );
}

export function FieldList({
  rows,
}: {
  rows: Array<{ label: string; value: unknown }>;
}): React.ReactElement {
  return (
    <dl className="grid grid-cols-[7.5rem_1fr] gap-x-3 gap-y-2 text-xs">
      {rows.map((row) => (
        <div key={row.label} className="contents">
          <dt className="text-muted-foreground">{row.label}</dt>
          <dd className="min-w-0 wrap-break-word">{display(row.value)}</dd>
        </div>
      ))}
    </dl>
  );
}

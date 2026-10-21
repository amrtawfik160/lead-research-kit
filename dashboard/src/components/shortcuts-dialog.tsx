"use client";

import {
  Dialog,
  DialogDescription,
  DialogHeader,
  DialogPanel,
  DialogPopup,
  DialogTitle,
} from "@/components/ui/dialog";
import { Kbd } from "@/components/ui/kbd";

const GROUPS: Array<{ title: string; items: Array<[string, string[]]> }> = [
  {
    title: "General",
    items: [
      ["Command menu", ["⌘", "K"]],
      ["Search leads", ["/"]],
      ["Keyboard shortcuts", ["?"]],
      ["Toggle sidebar", ["["]],
      ["Toggle theme", ["D"]],
    ],
  },
  {
    title: "Go to",
    items: [
      ["Overview", ["G", "O"]],
      ["Daily", ["G", "D"]],
      ["Leads", ["G", "L"]],
      ["Quality", ["G", "Q"]],
      ["Budget", ["G", "B"]],
      ["Settings", ["G", ","]],
      ["Open daily", ["N"]],
    ],
  },
  {
    title: "Lists",
    items: [
      ["Next lead", ["J"]],
      ["Previous lead", ["K"]],
      ["Open lead", ["↵"]],
      ["Clear selection", ["Esc"]],
    ],
  },
];

export function ShortcutsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): React.ReactElement {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogPopup className="max-w-[720px] sm:max-w-[720px]">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>Jump between Overview, Daily, Leads, and Budget without leaving the keyboard.</DialogDescription>
        </DialogHeader>
        <DialogPanel className="grid gap-6 sm:grid-cols-2">
          {GROUPS.map((group) => (
            <div key={group.title}>
              <p className="mb-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                {group.title}
              </p>
              {group.items.map(([label, keys]) => (
                <div
                  key={label}
                  className="flex h-8 items-center border-b text-sm"
                >
                  <span className="min-w-0 flex-1 truncate">{label}</span>
                  <span className="flex items-center gap-1">
                    {keys.map((key, index) => (
                      <span key={`${label}-${key}`} className="inline-flex items-center gap-1">
                        {index > 0 && keys[0] === "G" ? (
                          <span className="text-xs text-muted-foreground">then</span>
                        ) : null}
                        <Kbd className="h-5 min-w-5 px-1">{key}</Kbd>
                      </span>
                    ))}
                  </span>
                </div>
              ))}
            </div>
          ))}
        </DialogPanel>
      </DialogPopup>
    </Dialog>
  );
}

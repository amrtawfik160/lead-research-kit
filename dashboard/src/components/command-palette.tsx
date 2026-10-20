"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef } from "react";
import {
  Command,
  CommandCollection,
  CommandDialog,
  CommandDialogPopup,
  CommandEmpty,
  CommandFooter,
  CommandGroup,
  CommandGroupLabel,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import { ALL_NAV } from "@/lib/nav";

type CommandEntry = {
  value: string;
  label: string;
  shortcut?: string;
  run: () => void;
};

export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): React.ReactElement {
  const router = useRouter();
  const groups = useMemo(() => {
    const navigate: CommandEntry[] = ALL_NAV.map((item) => ({
      value: `go:${item.href}`,
      label: `Go to ${item.label}`,
      shortcut: item.shortcut,
      run: () => router.push(item.href),
    }));
    return [
      { value: "Navigate", items: navigate },
      {
        value: "Actions",
        items: [
          {
            value: "action:quality",
            label: "Clean a lead list",
            run: () => router.push("/quality"),
          },
          {
            value: "action:settings",
            label: "Workspace settings",
            run: () => router.push("/settings"),
          },
        ],
      },
    ];
  }, [router]);

  function run(entry: CommandEntry) {
    onOpenChange(false);
    entry.run();
  }

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandDialogPopup>
        <Command items={groups}>
          <CommandInput placeholder="Type a command or search…" />
          <CommandEmpty>No commands found.</CommandEmpty>
          <CommandList>
            {(group: { value: string; items: CommandEntry[] }, index: number) => (
              <div key={group.value}>
                <CommandGroup items={group.items}>
                  <CommandGroupLabel>{group.value}</CommandGroupLabel>
                  <CommandCollection>
                    {(item: CommandEntry) => (
                      <CommandItem key={item.value} value={item} onClick={() => run(item)}>
                        {item.label}
                        {item.shortcut ? <CommandShortcut>{item.shortcut}</CommandShortcut> : null}
                      </CommandItem>
                    )}
                  </CommandCollection>
                </CommandGroup>
                {index < groups.length - 1 ? <CommandSeparator /> : null}
              </div>
            )}
          </CommandList>
        </Command>
        <CommandFooter>
          <KbdGroup>
            <Kbd>↵</Kbd>
            <span>to open</span>
          </KbdGroup>
          <KbdGroup>
            <Kbd>esc</Kbd>
            <span>to close</span>
          </KbdGroup>
        </CommandFooter>
      </CommandDialogPopup>
    </CommandDialog>
  );
}

export function useCommandHotkeys(onOpen: () => void): void {
  const router = useRouter();
  const pending = useRef<string | null>(null);
  const clearTimer = useRef<number>(0);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        onOpen();
        return;
      }
      if (typing) return;
      if (event.key === "g" || event.key === "G") {
        pending.current = "g";
        window.clearTimeout(clearTimer.current);
        clearTimer.current = window.setTimeout(() => {
          pending.current = null;
        }, 800);
        return;
      }
      if (pending.current === "g") {
        const match = ALL_NAV.find((item) => {
          const letter = item.shortcut.split(" ").at(-1)?.toLowerCase();
          return letter === event.key.toLowerCase();
        });
        pending.current = null;
        window.clearTimeout(clearTimer.current);
        if (match) {
          event.preventDefault();
          router.push(match.href);
        }
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      window.clearTimeout(clearTimer.current);
    };
  }, [onOpen, router]);
}

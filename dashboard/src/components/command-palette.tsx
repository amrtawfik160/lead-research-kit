"use client";

import { Moon, Settings, ShieldCheck, Sun } from "lucide-react";
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
import { useTheme } from "@/components/theme-provider";
import { ALL_NAV } from "@/lib/nav";
import type { LucideIcon } from "lucide-react";

type CommandEntry = {
  value: string;
  label: string;
  shortcut?: string;
  icon?: LucideIcon;
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
  const { theme, toggleTheme } = useTheme();
  const groups = useMemo(() => {
    const navigate: CommandEntry[] = ALL_NAV.map((item) => ({
      value: `go:${item.href}`,
      label: `Go to ${item.label}`,
      shortcut: item.shortcut,
      icon: item.icon,
      run: () => router.push(item.href),
    }));
    return [
      {
        value: "Suggestions",
        items: [
          {
            value: "action:inbox",
            label: "Open inbox",
            shortcut: "G D",
            icon: ALL_NAV.find((item) => item.href === "/daily")?.icon,
            run: () => router.push("/daily"),
          },
          {
            value: "action:tasks",
            label: "Open my tasks",
            shortcut: "G L",
            icon: ALL_NAV.find((item) => item.href === "/leads")?.icon,
            run: () => router.push("/leads"),
          },
        ],
      },
      { value: "Navigate", items: navigate },
      {
        value: "Actions",
        items: [
          {
            value: "action:quality",
            label: "Clean a lead list",
            icon: ShieldCheck,
            run: () => router.push("/quality"),
          },
          {
            value: "action:settings",
            label: "Workspace settings",
            icon: Settings,
            run: () => router.push("/settings"),
          },
          {
            value: "action:theme",
            label: theme === "dark" ? "Switch to light theme" : "Switch to dark theme",
            icon: theme === "dark" ? Sun : Moon,
            run: () => toggleTheme(),
          },
        ],
      },
    ];
  }, [router, theme, toggleTheme]);

  function run(entry: CommandEntry) {
    onOpenChange(false);
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    entry.run();
  }

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandDialogPopup className="max-h-[min(36rem,82vh)]">
        <Command items={groups}>
          <CommandInput placeholder="Type a command or search…" />
          <CommandEmpty>No commands found.</CommandEmpty>
          <CommandList>
            {(group: { value: string; items: CommandEntry[] }, index: number) => (
              <div key={group.value}>
                <CommandGroup items={group.items}>
                  <CommandGroupLabel className="px-2 text-[11px] font-semibold tracking-[0.06em] text-muted-foreground uppercase">
                    {group.value}
                  </CommandGroupLabel>
                  <CommandCollection>
                    {(item: CommandEntry) => (
                      <CommandItem key={item.value} value={item} onClick={() => run(item)}>
                        {item.icon ? <item.icon className="size-4 text-muted-foreground" /> : null}
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
        <CommandFooter className="text-[11px] text-muted-foreground">
          <KbdGroup>
            <Kbd>↑</Kbd>
            <Kbd>↓</Kbd>
            <span>navigate</span>
          </KbdGroup>
          <KbdGroup>
            <Kbd>↵</Kbd>
            <span>select</span>
          </KbdGroup>
          <KbdGroup>
            <Kbd>esc</Kbd>
            <span>close</span>
          </KbdGroup>
          <span className="ms-auto">veinore</span>
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
      const paletteOpen = document.querySelector('[data-slot="command-dialog-popup"]');
      const typing =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable ||
          Boolean(paletteOpen && paletteOpen.contains(target)));
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        onOpen();
        return;
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "b") {
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

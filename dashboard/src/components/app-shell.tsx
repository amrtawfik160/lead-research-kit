"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useState } from "react";
import { CommandPalette, useCommandHotkeys } from "@/components/command-palette";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarSeparator,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { NAV } from "@/lib/nav";

export function AppShell({ children }: { children: React.ReactNode }): React.ReactElement {
  const pathname = usePathname();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const openPalette = useCallback(() => setPaletteOpen(true), []);
  useCommandHotkeys(openPalette);

  return (
    <TooltipProvider>
      <SidebarProvider
        style={
          {
            "--sidebar-width": "15rem",
            "--sidebar-width-icon": "3rem",
          } as React.CSSProperties
        }
      >
        <Sidebar collapsible="icon" variant="inset">
          <SidebarHeader className="px-3 py-3">
            <div className="flex items-center gap-2 overflow-hidden">
              <div className="flex size-6 shrink-0 items-center justify-center rounded-md border bg-sidebar-accent text-[10px] font-medium">
                LR
              </div>
              <div className="min-w-0 group-data-[collapsible=icon]:hidden">
                <div className="truncate text-sm font-medium text-sidebar-accent-foreground">
                  Lead Research
                </div>
                <div className="truncate text-[11px] text-muted-foreground">Evidence-first kit</div>
              </div>
            </div>
          </SidebarHeader>
          <SidebarSeparator />
          <SidebarContent>
            {NAV.map((group) => (
              <SidebarGroup key={group.label}>
                <SidebarGroupLabel>{group.label}</SidebarGroupLabel>
                <SidebarGroupContent>
                  <SidebarMenu>
                    {group.items.map((item) => {
                      const active =
                        item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
                      return (
                        <SidebarMenuItem key={item.href}>
                          <SidebarMenuButton
                            isActive={active}
                            size="sm"
                            tooltip={item.label}
                            render={<Link href={item.href} />}
                          >
                            <item.icon />
                            <span>{item.label}</span>
                          </SidebarMenuButton>
                        </SidebarMenuItem>
                      );
                    })}
                  </SidebarMenu>
                </SidebarGroupContent>
              </SidebarGroup>
            ))}
          </SidebarContent>
          <SidebarFooter className="px-2 pb-3">
            <Button
              size="sm"
              variant="outline"
              className="w-full justify-between text-xs group-data-[collapsible=icon]:px-0"
              onClick={openPalette}
            >
              <span className="group-data-[collapsible=icon]:hidden">Search</span>
              <Kbd className="group-data-[collapsible=icon]:hidden">⌘K</Kbd>
            </Button>
          </SidebarFooter>
          <SidebarRail />
        </Sidebar>
        <SidebarInset className="min-w-0 bg-background">
          <header className="flex h-11 items-center gap-2 border-b px-3">
            <SidebarTrigger />
            <button
              type="button"
              onClick={openPalette}
              className="flex h-7 min-w-0 flex-1 items-center justify-between rounded-md border bg-muted/40 px-2 text-left text-xs text-muted-foreground transition-colors hover:bg-muted"
            >
              <span>Search leads, contacts, commands…</span>
              <Kbd>⌘K</Kbd>
            </button>
          </header>
          <div className="flex min-h-0 flex-1 flex-col overflow-auto">{children}</div>
        </SidebarInset>
        <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      </SidebarProvider>
    </TooltipProvider>
  );
}

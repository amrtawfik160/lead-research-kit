"use client";

import { ChevronsUpDown, Moon, Plus, Search, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useState, type CSSProperties, type ReactNode } from "react";
import { CommandPalette, useCommandHotkeys } from "@/components/command-palette";
import { EntityAvatar } from "@/components/entity-avatar";
import { Mark } from "@/components/logo";
import { useTheme } from "@/components/theme-provider";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { Menu, MenuItem, MenuPopup, MenuTrigger } from "@/components/ui/menu";
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
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { Tooltip, TooltipPopup, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { FOOTER_NAV, navForPath, PRIMARY, WORKSPACE } from "@/lib/nav";
import { cn } from "@/lib/utils";

function NavList({ items, pathname }: { items: typeof PRIMARY; pathname: string }): React.ReactElement {
  return (
    <SidebarMenu className="gap-px">
      {items.map((item) => {
        const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        return (
          <SidebarMenuItem key={item.href}>
            <SidebarMenuButton
              isActive={active}
              size="sm"
              tooltip={item.label}
              render={<Link href={item.href} />}
              className="h-[30px] rounded-[6px] px-2 text-[13.5px] font-normal"
            >
              <item.icon className="size-4" />
              <span>{item.label}</span>
            </SidebarMenuButton>
            <SidebarMenuBadge className="pointer-events-none text-[11px] font-normal text-muted-foreground opacity-0 transition-opacity group-hover/menu-item:opacity-100">
              {item.shortcut}
            </SidebarMenuBadge>
          </SidebarMenuItem>
        );
      })}
    </SidebarMenu>
  );
}

export function AppShell({ children }: { children: ReactNode }): React.ReactElement {
  const pathname = usePathname();
  const router = useRouter();
  const { theme, toggleTheme } = useTheme();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const openPalette = useCallback(() => setPaletteOpen(true), []);
  useCommandHotkeys(openPalette);
  const current = navForPath(pathname);

  return (
    <TooltipProvider>
      <SidebarProvider
        style={
          {
            "--sidebar-width": "252px",
            "--sidebar-width-icon": "52px",
          } as CSSProperties
        }
      >
        <Sidebar collapsible="icon" variant="sidebar" className="border-sidebar-border">
          <SidebarHeader className="gap-0 px-2 pt-2 pb-1">
            <div className="flex items-center gap-0.5">
              <Menu>
                <MenuTrigger
                  className="flex h-[32px] min-w-0 flex-1 items-center gap-2 rounded-[6px] px-1.5 text-left transition-colors hover:bg-sidebar-accent"
                  aria-label="Switch workspace"
                >
                  <span className="grid size-[22px] place-items-center overflow-hidden rounded-[6px] bg-black text-white">
                    <Mark className="size-3.5" />
                  </span>
                  <span className="min-w-0 truncate text-[13.5px] font-semibold group-data-[collapsible=icon]:hidden">
                    Veinore
                  </span>
                  <ChevronsUpDown className="ms-auto size-3.5 text-muted-foreground group-data-[collapsible=icon]:hidden" />
                </MenuTrigger>
                <MenuPopup align="start" className="min-w-48">
                  <MenuItem>Veinore workspace</MenuItem>
                  <MenuItem onClick={() => router.push("/settings")}>Open settings</MenuItem>
                </MenuPopup>
              </Menu>
              <SidebarTrigger className="size-[26px] group-data-[collapsible=icon]:hidden" />
            </div>
          </SidebarHeader>
          <SidebarContent className="gap-0">
            <SidebarGroup className="p-2 pt-1">
              <SidebarGroupContent>
                <NavList items={PRIMARY} pathname={pathname} />
              </SidebarGroupContent>
            </SidebarGroup>
            <SidebarGroup className="p-2 pt-1">
              <SidebarGroupLabel className="h-6 px-2 text-[11px] font-semibold tracking-[0.06em] text-[#6b6760] uppercase dark:text-[#9a968f]">
                Workspace
              </SidebarGroupLabel>
              <SidebarGroupContent>
                <NavList items={WORKSPACE} pathname={pathname} />
              </SidebarGroupContent>
            </SidebarGroup>
          </SidebarContent>
          <SidebarFooter className="gap-1 border-t px-2 py-2">
            <NavList items={FOOTER_NAV} pathname={pathname} />
            <button
              type="button"
              onClick={toggleTheme}
              className="flex h-9 w-full items-center gap-2 rounded-[6px] px-2 text-left text-[13.5px] font-medium transition-colors hover:bg-sidebar-accent group-data-[collapsible=icon]:justify-center"
            >
              <EntityAvatar name="Veinore" size={22} />
              <span className="min-w-0 flex-1 truncate group-data-[collapsible=icon]:hidden">Veinore</span>
              <ChevronsUpDown className="size-3.5 text-muted-foreground group-data-[collapsible=icon]:hidden" />
            </button>
          </SidebarFooter>
          <SidebarRail />
        </Sidebar>
        <SidebarInset className="min-w-0 bg-background">
          <header className="flex h-[46px] shrink-0 items-center gap-1.5 border-b px-3">
            <nav className="flex min-w-0 items-center gap-2 text-[12.5px] text-muted-foreground">
              <span className="font-medium text-foreground">{current?.label || "Veinore"}</span>
            </nav>
            <span className="flex-1" />
            <button
              type="button"
              onClick={openPalette}
              className="flex h-[26px] w-[220px] items-center gap-2 rounded-[6px] border bg-[#f7f6f3] px-2.5 text-[12.5px] text-muted-foreground transition-colors hover:border-[#dad7d0] hover:text-foreground dark:bg-[#181817]"
            >
              <Search className="size-3.5 shrink-0" />
              <span className="min-w-0 flex-1 truncate text-left">Search or jump to…</span>
              <Kbd className="h-4 min-w-0 px-1 text-[10px]">⌘K</Kbd>
            </button>
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button size="icon-sm" variant="ghost" aria-label="Toggle color theme" onClick={toggleTheme} />
                }
              >
                <Sun className="hidden dark:block" />
                <Moon className="dark:hidden" />
              </TooltipTrigger>
              <TooltipPopup>
                <span className="inline-flex items-center gap-1.5">
                  {theme === "dark" ? "Switch to light" : "Switch to dark"}
                  <Kbd className="h-4 px-1">D</Kbd>
                </span>
              </TooltipPopup>
            </Tooltip>
            <Menu>
              <MenuTrigger className="inline-flex h-[26px] items-center gap-1 rounded-[6px] border border-[#dad7d0] bg-background px-2 text-[12px] font-medium shadow-[0_1px_2px_rgba(28,25,20,0.05)] transition-colors hover:bg-[#f6f5f2] dark:border-[#3a3936] dark:hover:bg-[#252524]">
                <Plus className="size-3.5" />
                New
              </MenuTrigger>
              <MenuPopup align="end" className="min-w-40">
                <MenuItem onClick={() => router.push("/daily")}>Open inbox</MenuItem>
                <MenuItem onClick={() => router.push("/leads")}>Open my tasks</MenuItem>
                <MenuItem onClick={() => router.push("/quality")}>Clean a list</MenuItem>
              </MenuPopup>
            </Menu>
          </header>
          <div className="flex min-h-0 flex-1 flex-col overflow-auto">{children}</div>
        </SidebarInset>
        <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      </SidebarProvider>
    </TooltipProvider>
  );
}

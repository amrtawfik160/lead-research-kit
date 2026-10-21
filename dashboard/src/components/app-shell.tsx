"use client";

import { Bell, ChevronDown, ChevronsUpDown, CircleHelp, Keyboard, Moon, Plus, Search, Settings, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { CommandPalette, useCommandHotkeys } from "@/components/command-palette";
import { EntityAvatar } from "@/components/entity-avatar";
import { Mark } from "@/components/logo";
import { ShortcutsDialog } from "@/components/shortcuts-dialog";
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
  useSidebar,
} from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { FOOTER_NAV, navForPath, PRIMARY, WORKSPACE, type NavItem } from "@/lib/nav";
import { api } from "@/lib/api";
import type { Overview } from "@/lib/types";
import { cn } from "@/lib/utils";

function NavList({
  items,
  pathname,
  counts,
  accents,
}: {
  items: NavItem[];
  pathname: string;
  counts?: Record<string, number>;
  accents?: Record<string, boolean>;
}): React.ReactElement {
  return (
    <SidebarMenu className="gap-px">
      {items.map((item) => {
        const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        const count = counts?.[item.href];
        const accent = Boolean(accents?.[item.href] && count);
        return (
          <SidebarMenuItem key={item.href}>
            <SidebarMenuButton
              isActive={active}
              size="sm"
              tooltip={`${item.label} · ${item.shortcut}`}
              render={<Link href={item.href} />}
              className="h-[30px] rounded-[6px] px-2 text-[13.5px] font-normal"
            >
              <item.icon className="size-4" />
              <span>{item.label}</span>
            </SidebarMenuButton>
            {count != null && count > 0 ? (
              <SidebarMenuBadge
                className={cn(
                  "pointer-events-none text-[11px] font-normal tabular-nums",
                  accent
                    ? "grid h-4 min-w-4 place-items-center rounded-full bg-primary px-1 font-semibold text-primary-foreground"
                    : "text-muted-foreground",
                )}
              >
                {count}
              </SidebarMenuBadge>
            ) : null}
          </SidebarMenuItem>
        );
      })}
    </SidebarMenu>
  );
}

function ShellChrome({ children }: { children: ReactNode }): React.ReactElement {
  const pathname = usePathname();
  const router = useRouter();
  const { theme, toggleTheme } = useTheme();
  const { toggleSidebar } = useSidebar();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [overview, setOverview] = useState<Overview | null>(null);
  const openPalette = useCallback(() => setPaletteOpen(true), []);
  const openShortcuts = useCallback(() => setShortcutsOpen(true), []);
  useCommandHotkeys({
    onOpen: openPalette,
    onShortcuts: openShortcuts,
    onToggleSidebar: toggleSidebar,
    onToggleTheme: toggleTheme,
  });
  const current = navForPath(pathname);

  useEffect(() => {
    let cancelled = false;
    api
      .overview()
      .then((payload) => {
        if (!cancelled) setOverview(payload);
      })
      .catch(() => {
        if (!cancelled) setOverview(null);
      });
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  const counts: Record<string, number> = {
    "/daily": overview?.latest_daily?.delivered ?? 0,
    "/leads": overview?.latest_daily?.delivered ?? 0,
    "/quality": overview?.status.held ?? 0,
    "/contacts": overview?.status.contacts ?? 0,
    "/research": overview?.research_queue_count ?? 0,
  };
  const accents: Record<string, boolean> = {
    "/daily": (overview?.status.held ?? 0) > 0 || (overview?.latest_daily?.delivered ?? 0) > 0,
  };

  return (
    <>
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
              <NavList items={PRIMARY} pathname={pathname} counts={counts} accents={accents} />
            </SidebarGroupContent>
          </SidebarGroup>
          <SidebarGroup className="p-2 pt-1">
            <SidebarGroupLabel className="h-6 px-2 text-[11px] font-semibold tracking-[0.06em] text-[#6b6760] uppercase dark:text-[#9a968f]">
              Workspace
            </SidebarGroupLabel>
            <SidebarGroupContent>
              <NavList items={WORKSPACE} pathname={pathname} counts={counts} />
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
        <SidebarFooter className="gap-1 border-t px-2 py-2">
          <SidebarMenu className="gap-px">
            <SidebarMenuItem>
              <Menu>
                <MenuTrigger
                  className="flex h-[30px] w-full items-center gap-2 rounded-[6px] px-2 text-left text-[13.5px] text-sidebar-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground group-data-[collapsible=icon]:justify-center"
                  aria-label="Help and resources"
                >
                  <CircleHelp className="size-4 text-[#8c8880]" />
                  <span className="group-data-[collapsible=icon]:hidden">Help & resources</span>
                </MenuTrigger>
                <MenuPopup align="start" side="top" className="min-w-56">
                  <MenuItem onClick={openShortcuts}>
                    <Keyboard className="size-4" />
                    Keyboard shortcuts
                    <span className="ms-auto text-[11px] text-muted-foreground">?</span>
                  </MenuItem>
                  <MenuItem onClick={openPalette}>
                    <Search className="size-4" />
                    Command menu
                    <span className="ms-auto text-[11px] text-muted-foreground">⌘K</span>
                  </MenuItem>
                </MenuPopup>
              </Menu>
            </SidebarMenuItem>
          </SidebarMenu>
          <NavList items={FOOTER_NAV} pathname={pathname} />
          <Menu>
            <MenuTrigger
              className="flex h-9 w-full items-center gap-2 rounded-[6px] px-2 text-left text-[13.5px] font-medium transition-colors hover:bg-sidebar-accent group-data-[collapsible=icon]:justify-center"
              aria-label="Account menu"
            >
              <EntityAvatar name="Veinore" size={22} />
              <span className="min-w-0 flex-1 truncate group-data-[collapsible=icon]:hidden">Veinore</span>
              <ChevronsUpDown className="size-3.5 text-muted-foreground group-data-[collapsible=icon]:hidden" />
            </MenuTrigger>
            <MenuPopup align="start" side="top" className="min-w-48">
              <MenuItem onClick={() => router.push("/settings")}>
                <Settings className="size-4" />
                Settings
              </MenuItem>
              <MenuItem onClick={toggleTheme}>
                {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
                {theme === "dark" ? "Switch to light" : "Switch to dark"}
                <span className="ms-auto text-[11px] text-muted-foreground">D</span>
              </MenuItem>
            </MenuPopup>
          </Menu>
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
      <SidebarInset className="min-w-0 bg-background">
        <header className="flex h-[46px] shrink-0 items-center gap-1.5 border-b pr-3 pl-6">
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
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label="Notifications"
            className="relative"
            render={<Link href="/daily" />}
          >
            <Bell />
            {accents["/daily"] ? (
              <span className="absolute top-1.5 right-1.5 size-1.5 rounded-full bg-primary shadow-[0_0_0_2px_var(--background)]" />
            ) : null}
          </Button>
          <Menu>
            <MenuTrigger className="inline-flex h-[26px] items-center gap-1 rounded-[6px] border border-[#dad7d0] bg-background px-2 text-[12px] font-medium shadow-[0_1px_2px_rgba(28,25,20,0.05)] transition-colors hover:bg-[#f6f5f2] dark:border-[#3a3936] dark:hover:bg-[#252524]">
              <Plus className="size-3.5" />
              New
              <ChevronDown className="size-3" />
            </MenuTrigger>
            <MenuPopup align="end" className="min-w-40">
              <MenuItem onClick={() => router.push("/daily")}>Open inbox</MenuItem>
              <MenuItem onClick={() => router.push("/leads")}>Open my tasks</MenuItem>
              <MenuItem onClick={() => router.push("/quality")}>Clean a list</MenuItem>
            </MenuPopup>
          </Menu>
          <Menu>
            <MenuTrigger
              className="hidden h-auto rounded-full p-0.5 hover:bg-transparent md:inline-flex"
              aria-label="Account menu"
            >
              <EntityAvatar name="Veinore" size={22} />
            </MenuTrigger>
            <MenuPopup align="end" className="min-w-44">
              <MenuItem onClick={() => router.push("/settings")}>Settings</MenuItem>
              <MenuItem onClick={toggleTheme}>{theme === "dark" ? "Switch to light" : "Switch to dark"}</MenuItem>
            </MenuPopup>
          </Menu>
        </header>
        <div className="flex min-h-0 flex-1 flex-col overflow-auto">{children}</div>
      </SidebarInset>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} onShortcuts={openShortcuts} />
      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
    </>
  );
}

export function AppShell({ children }: { children: ReactNode }): React.ReactElement {
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
        <ShellChrome>{children}</ShellChrome>
      </SidebarProvider>
    </TooltipProvider>
  );
}

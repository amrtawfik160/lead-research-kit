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
import { Badge } from "@/components/ui/badge";
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
              tooltip={`${item.label} · ${item.shortcut}`}
              render={<Link href={item.href} />}
            >
              <item.icon />
              <span className="min-w-0 flex-1 truncate">{item.label}</span>
              {count != null && count > 0 ? (
                <Badge
                  render={<div />}
                  size="default"
                  variant="secondary"
                  className={cn(
                    "ms-auto tabular-nums group-data-[collapsible=icon]:hidden",
                    accent && "bg-info/12 text-info-foreground",
                  )}
                >
                  {count}
                </Badge>
              ) : null}
            </SidebarMenuButton>
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
                className="flex h-8 min-w-0 flex-1 items-center gap-2 rounded-lg px-2 text-left text-sm transition-colors hover:bg-sidebar-accent"
                aria-label="Switch workspace"
              >
                <span className="grid size-6 place-items-center overflow-hidden rounded-md bg-sidebar-primary text-sidebar-primary-foreground">
                  <Mark className="size-3.5" />
                </span>
                <span className="min-w-0 truncate font-semibold group-data-[collapsible=icon]:hidden">
                  Veinore
                </span>
                <ChevronsUpDown className="ms-auto size-3.5 text-muted-foreground group-data-[collapsible=icon]:hidden" />
              </MenuTrigger>
              <MenuPopup align="start" className="min-w-48">
                <MenuItem>Veinore workspace</MenuItem>
                <MenuItem onClick={() => router.push("/settings")}>Open settings</MenuItem>
              </MenuPopup>
            </Menu>
            <SidebarTrigger className="group-data-[collapsible=icon]:hidden" />
          </div>
        </SidebarHeader>
        <SidebarContent className="gap-0">
          <SidebarGroup className="p-2 pt-1">
            <SidebarGroupContent>
              <NavList items={PRIMARY} pathname={pathname} counts={counts} accents={accents} />
            </SidebarGroupContent>
          </SidebarGroup>
          <SidebarGroup className="p-2 pt-1">
            <SidebarGroupLabel>Workspace</SidebarGroupLabel>
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
                  className="flex h-8 w-full items-center gap-2 rounded-lg px-2 text-left text-sm text-sidebar-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground group-data-[collapsible=icon]:justify-center"
                  aria-label="Shortcuts"
                >
                  <CircleHelp className="size-4" />
                  <span className="group-data-[collapsible=icon]:hidden">Shortcuts</span>
                </MenuTrigger>
                <MenuPopup align="start" side="top" className="min-w-56">
                  <MenuItem onClick={openShortcuts}>
                    <Keyboard className="size-4" />
                    Keyboard shortcuts
                    <Kbd className="ms-auto">?</Kbd>
                  </MenuItem>
                  <MenuItem onClick={openPalette}>
                    <Search className="size-4" />
                    Command menu
                    <Kbd className="ms-auto">⌘K</Kbd>
                  </MenuItem>
                </MenuPopup>
              </Menu>
            </SidebarMenuItem>
          </SidebarMenu>
          <NavList items={FOOTER_NAV} pathname={pathname} />
          <Menu>
            <MenuTrigger
              className="flex h-8 w-full items-center gap-2 rounded-lg px-2 text-left text-sm font-medium transition-colors hover:bg-sidebar-accent group-data-[collapsible=icon]:justify-center"
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
                <Kbd className="ms-auto">D</Kbd>
              </MenuItem>
            </MenuPopup>
          </Menu>
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
      <SidebarInset className="min-w-0 bg-background">
        <header className="flex h-14 shrink-0 items-center gap-2 border-b px-6">
          <nav className="flex min-w-0 items-center gap-2 text-sm text-muted-foreground">
            <span className="font-medium text-foreground">{current?.label || "Veinore"}</span>
          </nav>
          <span className="flex-1" />
          <Button
            size="sm"
            variant="outline"
            onClick={openPalette}
            className="w-[220px] justify-start font-normal text-muted-foreground"
            aria-label="Search leads"
          >
            <Search />
            <span className="min-w-0 flex-1 truncate text-left">Search leads…</span>
            <Kbd className="ms-auto">⌘K</Kbd>
          </Button>
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label="Daily deliveries"
            className="relative"
            render={<Link href="/daily" />}
          >
            <Bell />
            {accents["/daily"] ? (
              <span className="absolute top-1.5 right-1.5 size-1.5 rounded-full bg-primary shadow-[0_0_0_2px_var(--background)]" />
            ) : null}
          </Button>
          <Menu>
            <MenuTrigger render={<Button size="sm" variant="outline" />}>
              <Plus />
              New
              <ChevronDown />
            </MenuTrigger>
            <MenuPopup align="end" className="min-w-40">
              <MenuItem onClick={() => router.push("/daily")}>Open daily</MenuItem>
              <MenuItem onClick={() => router.push("/leads")}>Open leads</MenuItem>
              <MenuItem onClick={() => router.push("/quality")}>Clean a lead list</MenuItem>
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
            "--sidebar-width": "16rem",
            "--sidebar-width-icon": "3rem",
          } as CSSProperties
        }
      >
        <ShellChrome>{children}</ShellChrome>
      </SidebarProvider>
    </TooltipProvider>
  );
}

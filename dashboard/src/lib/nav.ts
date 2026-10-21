import type { LucideIcon } from "lucide-react";
import {
  Activity,
  Building2,
  CalendarDays,
  ClipboardCheck,
  History,
  House,
  Search,
  Settings,
  Shield,
  Users,
  Wallet,
} from "lucide-react";

export type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  shortcut: string;
};

export const PRIMARY: NavItem[] = [
  { href: "/", label: "Overview", icon: House, shortcut: "G O" },
  { href: "/daily", label: "Daily", icon: CalendarDays, shortcut: "G D" },
  { href: "/leads", label: "Leads", icon: Users, shortcut: "G L" },
  { href: "/quality", label: "Quality", icon: ClipboardCheck, shortcut: "G Q" },
];

export const WORKSPACE: NavItem[] = [
  { href: "/contacts", label: "Contacts", icon: Users, shortcut: "G C" },
  { href: "/research", label: "Research", icon: Search, shortcut: "G R" },
  { href: "/companies", label: "Companies", icon: Building2, shortcut: "G M" },
  { href: "/providers", label: "Providers", icon: Shield, shortcut: "G P" },
  { href: "/budget", label: "Budget", icon: Wallet, shortcut: "G B" },
  { href: "/jobs", label: "Jobs", icon: Activity, shortcut: "G J" },
  { href: "/history", label: "History", icon: History, shortcut: "G H" },
];

export const FOOTER_NAV: NavItem[] = [
  { href: "/settings", label: "Settings", icon: Settings, shortcut: "G ," },
];

export const NAV: Array<{ label: string; items: NavItem[] }> = [
  { label: "", items: PRIMARY },
  { label: "Workspace", items: WORKSPACE },
];

export const ALL_NAV = [...PRIMARY, ...WORKSPACE, ...FOOTER_NAV];

export function navForPath(pathname: string): NavItem | undefined {
  return ALL_NAV.find((item) => (item.href === "/" ? pathname === "/" : pathname.startsWith(item.href)));
}

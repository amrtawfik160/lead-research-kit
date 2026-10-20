import type { LucideIcon } from "lucide-react";
import {
  Activity,
  Building2,
  ClipboardCheck,
  Database,
  History,
  Inbox,
  LayoutDashboard,
  Search,
  Settings,
  Shield,
  Users,
} from "lucide-react";

export type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  shortcut: string;
};

export const NAV: Array<{ label: string; items: NavItem[] }> = [
  {
    label: "Research",
    items: [
      { href: "/", label: "Overview", icon: LayoutDashboard, shortcut: "G O" },
      { href: "/daily", label: "Daily", icon: Inbox, shortcut: "G D" },
      { href: "/leads", label: "Leads", icon: Database, shortcut: "G L" },
      { href: "/contacts", label: "Contacts", icon: Users, shortcut: "G C" },
      { href: "/research", label: "Research", icon: Search, shortcut: "G R" },
    ],
  },
  {
    label: "Operations",
    items: [
      { href: "/companies", label: "Companies", icon: Building2, shortcut: "G M" },
      { href: "/providers", label: "Providers", icon: Shield, shortcut: "G P" },
      { href: "/jobs", label: "Jobs", icon: Activity, shortcut: "G J" },
      { href: "/history", label: "History", icon: History, shortcut: "G H" },
    ],
  },
  {
    label: "Workspace",
    items: [
      { href: "/quality", label: "Quality", icon: ClipboardCheck, shortcut: "G Q" },
      { href: "/settings", label: "Settings", icon: Settings, shortcut: "G ," },
    ],
  },
];

export const ALL_NAV = NAV.flatMap((group) => group.items);

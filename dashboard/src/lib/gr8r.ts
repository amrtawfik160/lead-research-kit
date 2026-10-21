/** Visual tokens only (paper chrome, type, motion). Copy and nav names are Veinore's. */

export const GR8R = {
  light: {
    bg: "#fbfaf8",
    bgSide: "#f4f3f0",
    surface: "#ffffff",
    surface2: "#f6f5f2",
    surface3: "#eeede9",
    sunken: "#f7f6f3",
    border: "#e9e7e2",
    borderStrong: "#dad7d0",
    divider: "#f0eeea",
    text: "#1d1c1a",
    text2: "#57544e",
    text3: "#6b6760",
    text4: "#8c8880",
    accent: "#4b5bd6",
    onAccent: "#ffffff",
  },
  dark: {
    bg: "#1a1a19",
    bgSide: "#151514",
    surface: "#1d1d1c",
    surface2: "#252524",
    surface3: "#2d2d2b",
    sunken: "#181817",
    border: "#2c2b29",
    borderStrong: "#3a3936",
    divider: "#252523",
    text: "#eceae5",
    text2: "#b5b2ab",
    text3: "#9a968f",
    text4: "#6f6c66",
    accent: "#8e9af3",
    onAccent: "#12131c",
  },
  type: {
    font: "Geist",
    mono: "Geist Mono",
    xs: 11,
    sm: 12.5,
    base: 13.5,
    md: 14.5,
    xl: 19,
    display: 23,
  },
  size: {
    sidebar: 252,
    sideRow: 30,
    row: 40,
    thead: 32,
    group: 36,
    topbar: 46,
    gutter: 24,
    radius: 8,
    radiusSm: 6,
    radiusLg: 10,
    avatar: 22,
  },
  motion: {
    dur: "140ms",
    ease: "cubic-bezier(0.23, 1, 0.32, 1)",
  },
} as const;

export const LABEL_COLORS = [
  "#3570c4",
  "#7652c2",
  "#1e8580",
  "#bc4270",
  "#c0612b",
  "#8f5e0e",
  "#25754b",
  "#4b5bd6",
] as const;

export function issueKey(prefix: string, index: number): string {
  return `${prefix}-${String(index + 1).padStart(2, "0")}`;
}

export function labelColor(value: string): string {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
  return LABEL_COLORS[hash % LABEL_COLORS.length];
}

export function initials(value: string): string {
  const parts = value
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2);
  if (!parts.length) return "V";
  return parts.map((part) => part[0]?.toUpperCase() || "").join("");
}

export function greeting(now = new Date()): string {
  const hour = now.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

export function longDate(now = new Date()): string {
  return now.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
}

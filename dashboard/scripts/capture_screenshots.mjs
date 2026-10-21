#!/usr/bin/env node
/**
 * Viewport-only README screenshots (no OS chrome).
 * 1440x900 @ deviceScaleFactor 2, dark theme, sample workspace.
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

const origin = process.env.VEINORE_ORIGIN || "http://127.0.0.1:3000";
const outDir = resolve(process.argv[2] || "docs/screenshots");
mkdirSync(outDir, { recursive: true });

const freezeMotion = `
  *, *::before, *::after {
    animation-duration: 0s !important;
    animation-delay: 0s !important;
    transition-duration: 0s !important;
    caret-color: transparent !important;
  }
`;

function hasText(page, needle) {
  return page.waitForFunction(
    (want) => document.body.innerText.toLowerCase().includes(want),
    needle.toLowerCase(),
    { timeout: 20000 },
  );
}

async function shot(page, name) {
  const path = resolve(outDir, name);
  await page.screenshot({ path, type: "png", scale: "device", animations: "disabled" });
  console.log("wrote", path);
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
  colorScheme: "dark",
  reducedMotion: "reduce",
});
await context.addInitScript(() => {
  try {
    localStorage.setItem("veinore-theme", "dark");
  } catch {
    /* ignore */
  }
  const root = document.documentElement;
  if (root) {
    root.classList.add("dark");
    root.style.colorScheme = "dark";
  }
});
const page = await context.newPage();
await page.addStyleTag({ content: freezeMotion });

await page.goto(`${origin}/`, { waitUntil: "networkidle" });
await page.addStyleTag({ content: freezeMotion });
await hasText(page, "12 / 12");
await shot(page, "overview.png");

await page.goto(`${origin}/leads`, { waitUntil: "networkidle" });
await page.addStyleTag({ content: freezeMotion });
await hasText(page, "Jane Sample");
await shot(page, "leads-table.png");

const jane = page.getByText("Jane Sample", { exact: true }).first();
await jane.click();
await page.waitForSelector('[data-slot="sheet-popup"]');
await page.waitForTimeout(200);
await shot(page, "lead-detail.png");
await page.keyboard.press("Escape");
await page.waitForTimeout(150);

await page.goto(`${origin}/daily`, { waitUntil: "networkidle" });
await page.addStyleTag({ content: freezeMotion });
await hasText(page, "2026-10-20");
const reportTab = page.getByRole("tab", { name: /report/i });
if (await reportTab.count()) {
  await reportTab.click();
} else {
  await page.getByText("Report", { exact: true }).first().click();
}
await hasText(page, "email sources");
await shot(page, "daily-report.png");

await page.goto(`${origin}/providers`, { waitUntil: "networkidle" });
await page.addStyleTag({ content: freezeMotion });
await hasText(page, "hunter");
await shot(page, "providers.png");

await page.keyboard.press("Control+k");
await page.waitForSelector('[data-slot="command-dialog-popup"]');
await page.waitForTimeout(150);
await shot(page, "command-palette.png");

await browser.close();
console.log("done", outDir);

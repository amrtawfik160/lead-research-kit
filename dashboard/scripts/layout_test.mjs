#!/usr/bin/env node
/**
 * Layout contract for every Veinore route at 1280 and 1440.
 * Asserts page gutter, aligned chrome, no viewport overflow, no cell overlap.
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

const origin = process.env.VEINORE_ORIGIN || "http://127.0.0.1:3000";
const shotDir = process.env.VEINORE_SHOT_DIR || "";
const widths = [1280, 1440];
const routes = [
  "/",
  "/daily",
  "/leads",
  "/quality",
  "/contacts",
  "/research",
  "/companies",
  "/providers",
  "/budget",
  "/jobs",
  "/history",
  "/settings",
];

if (shotDir) mkdirSync(shotDir, { recursive: true });

function close(a, b, tol = 2) {
  return Math.abs(a - b) <= tol;
}

const measure = () => {
  const shell = document.querySelector('[data-slot="page-shell"]');
  const content = document.querySelector('[data-slot="page-content"]');
  const header = document.querySelector('[data-slot="page-header"]');
  const toolbar = document.querySelector('[data-slot="page-toolbar"]');
  const table = document.querySelector('[data-slot="page-table"]');
  const gap = document.querySelector('[data-slot="sidebar-gap"]');
  const inset = document.querySelector('[data-slot="sidebar-inset"]');
  if (!shell || !content || !gap || !inset) {
    return { error: "missing layout slots", found: { shell: !!shell, content: !!content, gap: !!gap, inset: !!inset } };
  }
  const gutter = parseFloat(getComputedStyle(shell).paddingLeft);
  const contentBox = content.getBoundingClientRect();
  const gapBox = gap.getBoundingClientRect();
  const insetBox = inset.getBoundingClientRect();
  const headerBox = header?.getBoundingClientRect() || null;
  const toolbarBox = toolbar?.getBoundingClientRect() || null;
  const tableBox = table?.getBoundingClientRect() || null;
  const overlaps = [];
  for (const row of document.querySelectorAll('[data-slot="table-row"]')) {
    const cells = [...row.querySelectorAll('[data-slot="table-cell"]')];
    for (let i = 0; i < cells.length; i += 1) {
      const cell = cells[i];
      const box = cell.getBoundingClientRect();
      if (i + 1 < cells.length) {
        const next = cells[i + 1].getBoundingClientRect();
        if (box.right > next.left + 1) {
          overlaps.push({
            kind: "cell-box",
            left: cell.textContent?.trim().slice(0, 48) || "",
            right: cells[i + 1].textContent?.trim().slice(0, 48) || "",
            gap: next.left - box.right,
          });
        }
        const leftChild = cell.firstElementChild?.getBoundingClientRect();
        const rightChild = cells[i + 1].firstElementChild?.getBoundingClientRect();
        if (
          leftChild &&
          rightChild &&
          leftChild.width > 0 &&
          rightChild.width > 0 &&
          rightChild.left - leftChild.right < 6
        ) {
          overlaps.push({
            kind: "cell-content-gap",
            left: cell.textContent?.trim().slice(0, 48) || "",
            right: cells[i + 1].textContent?.trim().slice(0, 48) || "",
            gap: rightChild.left - leftChild.right,
          });
        }
      }
      for (const node of cell.querySelectorAll("span, a, p, div, button")) {
        const text = (node.textContent || "").trim();
        if (!text) continue;
        const nodeBox = node.getBoundingClientRect();
        if (nodeBox.width < 1 || nodeBox.height < 1) continue;
        if (nodeBox.right > box.right + 1.5 || nodeBox.left < box.left - 1.5) {
          overlaps.push({
            kind: "text-clip",
            text: text.slice(0, 48),
            overflow: { left: box.left - nodeBox.left, right: nodeBox.right - box.right },
          });
        }
      }
    }
  }
  for (const headSpan of document.querySelectorAll('[data-slot="table-head"] button span')) {
    if (headSpan.scrollWidth > headSpan.clientWidth + 1) {
      overlaps.push({
        kind: "table-head-truncated",
        text: (headSpan.textContent || "").trim(),
      });
    }
  }
  for (const statusNode of document.querySelectorAll('[data-slot="quiet-status"]')) {
    const rawText = (statusNode.textContent || "").trim();
    if (/[a-z0-9]+_[a-z0-9]+/i.test(rawText)) {
      overlaps.push({
        kind: "raw-snake-case-status",
        text: rawText.slice(0, 48),
      });
    }
  }
  for (const titleNode of document.querySelectorAll(".issue-title")) {
    const deco = getComputedStyle(titleNode).textDecorationLine || "";
    if (deco.includes("line-through")) {
      overlaps.push({
        kind: "lead-title-strikethrough",
        text: (titleNode.textContent || "").trim().slice(0, 48),
      });
    }
  }
  return {
    gutter,
    contentLeft: contentBox.left,
    expectedFromGap: gapBox.right + gutter,
    expectedFromInset: insetBox.left + gutter,
    headerLeft: headerBox?.left ?? null,
    toolbarLeft: toolbarBox?.left ?? null,
    tableLeft: tableBox?.left ?? null,
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
    overlaps,
  };
};

const failures = [];

const browser = await chromium.launch({ headless: true });
try {
  for (const width of widths) {
    const context = await browser.newContext({
      viewport: { width, height: 900 },
      deviceScaleFactor: 1,
      colorScheme: "light",
      reducedMotion: "reduce",
    });
    await context.addInitScript(() => {
      try {
        localStorage.setItem("veinore-theme", "light");
      } catch {
        /* ignore */
      }
    });
    const page = await context.newPage();
    for (const route of routes) {
      const url = `${origin}${route}`;
      await page.goto(url, { waitUntil: "networkidle", timeout: 45000 });
      await page.waitForSelector('[data-slot="page-content"]', { timeout: 20000 });
      await page.waitForTimeout(150);
      const result = await page.evaluate(measure);
      const label = `${route} @${width}`;
      if (result.error) {
        failures.push(`${label}: ${result.error} ${JSON.stringify(result.found)}`);
        continue;
      }
      if (!close(result.contentLeft, result.expectedFromGap) && !close(result.contentLeft, result.expectedFromInset)) {
        failures.push(
          `${label}: content left ${result.contentLeft.toFixed(1)} != sidebar+gutter (gap ${result.expectedFromGap.toFixed(1)}, inset ${result.expectedFromInset.toFixed(1)}, gutter ${result.gutter})`,
        );
      }
      if (result.headerLeft != null && !close(result.headerLeft, result.contentLeft)) {
        failures.push(`${label}: header left ${result.headerLeft.toFixed(1)} != content ${result.contentLeft.toFixed(1)}`);
      }
      if (result.toolbarLeft != null && !close(result.toolbarLeft, result.contentLeft)) {
        failures.push(`${label}: toolbar left ${result.toolbarLeft.toFixed(1)} != content ${result.contentLeft.toFixed(1)}`);
      }
      if (result.tableLeft != null && !close(result.tableLeft, result.contentLeft)) {
        failures.push(`${label}: table left ${result.tableLeft.toFixed(1)} != content ${result.contentLeft.toFixed(1)}`);
      }
      if (result.scrollWidth > result.innerWidth + 1) {
        failures.push(`${label}: horizontal overflow ${result.scrollWidth} > ${result.innerWidth}`);
      }
      if (result.overlaps.length) {
        failures.push(`${label}: overlapping table text ${JSON.stringify(result.overlaps.slice(0, 4))}`);
      }
      if (shotDir && width === 1440) {
        const name = route === "/" ? "overview" : route.slice(1);
        await page.screenshot({
          path: resolve(shotDir, `${name}.png`),
          type: "png",
          scale: "device",
          animations: "disabled",
        });
      }
    }

    // Interactive state & tab checks at each width
    await page.goto(`${origin}/history`, { waitUntil: "networkidle", timeout: 45000 });
    for (const tabLabel of ["Suppressed", "Outcomes", "Patterns", "Sources", "Delivered"]) {
      const tab = page.getByRole("tab", { name: tabLabel });
      if (await tab.count()) {
        await tab.click();
        await page.waitForTimeout(80);
        const tabMeasure = await page.evaluate(measure);
        if (tabMeasure.overlaps?.length) {
          failures.push(`/history (${tabLabel}) @${width}: ${JSON.stringify(tabMeasure.overlaps.slice(0, 4))}`);
        }
      }
    }

    await page.goto(`${origin}/leads`, { waitUntil: "networkidle", timeout: 45000 });
    await page.keyboard.press("Tab");
    const hasFocus = await page.evaluate(() => Boolean(document.activeElement && document.activeElement !== document.body));
    if (!hasFocus) {
      failures.push(`/leads @${width}: keyboard Tab did not move focus`);
    }
    const firstLeadRow = page.locator('[data-slot="table-row"]').nth(1);
    if (await firstLeadRow.count()) {
      await firstLeadRow.click();
      await page.waitForTimeout(120);
      const stageTrigger = page.getByRole("combobox", { name: "Lead stage" });
      if (!(await stageTrigger.count())) {
        failures.push(`/leads @${width}: clicking lead row did not open LeadOpsPanel in DetailSheet`);
      }
      await page.keyboard.press("Escape");
      await page.waitForTimeout(80);
    }

    await context.close();
  }
} finally {
  await browser.close();
}

if (failures.length) {
  console.error(`layout test failed (${failures.length})\n${failures.join("\n")}`);
  process.exit(1);
}
console.log(`layout test ok (${routes.length} routes × ${widths.length} widths)`);

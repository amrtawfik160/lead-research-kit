#!/usr/bin/env python3
"""Fail if app pages/components invent their own gutter outside page-shell."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard" / "src"
PRIMITIVE = DASH / "components" / "page-shell.tsx"
PAGES = DASH / "components" / "pages"
APP = DASH / "app"
COMPONENTS = DASH / "components"

ARBITRARY = (
    "p-[",
    "m-[",
    "px-[",
    "py-[",
    "pt-[",
    "pb-[",
    "pl-[",
    "pr-[",
    "ps-[",
    "pe-[",
    "mx-[",
    "my-[",
    "ms-[",
    "me-[",
    "mt-[",
    "mb-[",
    "ml-[",
    "mr-[",
    "inset-[",
    "top-[",
    "right-[",
    "bottom-[",
    "left-[",
    "start-[",
    "end-[",
)
NEGATIVE_X = ("-mx-", "-ms-", "-me-", "-ml-", "-mr-")
PAGE_GUTTER = ("px-6", "mx-6", "-mx-")


def iter_targets() -> list[Path]:
    files: list[Path] = []
    if PAGES.is_dir():
        files.extend(sorted(PAGES.rglob("*.tsx")))
    if APP.is_dir():
        files.extend(sorted(APP.rglob("*.tsx")))
    if COMPONENTS.is_dir():
        for path in sorted(COMPONENTS.glob("*.tsx")):
            if path.resolve() != PRIMITIVE.resolve():
                files.append(path)
    return files


def line_hits(path: Path) -> list[str]:
    hits: list[str] = []
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(ROOT)
    in_pages = PAGES in path.parents or path.parent == PAGES
    for index, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("//"):
            continue
        for token in ARBITRARY:
            if token in line:
                hits.append(f"{rel}:{index}: arbitrary spacing {token} -> {line.strip()}")
        for token in NEGATIVE_X:
            if token in line:
                hits.append(f"{rel}:{index}: negative horizontal margin {token} -> {line.strip()}")
        if in_pages:
            for token in PAGE_GUTTER:
                if token in line:
                    hits.append(f"{rel}:{index}: page-owned gutter {token} -> {line.strip()}")
    return hits


def missing_shell() -> list[str]:
    missing: list[str] = []
    if not PAGES.is_dir():
        return ["dashboard/src/components/pages is missing"]
    for path in sorted(PAGES.glob("*-page.tsx")):
        text = path.read_text(encoding="utf-8")
        if "<PageShell" not in text:
            missing.append(f"{path.relative_to(ROOT)}: route page must render through <PageShell>")
    return missing


def primitive_guards() -> list[str]:
    findings: list[str] = []
    issue_list = COMPONENTS / "issue-list.tsx"
    if issue_list.is_file():
        text = issue_list.read_text(encoding="utf-8")
        if "line-through" in text:
            findings.append("dashboard/src/components/issue-list.tsx: must not strike through lead/contact titles with line-through")
        if "gap-x-" not in text:
            findings.append("dashboard/src/components/issue-list.tsx: row grid must include horizontal column gap (gap-x-*)")
        if 'data-slot="table-row"' not in text or 'data-slot="table-cell"' not in text:
            findings.append("dashboard/src/components/issue-list.tsx: must expose data-slot=\"table-row\" and data-slot=\"table-cell\" for layout_test.mjs")

    data_table = COMPONENTS / "data-table.tsx"
    if data_table.is_file():
        text = data_table.read_text(encoding="utf-8")
        if "<MenuTrigger" not in text:
            findings.append("dashboard/src/components/data-table.tsx: toolbar Filter/Sort buttons must use interactive <MenuTrigger>")

    status_glyph = COMPONENTS / "status-glyph.tsx"
    if status_glyph.is_file():
        text = status_glyph.read_text(encoding="utf-8")
        if "humanName(" not in text or "<CellText" not in text:
            findings.append("dashboard/src/components/status-glyph.tsx: QuietStatus must format status tokens with humanName() and wrap text in <CellText>")

    return findings


def main() -> int:
    if not PRIMITIVE.is_file():
        print("page-shell primitive is missing", file=sys.stderr)
        return 1
    findings = missing_shell()
    findings.extend(primitive_guards())
    for path in iter_targets():
        findings.extend(line_hits(path))
    if findings:
        print("dashboard layout guard failed:\n" + "\n".join(findings), file=sys.stderr)
        return 1
    print(f"dashboard layout guard ok ({len(iter_targets())} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

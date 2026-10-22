# Agent Guide (`AGENTS.md`)

Veinore (`lead-research-kit`) is an evidence-first B2B lead research toolkit consisting of:
1. **Python CLI (`lead_research`)** — zero runtime dependencies (Python 3.11+ standard library only). Finds, cleans, scores, crawls, and verifies B2B decision-maker contacts and work emails into a standardized 6-column CSV (`Full Name`, `Title`, `Company`, `Company Website`, `LinkedIn URL`, `Email`) with hash-chained SQLite audit logging, explicit free-budget accounting, and dry-first execution.
2. **Loopback Operator Dashboard (`dashboard/`)** — Next.js 16 (App Router, React 19, TypeScript, Tailwind v4, coss ui on Base UI, Recharts) paired with a loopback-only Python HTTP API (`127.0.0.1:8787`).

- For detailed lead-research operational workflows, read `skills/lead-research/SKILL.md`.
- Keep network work **dry by default** (`--execute` is required for live fetches or budget spend) and use synthetic offline fixtures for tests.
- Preserve exact budget accounting, source labels, holds, and failure history when changing behavior.
- Keep account data, filled `.env` files, and contact workspaces outside this source tree; run `python3 scripts/release_check.py .` before committing or packaging.
- **Works with zero API keys**: all CLI commands, daily previews, synthetic demos, and the full dashboard work out-of-the-box with no environment variables set.

---

## 1. Repository Layout

```text
.
├── AGENTS.md                        # This guide for AI coding agents & operators
├── Makefile                         # One-command entrypoints (setup, dev, test, check, run-daily, ...)
├── .env.example                     # Environment variable NAMES only (values must stay empty)
├── pyproject.toml                   # Python package metadata (Python >= 3.11, zero dependencies)
├── src/lead_research/               # Core Python package
│   ├── cli.py                       # CLI parser & JSON-first command dispatcher (`python3 -m lead_research`)
│   ├── team.py                      # Offline team roster, territories, and shared lead-list bundle
│   ├── filters.py                   # Named, pinned lead filters (`filter save/list/pin/apply`)
│   ├── daily.py                     # Daily fresh-lead engine, History SQLite store, market rotation
│   ├── store.py                     # Hash-chained SQLite ledger (`research.sqlite3`), budgets, attempts, holds
│   ├── runner.py                    # Serial dry-first job planner/runner & GET-only pending job polling
│   ├── providers.py                 # 41 stateless provider adapters (search, discover, crawl, find, verify, signals)
│   ├── records.py                   # Free public-record readers (TX TREC, NY DOS, CO DORA, CT DLS, SEC Form D, FL DBPR)
│   ├── extract.py / webfetch.py     # Polite robots.txt-respecting website crawler & people/email extractor
│   ├── quality.py / emailcheck.py   # 6-column lead cleaner, title classifier, tier scorer, MX/risk analyzer
│   └── configuration.py / policy.py # Built-in provider pool charges & strict input validation
├── dashboard/                       # Next.js 16 + coss ui operator dashboard
│   ├── server.py                    # Loopback-only Python HTTP API server (binds 127.0.0.1:8787)
│   ├── api/snapshot.py              # Workspace snapshot reader & safe local mutations (hold, stage/owner/notes, enable)
│   ├── api/sample.py                # Deterministic synthetic workspace seeder (`example.org` / `.example`)
│   ├── src/app/                     # Next.js App Router pages (`/`, `/daily`, `/leads`, `/quality`, `/contacts`, ...)
│   ├── src/components/              # Shared UI primitives (`page-shell.tsx`, `data-table.tsx`, `issue-list.tsx`, ...)
│   ├── src/components/pages/        # Route page views (`overview-page.tsx`, `daily-page.tsx`, `leads-page.tsx`, ...)
│   ├── src/components/ui/           # Unmodified coss ui / Base UI primitives
│   └── scripts/layout_test.mjs      # Playwright layout contract across all 12 routes at 1280px & 1440px
├── scripts/
│   ├── lead-research                # CLI wrapper script (sets PYTHONPATH=src automatically)
│   ├── check_dashboard_layout.py    # Static AST/source guard for PageShell, gutters, DataTable, IssueList, StatusGlyph
│   └── release_check.py             # Public source-tree guardrail (blocks secrets, non-example emails, SQLite/CSV leaks)
├── skills/lead-research/            # Agent skill instructions & provider/recovery references
└── tests/                           # Offline Python unittest suite (`test_*.py`)
```

---

## 2. One-Command Setup & Entrypoints (`Makefile`)

Run from the repository root (requires Python 3.11+ and Node.js 20+):

```bash
make setup
```

`make setup` installs `dashboard/` npm dependencies and seeds the deterministic synthetic sample workspace in `dashboard/.data/sample-workspace/` (gitignored). No API keys are needed.

### Summary of `make` Targets

| Command | What it does |
|---|---|
| `make setup` | Install `dashboard/` npm packages and seed `dashboard/.data/sample-workspace` |
| `make dev` | Start both the Python API (`127.0.0.1:8787`) and Next.js dev UI (`127.0.0.1:3000`) |
| `make api` | Start only the loopback Python API server on `http://127.0.0.1:8787` |
| `make web` | Start only the Next.js dev server on `http://127.0.0.1:3000` |
| `make build` | Build the Next.js production bundle (`cd dashboard && npm run build`) |
| `make start` | Start the production Python API (`:8787`) and Next.js server (`:3000`) |
| `make run-daily` | Run a dry-run daily lead preview (`WORKSPACE=...`, `TARGET=20`, `EXECUTE=0`) |
| `make run-daily-execute` | Run an executed daily lead batch (`WORKSPACE=...`, `TARGET=20`, `EXECUTE=1`) |
| `make status` | Print workspace status & provider states as JSON |
| `make providers` | Print all 41 providers with required/optional env var names & key presence as JSON |
| `make demo` | Run an offline synthetic end-to-end demo in a temporary directory |
| `make team-list` | Print the conjoined team list as JSON (`WORKSPACE=...`) |
| `make filters` | Print saved / pinned filters as JSON (`WORKSPACE=...`) |
| `make test` | Run the offline Python unit test suite (`PYTHONPATH=src python3 -m unittest discover -s tests -v`) |
| `make layout-check` | Run `python3 scripts/check_dashboard_layout.py` |
| `make release-check` | Run `python3 scripts/release_check.py .` |
| `make layout-test` | Run Playwright layout tests (`cd dashboard && npm run layout:test`; requires running server) |
| `make check` | Run `test`, `layout-check`, `release-check`, and `build` |

Equivalent npm scripts are also available inside `dashboard/` (`npm run dev`, `npm run api`, `npm run web`, `npm run build`, `npm start`, `npm test`, `npm run layout:check`, `npm run layout:test`, `npm run release:check`, `npm run check`).

---

## 3. Running the API (`127.0.0.1:8787`) and Dashboard (`127.0.0.1:3000`)

Both servers bind **loopback only** (`127.0.0.1`, `localhost`, or `::1`). `dashboard/server.py` refuses `0.0.0.0` or external interfaces.

### Start Both Together (Development)
```bash
make dev
# Or: cd dashboard && npm run dev
```

### Start API and UI Separately
```bash
# Terminal 1 — Loopback Python API on 127.0.0.1:8787
make api
# Or: cd dashboard && python3 server.py --host 127.0.0.1 --port 8787

# Terminal 2 — Next.js UI on 127.0.0.1:3000 (proxies /api/* to 127.0.0.1:8787)
make web
# Or: cd dashboard && npx next dev --port 3000
```

### Verify Health
```bash
curl -sf http://127.0.0.1:8787/api/health
curl -sf http://127.0.0.1:3000/ >/dev/null && echo "UI ready"
```

### Workspace Selection
- By default, the dashboard runs in **Sample Mode** (`dashboard/.data/sample-workspace/`), seeded automatically with synthetic `.example` / `example.org` data.
- To point the dashboard at a private workspace directory outside the repo:
  ```bash
  LEAD_RESEARCH_WORKSPACE=/tmp/my-private-ws make dev
  ```
  or switch workspaces in the UI at `/settings`.

---

## 4. CLI Usage & Running a Daily Lead Run (Dry Run vs `--execute`)

Every CLI command emits **machine-readable JSON** to `stdout` (and structured `{"error": "<code>", "hint": "..."}` JSON to `stderr` with exit code `2` on policy/budget/provider errors). The `--json` flag is supported on all commands.

You can invoke the CLI via `scripts/lead-research <cmd>`, `PYTHONPATH=src python3 -m lead_research <cmd>`, or the `make` targets.

### Initialize a Private Workspace (Outside the Repo)
```bash
scripts/lead-research --workspace /tmp/private-ws init --json
scripts/lead-research --workspace /tmp/private-ws status --json
```

### Daily Lead Run: Dry-Run Preview (Default — Zero Spend, Zero Keys Needed)
Without `--execute`, `daily` plans the run, inspects existing cached crawls/prospects in the workspace, writes a preview to `<workspace>/daily/<date>-preview/`, and **never** makes live network requests, spends budgets, or marks leads delivered:

```bash
# Using make (defaults to sample workspace, TARGET=20):
make run-daily TARGET=20

# Or against any private workspace:
make run-daily WORKSPACE=/tmp/private-ws TARGET=20

# Or directly via CLI:
scripts/lead-research --workspace /tmp/private-ws daily --target 20 --json
```

### Daily Lead Run: Live / Local Execution (`--execute`)
With `--execute`, `daily` applies due recurring free-budget refills, polls pending GET jobs, pulls enabled public records/discovery sources (skipping any provider that is disabled, unbudgeted, or missing its API key with a clear reason in `report.json`), crawls queued domains, verifies candidate emails within confirmed budgets, writes `<workspace>/daily/<date>/leads.csv`, records delivered keys in `lead-history.sqlite3`, and backs up SQLite databases:

```bash
# Execute using only local workspace data (no network/provider calls, works with 0 keys):
scripts/lead-research --workspace /tmp/private-ws daily --target 20 --sources crawl --record-sources "" --execute --json

# Full daily execute (uses any enabled + budgeted providers whose keys are in env):
make run-daily-execute WORKSPACE=/tmp/private-ws TARGET=20
# Or:
scripts/lead-research --workspace /tmp/private-ws daily --target 20 --execute --json
```

- **Same-day re-execute**: running `daily --execute` a second time on the same date **merges and dedupes** into that day's `leads.csv` (archiving the prior report under `daily/<date>/runs/`) without replaying spent requests.
- **Spot-check audit**:
  ```bash
  scripts/lead-research --workspace /tmp/private-ws daily audit --sample 20 --json
  ```

---

## 5. Provider & Runtime Environment Variable Names (Names Only — Never Commit Values)

All 41 providers start **disabled** with **zero budget**. Running with **no keys set** never crashes: unconfigured steps report `"missing_auth"` or `"provider_disabled"` in `skipped_reasons`.

To inspect live key presence in JSON at any time:
```bash
make providers
# Or: scripts/lead-research --workspace /tmp/private-ws provider list --json
```

### Runtime & Public-Record Variables (All Optional unless noted)

| Variable Name | Required? | Purpose |
|---|---|---|
| `LEAD_RESEARCH_WORKSPACE` | Optional | Path to private workspace directory for the dashboard/API |
| `LEAD_RESEARCH_SAMPLE` | Optional | `"1"` / `"true"` to force sample mode, `"0"` / `"false"` to disable |
| `LEAD_RESEARCH_DNS_SERVERS` | Optional | Comma-separated DNS server IPs for MX checks |
| `DASHBOARD_API_ORIGIN` | Optional | Loopback API origin for Next.js SSR fetches (default `http://127.0.0.1:8787`) |
| `SEC_USER_AGENT` | Conditional | Required **only** when fetching `sec-form-d` public records (format: `"Your Name you@example.com"`) |
| `SOCRATA_APP_TOKEN` | Optional | Raises rate limits on Socrata public-record portals (`tx-trec`, `ny-dos`, `co-dora`, `ct-dls`); works without it |
| `DEEPLINE_FIND_ROUTE` | Optional | Deepline find route: `"finder"` (default) or `"waterfall"` |

### Keyless Providers (Work with No API Key)

| Provider ID | Operations | Required Env Vars | Optional Env Vars |
|---|---|---|---|
| `overpass` | `places` (OpenStreetMap offices) | *(none)* | *(none)* |
| `jina` | `read` (Jina Reader fallback) | *(none)* | `JINA_API_KEY` (optional, higher rate limit) |
| `hackernews` | `watch` (Algolia HN signals) | *(none)* | *(none)* |
| `hunter-insight` | `signals` | *(none)* | *(none)* |
| `disify` | `signals` | *(none)* | *(none)* |

### Search, Discovery, Crawl & Enrichment Providers (Required Only When Enabled)

| Provider ID | Operations | Required Env Var Name(s) |
|---|---|---|
| `serper` | `search`, `search_batch` | `SERPER_API_KEY` |
| `brave` | `search` | `BRAVE_API_KEY` |
| `tavily` | `search` | `TAVILY_API_KEY` |
| `exa` | `search`, `company_search` | `EXA_API_KEY` |
| `zenserp` | `search` | `ZENSERP_API_KEY` |
| `linkup` | `search` | `LINKUP_API_KEY` |
| `youcom` | `search` | `YDC_API_KEY` |
| `serpapi` | `search` | `SERPAPI_API_KEY` |
| `firecrawl` | `search`, `scrape` | `FIRECRAWL_API_KEY` |
| `hunter` | `find`, `verify`, `discover`, `domain_search` | `HUNTER_API_KEY` |
| `google_places` | `text_search` | `GOOGLE_MAPS_API_KEY` |
| `apollo` | `people_search`, `people_enrich` | `APOLLO_API_KEY` |
| `prospeo` | `people_search`, `enrich` | `PROSPEO_API_KEY` |
| `pdl` | `person_enrich` | `PDL_API_KEY` |
| `apify` | `places`, `contacts` | `APIFY_API_TOKEN` |

### Email Finder Providers (Required Only When Enabled)

| Provider ID | Operations | Required Env Var Name(s) | Notes |
|---|---|---|---|
| `zerobounce` | `find`, `verify` | `ZEROBOUNCE_API_KEY` | Separate `finder_queries` and `validation_credits` pools |
| `tomba` | `find`, `linkedin_email` | `TOMBA_API_KEY`, `TOMBA_SECRET` | Both variables required |
| `icypeas` | `find` | `ICYPEAS_API_KEY` | Supports async GET polling via `resume` |
| `deepline` | `find` | `DEEPLINE_API_KEY` | Optional: `DEEPLINE_FIND_ROUTE` (`finder` or `waterfall`) |
| `enrow` | `find` | `ENROW_API_KEY` | Supports async GET polling via `resume` |
| `lusha` | `find` | `LUSHA_API_KEY` | Paid plans only |
| `skrapp` | `find`, `company_search` | `SKRAPP_API_KEY` | — |

### Email Verification & Signal Providers (Required Only When Enabled)

| Provider ID | Operations | Required Env Var Name(s) | Notes |
|---|---|---|---|
| `reoon` | `verify` | `REOON_API_KEY` | — |
| `verifalia` | `verify` | `VERIFALIA_TOKEN` **or** (`VERIFALIA_USERNAME` + `VERIFALIA_PASSWORD`) | Either bearer token or basic auth pair |
| `clearout` | `verify` | `CLEAROUT_API_KEY` | — |
| `millionverifier` | `verify` | `MILLIONVERIFIER_API_KEY` | — |
| `abstract` | `verify` | `ABSTRACT_EMAIL_API_KEY` | — |
| `emailable` | `verify` | `EMAILABLE_API_KEY` | — |
| `kickbox` | `verify` | `KICKBOX_API_KEY` | — |
| `bouncer` | `verify` | `BOUNCER_API_KEY` | — |
| `myemailverifier` | `verify` | `MYEMAILVERIFIER_API_KEY` | — |
| `quickemailverification` | `verify` | `QUICKEMAILVERIFICATION_API_KEY` | — |
| `emailvalidation` | `verify` | `EMAILVALIDATION_API_KEY` | — |
| `emaillistverify` | `verify` | `EMAILLISTVERIFY_API_KEY` | — |
| `producthunt` | `watch` | `PRODUCTHUNT_TOKEN` | Signal only |
| `github` | `watch` | `GITHUB_TOKEN` | Signal only |

---

## 6. How to Run Every Check

Before finishing any task or opening a PR, run all verification checks:

1. **All static + unit + build checks in one command**:
   ```bash
   make check
   ```
   Or individually:
2. **Python Unit Tests (offline, synthetic fixtures only)**:
   ```bash
   make test
   # Or: PYTHONPATH=src python3 -m unittest discover -s tests -v
   ```
3. **Dashboard Static Layout & Primitive Guard**:
   ```bash
   make layout-check
   # Or: python3 scripts/check_dashboard_layout.py
   ```
4. **Public Release & Secret Guardrail Scan**:
   ```bash
   make release-check
   # Or: python3 scripts/release_check.py .
   ```
5. **Next.js Production Build & TypeScript Check**:
   ```bash
   make build
   # Or: cd dashboard && npm run build
   ```
6. **Playwright Live Layout Contract (tests all 12 routes at `1280x900` and `1440x900`)**:
   Ensure the dashboard is running (`make start` or `make dev`), then run:
   ```bash
   make layout-test
   # Or: cd dashboard && npm run layout:test
   ```

---

## 7. Dashboard UI/UX Rules & System-Level Guards

When modifying anything under `dashboard/src/`:

1. **Always use `<PageShell>` (`dashboard/src/components/page-shell.tsx`)**:
   - Every route view in `dashboard/src/components/pages/*-page.tsx` **must** render inside `<PageShell>` (with `<PageHeader>` and `<PageBody>`).
   - Never invent page-level horizontal gutters (`px-6`, `mx-6`, `-mx-*`) or arbitrary bracket spacing (`p-[...]`, `m-[...]`, `inset-[...]`, etc.). `scripts/check_dashboard_layout.py` enforces this.
2. **Use shared `<DataTable>` (`dashboard/src/components/data-table.tsx`) and `<IssueList>` (`dashboard/src/components/issue-list.tsx`)**:
   - `<DataTable>` must keep interactive `<MenuTrigger>` dropdowns for `Filter` and `Sort`, column grouping, and `exportFilename` CSV export.
   - `<IssueList>` rows and cells must expose `data-slot="table-row"` and `data-slot="table-cell"`, use horizontal column gaps (`gap-x-*`), wrap cell labels in `<CellText>`, and **never** apply `line-through` to lead or contact titles.
   - Ensure table column widths accommodate header titles without truncation (`table-head-truncated` check in `layout_test.mjs`) and maintain at least `6px` horizontal gap between adjacent cell contents (`cell-content-gap` check in `layout_test.mjs`).
3. **Use `<QuietStatus>` & `humanName()` (`dashboard/src/components/status-glyph.tsx`, `dashboard/src/lib/format.ts`)**:
   - Never leak raw `snake_case` status or source tokens (`website_published`, `provider_valid`, `public_web`, etc.) into the UI. Format labels through `humanName()` and wrap text in `<CellText>`.
4. **Use coss ui on Base UI (`dashboard/src/components/ui/`) as-is**:
   - Do not override coss design tokens, fonts, or base sizing. Compose pages from the existing primitives (`Button`, `Badge`, `Card`, `Dialog`, `Menu`, `Select`, `Sheet`, `Tabs`, `Table`, `Tooltip`).
5. **Charts (`dashboard/src/components/workspace-charts.tsx`)**:
   - Keep Recharts compact with clear axis labels (`interval={0}` on categorical `<YAxis>`), hover tooltips (`ChartTooltip`), and sort quota/budget pools by active spend first so spent pools are never sliced off.
6. **Lead-generation domain terminology only**:
   - Navigation and labels must strictly fit lead generation (`Overview`, `Daily`, `Leads`, `Quality`, `Contacts`, `Research`, `Companies`, `Providers`, `Budget`, `Jobs`, `History`, `Settings`).

---

## 8. Set up Veinore with your own agent (plain words)

Use this when a non-technical teammate pastes the README prompt or says “clone, install and run https://github.com/amrtawfik160/lead-research-kit”. Guide them. Do not dump jargon. Stop and ask when you need a choice or a secret. Never print, log, or commit API keys.

1. **Clone and install**
   ```bash
   git clone https://github.com/amrtawfik160/lead-research-kit.git
   cd lead-research-kit
   make setup
   ```
   `make setup` only installs the dashboard packages and a fake sample workspace. No keys needed.

2. **Make a private work folder** outside this repo. Example: `/tmp/veinore-$USER` or a folder in their Documents. Then:
   ```bash
   scripts/lead-research --workspace /private/their-folder init --json
   ```

3. **Ask which area they own** so two people do not cover the same slice. One of: `industry`, `region`, `source`, `icp`. Write it down. Examples: `Texas`, `commercial real estate`, `public records`, `SaaS founders`.

4. **Join the team** (this is how lists stay one list; there is no website to log into):
   ```bash
   scripts/lead-research --workspace /private/their-folder team join \
     --name "Their Name" --area-kind region --area "Texas" \
     --shared /path/to/shared-folder-or-veinore-team.json --json
   ```
   If that area is taken, the command exits `2` with `area_already_claimed`. Ask them to pick another slice. Then `team list --json`.

5. **Keys (optional).** The CLI and dashboard work with zero keys. If they have keys, load them into the process environment only (`set -a; . /private/keys.env; set +a` or `--env-file` with mode `0600`). Never put values in git, screenshots, or chat. `provider list --json` shows names and whether a key is present, not the value.

6. **First daily run.** Preview, then execute only if they say yes:
   ```bash
   scripts/lead-research --workspace /private/their-folder daily --target 10 --json
   scripts/lead-research --workspace /private/their-folder daily --target 10 --execute --json
   ```
   A second `--execute` the same day merges. Leads already found by a teammate are skipped (`skipped_team_owned`).

7. **Share finds into the team list**
   ```bash
   scripts/lead-research --workspace /private/their-folder team export --shared /path/to/shared-folder --json
   scripts/lead-research --workspace /private/their-folder team import --shared /path/to/shared-folder --json
   ```
   Same as `team merge`. Dedupe is email, LinkedIn, or name + company website. `Found By` stays with the first person who had the lead.

8. **Save and pin filters** they will reuse, including ICP targeting:
   ```bash
   scripts/lead-research --workspace /private/their-folder filter save today-new --date 2026-10-22 --stage new --pin --json
   scripts/lead-research --workspace /private/their-folder filter list --json
   scripts/lead-research --workspace /private/their-folder filter apply today-new --json
   ```
   Pinned filters also show at the top of the Leads page. The UI is optional.

9. **Stage, owner, notes, export** from the CLI (same sidecar the dashboard shows):
   ```bash
   scripts/lead-research --workspace /private/their-folder ops set --email casey@harbor.example --stage contacted --owner Ada --notes "Intro this week" --json
   scripts/lead-research --workspace /private/their-folder leads export --output /private/their-folder/leads.csv --json
   ```

10. **Dashboard (optional).** `make dev` then open http://127.0.0.1:3000. Settings shows the roster. Leads has a “Team list” date and the pinned chips. Do not use the UI to join, export, or spend.

### Example: Jess’s US CRE $5M+ filter (paste to your agent)

```text
Save and pin a filter named "US CRE decision makers 5M". Target US commercial real estate decision makers with at least $5M revenue or ARR.

Industry keywords: real estate development, real estate, commercial real estate, property management, construction.

Include titles: CEO, COO, CFO, President, Partner, Managing Partner, Managing Director, VP of Strategy, President of Strategy, Founder, Owner, Principal, EVP, SVP. Exclude junior roles.

Use that filter as daily targeting (`daily --filter "US CRE decision makers 5M"`). Preview first. Execute only if I say so. Do everything from the CLI with --json. The dashboard is optional.
```

```bash
scripts/lead-research --workspace /private/their-folder filter save "US CRE decision makers 5M" \
  --country US --revenue-min 5M \
  --industry "real estate development,real estate,commercial real estate,property management,construction" \
  --include-titles "CEO,COO,CFO,President,Partner,Managing Partner,Managing Director,VP of Strategy,President of Strategy,Founder,Owner,Principal,EVP,SVP" \
  --exclude-junior --pin --json
scripts/lead-research --workspace /private/their-folder daily --target 20 --filter "US CRE decision makers 5M" --json
```

Unknown revenue or country is left unknown (not treated as a match or a miss). A known value below $5M or outside the US is dropped.

---

## 9. Team lists and saved filters (CLI first)

Sync is a JSON bundle (`veinore-team.json`). No hosted server. Put the file in a shared folder or a private git repo.

| Command | What it does |
|---|---|
| `team join --name NAME --area-kind industry\|region\|source\|icp --area SLICE [--shared PATH]` | Create or join a team and claim one area. Duplicate areas are blocked. |
| `team export [--output FILE] [--shared PATH]` | Write the merged list with who found each lead. Merges an existing shared file so you do not wipe teammates. |
| `team import [FILE] [--shared PATH]` | Merge a bundle: dedupe, keep first finder, skip those leads on your next daily run. |
| `team merge` | Same as `import`. |
| `team list --json` | Members, areas, and the conjoined list. |
| `filter save NAME [--stage --owner --area --source --risk --date --verified --found-by] [--country --revenue-min --industry --include-titles --exclude-titles --exclude-junior] [--pin]` | Create or update a named filter, including ICP targeting. |
| `filter list --json` | Saved filters; pinned first. |
| `filter pin NAME` / `filter unpin NAME` | Pin or unpin. |
| `filter apply NAME --json` | Matching leads. |
| `ops set --email EMAIL [--stage --owner --notes]` | Lead stage, owner, notes (dashboard Outreach panel). |
| `ops get --email EMAIL` / `ops list` | Read one or all outreach rows. |
| `leads list [--date] [--filter NAME]` | JSON leads. |
| `leads export --output FILE.csv [--date] [--filter NAME]` | Same CSV columns as the dashboard Export button. |
| `daily --filter NAME` | Use a saved filter (ICP included) when picking today's leads. |
| `workspace show` | Path, init, team, pins, latest daily. |
| `schema` / `commands` | Every command, flags, output shape, exit codes. |
| `jobs list` | Ledger job attempts. |

`--json` is accepted on every command. `--help` is on every command. Exit `0` is OK; exit `2` is a usage/policy/provider/budget/audit error with `{"error","hint"}` on stderr. Workspaces and filled env files stay outside the repo.

The dashboard never does anything the CLI cannot. Workspace switch in Settings is `--workspace` on the next command. Sample reset is `demo`. Run locally is `daily` / `daily --execute`.

---

## 10. Full command reference (agent catalog)

Run `scripts/lead-research schema --json` (same as `commands --json`) for the live list. Top-level commands:

`init`, `import`, `evidence`, `hold`, `research-queue`, `generate`, `verify-queue`, `find-queue`, `run`, `resume`, `budget` (`list`/`confirm`/`sync`), `provider` (`list`/`enable`/`disable`/`pause`/`review`), `cancel-unsent`, `export`, `status`, `audit`, `clean`, `crawl`, `jobs` (`list`/`xray`/`hunter-discover`/`hunter-domains`/`osm`), `collect`, `build`, `daily`, `daily-audit`, `records`, `history`, `demo`, `team`, `filter`, `ops`, `leads`, `workspace`, `schema`, `commands`.

Non-interactive: never prompt. `--execute` is required to spend or write a live daily file. Dry is the default.

<p>
  <img src="public/brand/logo-light.svg" alt="Veinore" width="168" height="32" />
</p>

# Veinore

The local operator dashboard for the Lead Research Kit. It reads a private workspace through the kit’s own `Store`, `History`, `quality`, `intake`, and daily report APIs. Sample mode seeds reserved `example.org` / `.example` data so you can run the UI without a live workspace.

This is a local dashboard. It does not send email, scrape private profiles, or store API keys.

## Screens

| Route | What it shows |
|---|---|
| `/` Overview | Latest daily delivery vs target, audit chain, backlog, unresolved attempts, attested budgets, shortfall advice |
| `/daily` | Dated `daily/<date>` batches, leads table, `report.md`, optional `audit.json` |
| `/leads` | Latest delivered lead file (6 clean columns plus city, type, source, confidence, risk) |
| `/contacts` | Workspace contacts, evidence, candidates, hold (calls `Store.hold_contact`) |
| `/research` | `research_queue`, evidence rows, email candidates |
| `/companies` | Domain backlog, crawls, public-record firms, prospects |
| `/providers` | Enable flags, pause state, env-key presence (never the value), attested pools |
| `/jobs` | Ledger attempts (resume stays GET-only on the CLI) |
| `/history` | Delivered keys, suppressions, outcomes, distrusted patterns, source quality |
| `/quality` | Offline `clean_records` on a pasted CSV/JSON |
| `/settings` | Sample mode or a real workspace path |

Keyboard: `⌘K` command palette, `⌘B` sidebar, `G` then a letter to jump (`G O` overview, `G L` leads, `G C` contacts, …).

## Run

From the repository root (Python 3.11+, Node 20+):

```bash
cd dashboard
npm install
npm run dev
```

Open http://127.0.0.1:3000. `npm run dev` starts the Python API on loopback `:8787` and Next.js on `:3000`. The first request in sample mode writes `dashboard/.data/sample-workspace/` (gitignored). Pages read the kit on the server so the first HTML already has workspace rows; the browser then refreshes over `/api/*`.

Use your own workspace:

```bash
LEAD_RESEARCH_WORKSPACE=/private/path/research npm run dev
```

Or switch in Settings. Providers stay disabled until you enable them here or with the CLI; budgets still require `budget confirm` on the CLI. Keys stay in the environment — the UI only reports whether the named variable is present.

Production-style local serve after `npm run build`:

```bash
npm start
```

## Architecture

- `server.py` — loopback JSON API. Imports `lead_research` with `PYTHONPATH` set to `src/`.
- `api/snapshot.py` — workspace reads and the few safe writes (hold, provider enable, settings).
- `api/sample.py` — seeds a synthetic workspace with the same functions the CLI uses.
- `src/` — Next.js App Router + [coss ui](https://coss.com/ui) components (`npx shadcn@latest init @coss/style`).

Do not commit `.data/`, `node_modules/`, `.next/`, or any workspace SQLite/CSV.

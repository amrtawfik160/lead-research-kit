<p align="center">
  <img src="dashboard/public/brand/logo-light.svg" alt="Veinore" width="200" height="38" />
</p>

# Veinore

Evidence-first lead research: a Python CLI and local dashboard that finds B2B contacts and work emails without mixing guesses, public sources, and provider claims.

**v0.5.0 alpha** · Python 3.11+ · zero runtime dependencies · loopback-only UI · [MIT](LICENSE)

Synthetic `example.org` / `.example` fixtures only. No contact database, API keys, or live account state ships in this repository.

## Screenshots

Synthetic sample workspace (light theme, paper chrome). No real people or keys.

| Overview | Leads |
|---|---|
| ![Overview](docs/screenshots/overview.png) | ![Leads](docs/screenshots/leads-table.png) |

| Lead detail | Daily |
|---|---|
| ![Lead detail](docs/screenshots/lead-detail.png) | ![Daily](docs/screenshots/daily-report.png) |

| Providers | Command palette |
|---|---|
| ![Providers](docs/screenshots/providers.png) | ![Command palette](docs/screenshots/command-palette.png) |

## Features

- **Daily fresh leads** — public records → resolve → crawl → find → verify → 6-column CSV, never repeating delivered people
- **Evidence first** — source excerpts, person-match attestations, holds; guesses stay candidates until verified
- **Exact budgets** — providers start disabled and zero; `budget confirm` is operator attestation of free allowance
- **Dry by default** — network commands plan unless `--execute`; pauses stay until an explicit review
- **Veinore dashboard** — local workspace UI (coss ui) over the same Store / History / daily files; keys never leave the process environment

It does **not** send email, probe SMTP, scrape private profiles, rotate identities, or prove ownership or consent.

## Quick start

### CLI

```bash
PYTHONPATH=src python3 -m lead_research --help
PYTHONPATH=src python3 -m lead_research --workspace /private/ws init
PYTHONPATH=src python3 -m lead_research --workspace /private/ws demo --output /tmp/veinore-demo
```

Or install editable: `python -m pip install -e .` then use `lead-research`.

Daily loop:

```bash
lead-research --workspace /private/ws history import old-leads.csv
lead-research --workspace /private/ws daily --target 10                 # preview
lead-research --workspace /private/ws daily --target 10 --execute       # deliver
lead-research --workspace /private/ws daily audit --sample 20
```

A second `--execute` on the same date **merges** into that day’s `leads.csv` (dedupe) instead of overwriting. Preview folders stay separate.

Keys stay in the environment (names in [`.env.example`](.env.example)). Enable and attest before spend:

```bash
set -a; . /private/path/keys.env; set +a
lead-research --workspace /private/ws provider enable reoon
lead-research --workspace /private/ws budget confirm reoon \
  --pools '{"credits":"20"}' --source 'Own free plan' --free
```

### Dashboard

```bash
cd dashboard
npm install
npm run dev          # API :8787 + UI :3000, loopback only
```

Open http://127.0.0.1:3000. Sample mode seeds reserved example data on first load. Point Settings (or `LEAD_RESEARCH_WORKSPACE`) at a private workspace for live work.

Production-style local serve:

```bash
set -a; . /private/path/keys.env; set +a
cd dashboard && npm run build && npm start
```

Daily execute, budget confirm, and `resume --execute` stay on the CLI. See [`dashboard/README.md`](dashboard/README.md).

## Give this to your agent

Veinore is built so an AI agent can do the work. The dashboard is optional tracking. If you are not technical, copy the box below into your own agent (Cursor, Claude, ChatGPT, etc.).

```text
Clone, install, and run https://github.com/amrtawfik160/lead-research-kit

I am not technical. Follow AGENTS.md section "Set up Veinore with your own agent" and this README. Do everything from the CLI with --json. The dashboard is optional.

1. Clone the repo. Run `make setup`.
2. Make a private folder for my work, outside the repo. Never put API keys in git.
3. Ask me which area I own (industry, region, source, or ICP slice). Join the team with `team join` so I do not overlap anyone. If we already have a shared veinore-team.json, use `--shared` on that file or folder.
4. Help me add keys only if I have them (private env file, chmod 600). The tool works with zero keys.
5. Run a dry daily preview. Execute only if I say so.
6. After a run, `team export --shared` so the group list stays one list.
7. Save and pin the filters I care about (`filter save` / `filter pin`), including ICP (country, $ revenue, industry keywords, titles).
8. After a run, set stage/owner/notes with `ops set` and export CSV with `leads export`. `schema --json` lists every command.

Guide me in plain words. Stop and ask when you need a choice or a secret.
```

### Set up Veinore with your own agent (plain words)

The agent should do this, in order, and pause when it needs a choice from you.

1. **Get the code.** `git clone https://github.com/amrtawfik160/lead-research-kit.git` then `cd lead-research-kit` and `make setup`.
2. **Make a private work folder** outside the repo, for example `~/Documents/veinore-work`. Run `scripts/lead-research --workspace ~/Documents/veinore-work init --json`.
3. **Pick your area** so two people do not hunt the same slice. Industry, region, source, or ICP. Example: you take Texas, a teammate takes SaaS founders.
4. **Join the team** (creates or updates a shared file — no hosted server):
   ```bash
   scripts/lead-research --workspace ~/Documents/veinore-work team join \
     --name "Your Name" --area-kind region --area "Texas" \
     --shared /path/to/shared-folder --json
   ```
   Put that shared folder on a team Drive, a private git repo, or a USB stick. The file is `veinore-team.json`.
5. **Keys are optional.** The kit runs with zero keys. If you have some, keep them in a private env file (`chmod 600`) and load with `set -a; . /private/path/keys.env; set +a`. Never paste keys into chat, git, or the dashboard.
6. **Daily run.** Preview first (`daily --target 10 --json`). Add `--execute` only when you mean to spend or write today's file.
7. **Share your finds.** `team export --shared /path/to/shared-folder --json`. Teammates run `team import --shared /path/to/shared-folder --json`. Same email, LinkedIn, or name+company website collapses to one row. The first finder keeps credit.
8. **Filters and ICP.** `filter save NAME --stage new --area Texas --pin --json`. Add `--country US --revenue-min 5M --industry "..." --include-titles "CEO,..." --exclude-junior` for targeting. `daily --filter NAME` uses that ICP. `filter apply NAME --json` returns matching leads. Then `ops set --email ... --stage contacted` and `leads export --output FILE.csv`.
9. **Ask the catalog.** `schema --json` (or `commands --json`) lists every command, flag, output shape, and exit code. `--help` works on every command. Exit `0` is OK; exit `2` is an error with a hint.

Team commands: `team join`, `team export`, `team import` (same as `team merge`), `team list --json`.

Filter commands: `filter save`, `filter list`, `filter pin`, `filter unpin`, `filter apply --json`.


### HeyReach export

```bash
lead-research --json leads export --format heyreach --date 2026-10-22 --output heyreach.csv
lead-research --json leads export --format heyreach --filter "US CRE decision makers $5M+" --output heyreach.csv
```

Columns follow HeyReach's "Import from CSV" layout (Profile URL, First Name, Last Name, Full Name, Headline, Email Address, Job Title, Location, Company, Company URL), then Company Website, City, State, Company Type, Source, Email Risk, Email Verification and Confidence. HeyReach needs a LinkedIn Profile URL on every row, so leads without one go to `heyreach-no-linkedin.csv` with the same columns. Company URL is the company's LinkedIn page when known, otherwise its website. Every daily run also writes `leads-heyreach.csv` (and `leads-heyreach-no-linkedin.csv`).

### Targeting a state, industry and titles (`daily --filter`)

```bash
lead-research --json filter save "US CRE decision makers $5M+" --country US --state CA --revenue-min 5M --revenue-unknown keep \
  --industry "real estate development,real estate,commercial real estate,property management,construction" \
  --include-titles "CEO,COO,CFO,President,Partner,Managing Partner,Managing Director,VP of Strategy,Founder,Owner,Principal,EVP,SVP" \
  --exclude-titles "junior,assistant,coordinator,intern" --exclude-junior --pin
lead-research --json filter show "US CRE decision makers $5M+"
lead-research --json daily --filter "US CRE decision makers $5M+" --target 500            # dry preview
lead-research --json daily --filter "US CRE decision makers $5M+" --target 500 --execute  # real run
```

- `--state`: Hunter Discover, Places and OSM search only these states, out-of-state license feeds are skipped, and the crawl queue is the in-state backlog. Delivered leads must have a known, listed state
- `--industry`: also adds matching Hunter industries to discovery (e.g. Construction) and runs keyword searches with them. Unknown industry is kept; a declared industry that doesn't match is dropped
- Titles: whole-word match with abbreviations (CEO = Chief Executive Officer, VP of Strategy = Vice President, Strategy). "President" never matches "Vice President", and EVP/SVP never widen to every VP. Exclusions win
- `--revenue-min`: never estimated. None of the free providers report revenue, so with `--revenue-unknown keep` (default) matches are delivered and marked `unknown` in the details file's Annual Revenue column; `drop` keeps only leads with known revenue at or above the minimum
- The report shows matched and dropped counts by reason

## Providers

All start **disabled** with **zero** budget. Free allowances change; confirm balances each run. Env names only — never commit values.

| Job | Providers | Env |
|---|---|---|
| Search / resolve | Serper, Brave, Tavily, Exa, Zenserp, Linkup, You.com, SerpApi, Firecrawl search | `SERPER_API_KEY`, `BRAVE_API_KEY`, `TAVILY_API_KEY`, `EXA_API_KEY`, `ZENSERP_API_KEY`, `LINKUP_API_KEY`, `YDC_API_KEY`, `SERPAPI_API_KEY`, `FIRECRAWL_API_KEY` |
| Company discovery | Hunter Discover, Google Places, OpenStreetMap, public records (TX/NY/CO/CT/SEC) | `HUNTER_API_KEY`, `GOOGLE_MAPS_API_KEY`, `SEC_USER_AGENT` |
| Page read / crawl | Local crawler (free), Jina Reader (keyless; optional key), Firecrawl scrape | `JINA_API_KEY` (optional), `FIRECRAWL_API_KEY` |
| Find emails | Hunter, ZeroBounce, Tomba, Icypeas, Deepline, Enrow, Lusha, Skrapp, Apify contacts | `HUNTER_API_KEY`, `ZEROBOUNCE_API_KEY`, `TOMBA_API_KEY`+`TOMBA_SECRET`, `ICYPEAS_API_KEY`, `DEEPLINE_API_KEY`, `ENROW_API_KEY`, `LUSHA_API_KEY`, `SKRAPP_API_KEY`, `APIFY_API_TOKEN` |
| Verify | Reoon, QuickEmailVerification, Abstract, Emailable, Kickbox, Bouncer, MyEmailVerifier, emailvalidation.io, EmailListVerify, Verifalia, Clearout, MillionVerifier | `REOON_API_KEY`, `QUICKEMAILVERIFICATION_API_KEY`, `ABSTRACT_EMAIL_API_KEY`, `EMAILABLE_API_KEY`, `KICKBOX_API_KEY`, `BOUNCER_API_KEY`, `MYEMAILVERIFIER_API_KEY`, `EMAILVALIDATION_API_KEY`, `EMAILLISTVERIFY_API_KEY`, `VERIFALIA_TOKEN`, `CLEAROUT_API_KEY`, `MILLIONVERIFIER_API_KEY` |
| Signals (not mailbox proof) | Hacker News Algolia (no key), Product Hunt, GitHub, Hunter Insight, DISIFY | `PRODUCTHUNT_TOKEN`, `GITHUB_TOKEN` |
| Identity enrich | People Data Labs, Apollo | `PDL_API_KEY`, `APOLLO_API_KEY` |

Adapter contracts and recovery rules: [`skills/lead-research/references/provider-operations.md`](skills/lead-research/references/provider-operations.md), [`skills/lead-research/references/recovery.md`](skills/lead-research/references/recovery.md).

## Safety

- Workspaces, SQLite ledgers, exports, and filled `.env` files stay **outside** the public tree
- Provider pauses are never cleared by budget edits; `resume` is GET-only for saved pending jobs
- Same-day re-execute merges with dedupe; it does not replay spent requests
- `scripts/release_check.py` rejects contact-like data, filled env templates, and secret-shaped literals
- Provider-valid ≠ delivery, consent, current employment, or ownership

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 scripts/release_check.py .
cd dashboard && npm run build
```

## License

[MIT](LICENSE) for this source code. Review provider terms and privacy obligations before live use. No permission is granted to redistribute third-party contact datasets or operate a hosted verifier resale service.

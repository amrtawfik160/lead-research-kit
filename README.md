<p>
  <img src="dashboard/public/brand/logo-light.svg" alt="Veinore" width="168" height="32" />
</p>

# Veinore

Veinore is an evidence-first Python toolkit, with a local operator dashboard, for finding business contacts and researching their work emails without mixing guesses, public sources, and provider claims

**Status: v0.5.0 alpha, local CLI and Veinore dashboard, Python 3.11+, no runtime dependencies**

This is a clean source-code project. It contains synthetic examples, not the contact database, account state, API credentials, or captures from the research that inspired it. Source repository: https://github.com/amrtawfik160/lead-research-kit. No package has been published to PyPI.

## What it does

- Imports CSV, JSON and JSONL, preserving stable IDs and acquisition source
- Keeps LinkedIn-qualified captures separate from other-source contacts
- Records source excerpts, person-match attestations, affiliation caveats and quality holds
- Builds bounded research and verification queues
- Generates a maximum of three labeled name/domain guesses from explicit company-domain evidence
- Routes requests to enabled, funded providers and reuses saved results before spending
- Tracks fractional credits and separate pools with exact arithmetic
- Persists requests in a private SQLite ledger before sending them
- Resumes supported pending jobs with GET, without submitting the email again
- Exports public emails, candidates, provider results and accepted options separately

It does **not** send email, probe SMTP, register accounts, rotate identities/IPs, bypass access restrictions, scrape private profiles, infer company revenue, or establish that an email belongs to a particular person.

## Fresh leads every day

```bash
lead-research --workspace /private/ws history import old-leads.csv     # never deliver these again
lead-research --workspace /private/ws daily --target 500               # preview: plans only, nothing sent or recorded
lead-research --workspace /private/ws daily --target 500 --execute     # writes daily/<date>/leads.csv and remembers it
```

One `daily --execute` run:

1. **Polls** any saved pending finder/verifier jobs
2. **Public records** (free): pulls new or updated decision makers from Texas TREC (designated brokers of every brokerage), New York DOS (principal and owner brokers), Colorado DORA (responsible brokers), Connecticut DCP (active broker firms; public domain; the website and people come from resolution and the crawl) and SEC Form D (executives of new real-estate offerings). Cursors make each day a delta
3. **Resolve**: finds the website of each public-record firm with one search query (Serper or Brave), skipping portals, directories and franchise parents
4. **Discover** more companies when the backlog is short: Hunter Discover (free calls), Google Places (1,000 free requests a month), OpenStreetMap. Markets rotate so each day covers new states and metros
5. **Crawl** the oldest uncrawled company sites in parallel for published names, titles, emails and email patterns
6. **Find** emails for fresh decision makers whose site hides them (Hunter, ZeroBounce, Tomba, Icypeas, whichever is funded first)
7. **Verify** risk-first, promoting only valid results: pattern guesses for fresh decision makers, then published emails that do not look like the person's name, then finder results, then other guesses, then (up to `--cap verify-published=25`) other published emails never checked. Emails on domains already known to accept any address, or with no mail server, are skipped because a check cannot help (Reoon, Abstract, Bouncer, MyEmailVerifier, ...)
8. **Email safety** (free DNS): every email domain gets an MX lookup (cached 30 days in `lead-history.sqlite3`; a domain with only an A record still counts, per RFC 5321; resolvers come from `/etc/resolv.conf`, override with `LEAD_RESEARCH_DNS_SERVERS=9.9.9.9,1.1.1.1`). Each lead gets an email risk:
   - **high**, never delivered: role inboxes (info@, office@, sales@, ...), personal mailboxes, no mail server or null MX, a verifier said invalid, or an unverified guess
   - **medium**: accept-all (catch-all) domains, verifier unsure, unverified finder results, published emails not built from the person's name
   - **low**: verifier valid, or published on the company's own site with a name-based address
9. **Select** the best fresh A/B leads with a work email, low risk before medium, never anyone delivered before (matched by LinkedIn, email or name + company)
10. **Writes** `leads.csv`, `leads-details.csv` and `report.json` (per-step counts, dropped unsafe emails, shortfall and what to enable next)

Daily `leads.csv` columns (the 6 clean columns first, so tools that read the clean format keep working):

| Column | Meaning |
|---|---|
| Full Name, Title, Company, Company Website, LinkedIn URL, Email | Same as the `clean` output |
| City, State | From public records or the company site's address (state as a 2-letter code) |
| Company Type | `brokerage`, `developer`, `property_manager`, `investor` or `other`, from the company name (its last keyword wins: "Capital Realty" is a brokerage), then the record source, the site description, broker titles and the domain |
| Source | Where the lead came from: `website`, `public_record`, `linkedin`, `finder` or `search` |
| Confidence | `high`, `medium` or `low`, from email risk, tier and evidence (source page or LinkedIn) |

`leads-details.csv` adds Tier, Score, Email Source, Source URL, Issues, Email Risk, Email Risk Reason, MX Provider (google, microsoft365 or other) and Email Verification. The `clean` and `build` commands still write exactly the 6 columns.

Steps without an enabled, funded provider are skipped and reported, never forced. Tune with `--sources`, `--record-sources`, `--cap resolve=800`, `--max-domains`, `--min-tier A`.

### Free-tier stack (what a $0 setup can use)

| Job | Providers (free allowance as of Oct 2026, check before relying on it) |
|---|---|
| Company discovery | Hunter Discover (free calls; rotate cities, states, niches, hiring), OpenStreetMap, public license records (TX, NY, CO, CT, SEC Form D) |
| Firm website lookup | Hunter Discover by firm name (free, 50 names per call), then free domain guessing from the firm name (DNS check + homepage must show the firm name and its city/county/state; cap `resolve-guess`, 1,500 firms/day, each firm guessed once), then Serper batches of 100 (2,500 one-time), Tavily (1,000/month), Exa ($10/month), Zenserp (50/month), Linkup (~EUR 5/month credit, unverified), You.com (signup credit, ~$0.005/search) |
| People + emails | Your crawler (free), Firecrawl rendering for JavaScript-only team pages (~1,000 credits/month), Deepline promo credits (LeadMagic finder ~0.34 credits per found email; set `DEEPLINE_FIND_ROUTE=waterfall` for the multi-provider play), Enrow (50 one-time, billed only when found), Lusha (paid plans only: the Free plan has no API) |
| Email verification | Reoon (20/day), QuickEmailVerification (100/day), Abstract (100/month), emailvalidation.io (100/month, 10/min), Bouncer (100 one-time), MyEmailVerifier (100), EmailListVerify (100 one-time), Verifalia, Clearout, MillionVerifier, Emailable, Kickbox |
| LinkedIn URLs | Serper/Tavily search, People Data Labs (100/month, identity fields only on free) |

`verify` uses the first enabled, funded verifier in configuration order, so free pools are used one after another as each runs out. Daily and monthly allowances (Reoon 20/day, QuickEmailVerification 100/day, emailvalidation.io 100/month, Zenserp 50/month) refill only if you add them with `history refill add`.

### Long-term operations

```bash
lead-research --workspace WS history refill add reoon --pool credits --amount 20 --period daily --source "Reoon free plan"
lead-research --workspace WS history suppress customers.csv --reason customer      # people or whole domains, forever
lead-research --workspace WS history feedback outreach-results.csv                 # Outcome: replied, meeting, bounced, unsubscribed...
lead-research --workspace WS history import-results instantly-export.csv          # Instantly, Smartlead, Lemlist, Apollo, HubSpot (auto-detected)
lead-research --workspace WS history import-results events.csv --tool generic      # any file with email + status/event
lead-research --workspace WS history patterns                                      # domains whose guessed-email pattern bounced
lead-research --workspace WS history sources                                       # bounce / reply rate per email source
lead-research --workspace WS --env-file WS/keys.env daily --target 500 --execute
lead-research --workspace WS daily audit --sample 20                               # spot-check today's leads against their evidence
```

- **Recurring free allowances** are operator-attested: each pool is reset to its documented free amount once per day or month, never added on top
- **Feedback**: bounces, unsubscribes, wrong-person and do-not-contact rows are blocked automatically. Sources with a bounce rate above 5% are deprioritized
- **Send results** (`history import-results`): reads outreach tool exports directly. Hard bounces block the email forever; unsubscribes, spam complaints and do-not-contact block the whole person; replies, meetings and opens count toward each email source's reply/engagement rate. When a guessed (`pattern_verified`) email hard-bounces, that domain's pattern is distrusted and no more guesses are made there until `history patterns --clear DOMAIN`. Soft bounces are ignored. Re-importing the same file changes nothing
- **Spot-check** (`daily audit`): re-fetches the evidence page of a random sample (robots.txt respected, cache reused only when under a day old) and checks the name, published email, title, website and LinkedIn slug, plus a passing verifier result for guessed emails. Writes `audit.json` / `audit.md` with % confirmed, partial and failed, and the score is added to `report.md`
- **Recrawl**: company sites are revisited every 90 days to catch new people; crawl data older than 100 days is not delivered
- **Backups**: every executed run makes consistent copies of the ledger and history in `WS/backups/<date>/` (newest 14 kept)
- **Morning summary**: `daily/<date>/report.md` (delivered vs target, sources, low budgets, next steps, a Delivery section listing the files, and the spot-check score once `daily audit` has run)
- **Keys**: `--env-file` reads a private 0600 `KEY=VALUE` file; existing environment variables win

### What it takes to reach 500 a day

Measured: a crawled brokerage site yields about 0.3 fresh decision makers with a published work email, so 500/day needs about 1,700 fresh company sites a day, or fewer with a finder filling the gaps.

| Setup | Monthly cost | Realistic daily output |
|---|---|---|
| Free only: public records + OSM + crawl, no search key | $0 | Low: records give names and firms but most firm websites stay unknown |
| + Serper (resolve firms, LinkedIn URLs), Hunter Discover, Google Places free tier, Reoon free verifications | ~$0–50 (Serper is $1 per 1,000 queries after 2,500 free) | ~150–300 |
| + a pay-per-found finder (e.g. Icypeas ~$0.01–0.04 per found email) and MillionVerifier/Reoon | ~$100–150 | 500+ |

### Keys

Put keys in your environment (see `.env.example`), never in the repo: `SERPER_API_KEY`, `BRAVE_API_KEY`, `HUNTER_API_KEY`, `GOOGLE_MAPS_API_KEY`, `APOLLO_API_KEY`, `PROSPEO_API_KEY`, `ICYPEAS_API_KEY`, `TOMBA_API_KEY`/`TOMBA_SECRET`, `REOON_API_KEY`, `MILLIONVERIFIER_API_KEY`, `EXA_API_KEY`, plus `SEC_USER_AGENT="Your Name you@example.com"` for SEC and optional `SOCRATA_APP_TOKEN`. Then `provider enable <name>` and `budget confirm <name> --pools ... --free` with the allowance you actually want spent.

### Public-record terms

`records list` shows each source's terms. California DRE (not for mass mailing) and Washington DOL (commercial use prohibited) are deliberately excluded. Florida's DBPR files are behind Cloudflare, so download them yourself and use `records load fl-dbpr-file FILE`. License data is not consent: follow CAN-SPAM and your own legal advice for outreach.

## Build a clean lead list fast

The output is always the same 6 columns:

```
Full Name,Title,Company,Company Website,LinkedIn URL,Email
```

### 1. Clean any list you already have (offline, seconds)

```bash
lead-research clean leads.csv --output out/
```

Reads exports from any tool (CSV/JSON, column names are matched loosely) and writes:

- `leads-clean.csv`: tier A/B decision makers only, bad fields blanked instead of guessed
- `leads-review.csv`: tier C rows with score, issues and email source
- `leads-rejected.csv`: agents, investors, associations, non-US and multi-person rows, with reasons
- `quality-summary.json`: counts and top issues

What it fixes: "CEO at X" company fields, multi-part titles, credentials in names ("MBA", "CCIM"), percent-encoded or country LinkedIn URLs, websites copied from gmail/ISP/.edu/franchise-parent email domains (kw.com for an independent brokerage), personal and role emails in the Email column, duplicates by LinkedIn, email or name+domain. Options: `--min-tier A`, `--allow-personal-email`, `--keep-non-decision-makers`.

### 2. Fill emails from company websites (free, parallel)

```bash
lead-research --workspace /private/ws crawl out/leads-clean.csv out/leads-review.csv --output crawl.json            # plan
lead-research --workspace /private/ws crawl out/leads-clean.csv out/leads-review.csv --output crawl.json --execute --workers 24
```

Visits each company's homepage and its best team/about/leadership/agents/contact pages (max 6 per site), in parallel across sites, one request per site per second, honoring robots.txt, with an on-disk cache so re-runs are instant. It reads JSON-LD, microdata, mailto links, Cloudflare-protected and "[at]/[dot]" emails, LinkedIn links and name/title cards. It also learns each company's email pattern from the emails it publishes. Blocks private/internal addresses and pins connections to the checked IP.

### 3. Find new companies and people

| Source | Cost | What you get | Jobs command |
|---|---|---|---|
| Hunter Discover | Free API call | US real-estate company domains by state and size | `jobs hunter-discover --states all` |
| OpenStreetMap | Free | Real-estate offices with websites in a city/county | `jobs osm --areas "Austin:TX;Miami:FL"` |
| Serper (Google) / Brave | 2,500 free queries / $5 monthly credit | LinkedIn profiles (name, title, company, URL) from search results, without logging into LinkedIn | `jobs xray --provider serper --states TX,FL` |
| Hunter Domain Search | 1 search each | Decision makers at a domain with work email, title, LinkedIn and confidence | `jobs hunter-domains crawl.json` |
| Website crawl | Free | Published names, titles, emails, LinkedIn links | `crawl ... --execute` |

Each jobs command writes a plan for `run`, which spends only enabled, confirmed budget:

```bash
lead-research --workspace /private/ws jobs hunter-discover --states TX,FL,AZ --output discover.json
lead-research --workspace /private/ws run discover.json --execute --limit 50
lead-research --workspace /private/ws collect --output found.json
lead-research --workspace /private/ws crawl found.json --output crawl.json --execute
```

### 4. Merge and score everything

```bash
lead-research --workspace /private/ws build --import leads.csv --prospects found.json --crawl crawl.json --from-workspace --output final/
```

Website-published emails win. A guessed email (from a pattern the company publishes in at least 2 emails, or Hunter's pattern) never goes into the Email column directly. It goes to `final/email-candidates.csv` and `final/verify-jobs.json`. Verify with `run final/verify-jobs.json --execute`, then rebuild: only addresses a verifier calls valid (not catch-all) are promoted.

A practical fast loop for a new market: `hunter-discover` (free) or `osm` (free) → `crawl` (free) → `hunter-domains` only for companies the crawl did not cover → `xray` to add LinkedIn URLs → `build` → verify candidates → `build` again.

## Quick start

Run from the source tree, without installing anything:

```bash
PYTHONPATH=src python3 -m lead_research --help
PYTHONPATH=src python3 -m lead_research --workspace /private/path/research init
```

Or install in your own virtual environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
lead-research --help
```

The following examples use `lead-research`; the source-tree invocation above is equivalent.

### Try the complete offline demo

```bash
lead-research --workspace /private/path/demo demo --output /private/path/demo-export
```

The demo uses fake responses and reserved example domains. It sends **zero live requests** and checks no real addresses. Synthetic/demo workspaces are blocked from using the default live transport. Use a separate empty workspace for real work.

### Import and research

```bash
lead-research --workspace /private/path/research import examples/contacts.json
lead-research --workspace /private/path/research research-queue --limit 40 --output /private/path/queue.json
```

For captured LinkedIn records, supply accepted filter evidence in each record or explicitly attest the captured filter state:

```bash
lead-research --workspace /private/path/research import YOUR_CAPTURE.jsonl \
  --channel linkedin --filters-accepted --filter-country US --filter-industry "Real Estate"
```

This records the operator's capture evidence; it does not independently verify profiles or current employment. Directory additions never become LinkedIn leads. Missing revenue stays unknown. `--trust-classifications` only imports prior public-email labels when the source URL and an excerpt containing the email are also present; it does not re-fetch sources.

Record an attributable public address:

```bash
lead-research --workspace /private/path/research evidence LEAD_ID \
  --email person@company.example --classification public_person_linked \
  --source https://company.example/team \
  --excerpt 'Person Name, Founder: person@company.example' --person-matched
```

Add `--affiliation-mismatch` when the published email belongs to a different current business. Generic role mailboxes stay `company_inbox`, not person-linked. Use `hold` for deceased contacts, brands, stale leadership claims or unresolved identities. Importing an update does not clear an existing hold.

### Set up an authorized provider

All providers start **disabled**, all budgets start **zero**, and all network commands default to **dry run**. Credentials come only from your environment, never from the repository or SQLite records. `.env.example` lists the variable names; it is not automatically loaded.

```bash
lead-research --workspace /private/path/research provider enable hunter
lead-research --workspace /private/path/research budget sync hunter
# The previous command plans only; this explicitly reads the account balance:
lead-research --workspace /private/path/research budget sync hunter --execute
```

A plan called "Free" can still contain paid add-ons. Hunter, Clearout and ZeroBounce therefore report total/counter metadata without automatically funding a free budget. After confirming the genuine free allowance and billing settings yourself, record a bounded allocation:

```bash
lead-research --workspace /private/path/research budget confirm hunter \
  --pools '{"credits":"5","find_ops":"5","verify_ops":"10"}' \
  --source 'Own account dashboard, confirmed free allowance' --free
```

The example amount is an operator allocation, **not an advertised quota**. Verifalia's explicit `freeCredits` field can be synchronized automatically. No elapsed date or provider marketing page refills a budget. For providers with indistinguishable paid/free balances, free-only use depends on your attestation and account billing controls; the tool cannot prove how the vendor will bill.

### Plan, execute, resume

```bash
lead-research --workspace /private/path/research find-queue YOUR_DOMAIN_MAP.json --output /private/path/find-jobs.json
lead-research --workspace /private/path/research run /private/path/find-jobs.json
lead-research --workspace /private/path/research run /private/path/find-jobs.json --execute --limit 10

lead-research --workspace /private/path/research verify-queue --output /private/path/verify-jobs.json
lead-research --workspace /private/path/research run /private/path/verify-jobs.json --execute --limit 10

# One saved pending Verifalia job, one bounded GET poll, no new submission:
lead-research --workspace /private/path/research resume ATTEMPT_ID
lead-research --workspace /private/path/research resume ATTEMPT_ID --execute
```

The domain map uses `lead_id`, `domain`, `source_url`; see `examples/domains.json`. Automatic routing chooses one eligible funded provider per job. Cross-checking is explicit (`verify-queue --crosscheck`), limited to two independent provider groups per address. Signals from the same vendor do not count as independent mailbox proof. Normal re-runs reuse cached results; they do not silently retry errors.

```bash
lead-research --workspace /private/path/research export /private/path/export
lead-research --workspace /private/path/research audit
```

## Providers

| Provider | Supported operations | Important boundary |
|---|---|---|
| Hunter | Finder, verifier, balance read | Separate search/verification counters; shared fractional credits |
| Verifalia | Standard verification, saved-job GET, free balance | Explicit free credits only; bearer or Basic environment auth |
| Clearout | Instant verification, total balance read | Safe-to-send warnings retained; free allowance manually attested |
| ZeroBounce | Finder, single verification, total balance read | Finder queries never borrow validation credits; no batch route |
| Hunter Email Insight | Domain/pattern signals | Not mailbox verification |
| DISIFY | Syntax/DNS/disposable signals | Not mailbox verification |
| Hunter Discover / Domain Search | Company discovery, decision makers at a domain | Discover uses its own local counter; Domain Search uses Hunter search credits |
| Serper / Brave | Search-engine LinkedIn X-ray | Serper `num` fixed at 10 so 1 query = 1 credit |
| OpenStreetMap Overpass | Real-estate offices in a place | Free fair-use API, 5s spacing, ODbL attribution |
| Apollo | People API Search (`people_search`), single-person enrichment (`people_enrich`) | Search is 0 credits (local `search_requests` counter) and returns obfuscated last names (`name_complete: false`); enrichment is one person per job, 1 `credits`, personal emails and phones never revealed |
| Prospeo | Person search over ≤500 company websites (`people_search`), person enrichment (`enrich`) | 1 credit per search page (25 people, no emails) or per email found; `NO_RESULTS`/`NO_MATCH` (HTTP 400) are not-found, not errors |
| Google Places | Text Search (`text_search`) for brokerages and property managers | Fixed Enterprise field mask (website + phone) = 1 `enterprise_requests`; next pages via `page_cursor` |
| Exa | `search` in the `people` category (LinkedIn-style profiles) | 10 results per query, no paging; reuses the X-ray profile parser |
| Tomba | Email Finder (`find`), LinkedIn Finder (`linkedin_email`) | Key + secret headers; 30s spacing matches the Free plan's 2 finder requests/minute |
| Icypeas | Email discovery (`find`) | Asynchronous: the submit returns `pending`; finish it with `resume` (read-only result POST, never resubmitted). A pending attempt blocks new requests until resumed |
| Reoon | POWER-mode single verification | Only power-mode `safe` counts as valid; catch-all, role, disposable, spamtrap and inbox-full stay unknown with risk flags |
| MillionVerifier | Single API verification | Demo keys (`livemode: false`) are refused; `quality: risky/bad` becomes a risk flag |

Automatic routing tries providers in configuration order. Existing providers keep their priority; Reoon then MillionVerifier are the last `verify` options, so a saved configuration never silently changes which account a verification spends. Each verifier is its own independence group.

Environment variables: `HUNTER_API_KEY`, `VERIFALIA_TOKEN` (or `VERIFALIA_USERNAME` + `VERIFALIA_PASSWORD`), `CLEAROUT_API_KEY`, `ZEROBOUNCE_API_KEY`, `SERPER_API_KEY`, `BRAVE_API_KEY`, `APOLLO_API_KEY`, `PROSPEO_API_KEY`, `GOOGLE_MAPS_API_KEY`, `EXA_API_KEY`, `TOMBA_API_KEY` + `TOMBA_SECRET`, `ICYPEAS_API_KEY`, `REOON_API_KEY`, `MILLIONVERIFIER_API_KEY`. Reoon and MillionVerifier take the key as a query parameter (their only documented method); it stays in the request object and is never stored.

Adapters retain selected facts, not raw arbitrary provider bodies, names/geodata/gender enrichment or credential-bearing URLs. Live compatibility and account entitlement must be checked with a small authorized canary before scaling. Tests use mocked schemas, not live accounts. Some free services permit internal use but require approval for hosted resale or third-party API products; see [provider operations](skills/lead-research/references/provider-operations.md).

## Recovery and release

- [Recovery and known limitations](skills/lead-research/references/recovery.md): pauses, crashes, held budgets and pending requests
- [Data model and source labels](skills/lead-research/references/data-model.md): what each output means
- [Public-release checklist](skills/lead-research/references/public-release.md): code and synthetic fixtures only
- [Agent skill](skills/lead-research/SKILL.md): a reusable operating workflow, including optional browser capture guidance

Provider-valid is an observation, not delivery, inbox placement, consent, current employment or ownership proof. "Accepted options" exclude known risk/review flags but are not an outreach approval list. Verification observations are cached; inspect their timestamps before deciding whether fresh checks are needed.

## Dashboard

A local Linear-style UI lives in [`dashboard/`](dashboard/README.md). It reads a private workspace through the kit (or an opt-in synthetic sample) and does not store API keys.

```bash
cd dashboard
npm install
npm run dev
```

Open http://127.0.0.1:3000. See the dashboard README for screens, sample mode, and `LEAD_RESEARCH_WORKSPACE`.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 scripts/release_check.py .
```

Tests cover exact budgets, concurrency, no-network dry runs, provider schemas, pending GET-only polling, source separation, formula-safe CSV exports, paused services and synthetic demo isolation. CI targets Python 3.11–3.13; local verification is reported separately.

MIT license prepared for this source code. Review licensing, provider terms, privacy obligations and repository contents before publishing. This project includes no permission to redistribute third-party contact datasets or operate a hosted verifier resale service.

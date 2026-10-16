# Lead Research Kit

An evidence-first Python toolkit for finding business contacts and researching their work emails without mixing guesses, public sources, and provider claims

**Status: v0.1.0 alpha, local CLI, Python 3.11+, no runtime dependencies**

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

Adapters retain selected facts, not raw arbitrary provider bodies, names/geodata/gender enrichment or credential-bearing URLs. Live compatibility and account entitlement must be checked with a small authorized canary before scaling. Tests use mocked schemas, not live accounts. Some free services permit internal use but require approval for hosted resale or third-party API products; see [provider operations](skills/lead-research/references/provider-operations.md).

## Recovery and release

- [Recovery and known limitations](skills/lead-research/references/recovery.md): pauses, crashes, held budgets and pending requests
- [Data model and source labels](skills/lead-research/references/data-model.md): what each output means
- [Public-release checklist](skills/lead-research/references/public-release.md): code and synthetic fixtures only
- [Agent skill](skills/lead-research/SKILL.md): a reusable operating workflow, including optional browser capture guidance

Provider-valid is an observation, not delivery, inbox placement, consent, current employment or ownership proof. "Accepted options" exclude known risk/review flags but are not an outreach approval list. Verification observations are cached; inspect their timestamps before deciding whether fresh checks are needed.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 scripts/release_check.py .
```

Tests cover exact budgets, concurrency, no-network dry runs, provider schemas, pending GET-only polling, source separation, formula-safe CSV exports, paused services and synthetic demo isolation. CI targets Python 3.11–3.13; local verification is reported separately.

MIT license prepared for this source code. Review licensing, provider terms, privacy obligations and repository contents before publishing. This project includes no permission to redistribute third-party contact datasets or operate a hosted verifier resale service.

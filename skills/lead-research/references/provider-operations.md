# Provider operations

## Account and funding boundary

Use the user's existing authorized account. A separate service is not an invitation to create duplicate accounts at the same provider. Keep automatic top-ups and paid fallback out of a free-budget run. If an API exposes only a combined balance, obtain the user's free-fund attestation; do not infer it from a plan label.

All allowances start at zero. `budget confirm` records an operator assertion with a source, not independent proof of free funds. `budget sync --execute` can fund only a field explicitly representing free credits. Marketing allowances change; confirm actual balances each run. The toolkit cannot guarantee a vendor's billing-source choice when paid and free units are indistinguishable.

Pool names and per-request costs live in `configuration.py`, not in this document. Configuration loading rejects price/endpoint overrides for the built-in providers. Fractional amounts use exact integer millionths, not floats.

## Supported contracts

- Hunter: `api.hunter.io/v2/email-finder`, `email-verifier`, `account`; `HUNTER_API_KEY` in the documented header. A finder may contain a prior verification report; it remains a candidate, not a fresh verification. Account counters are metadata even on Free plans because add-on credits may exist.
- Verifalia: `api-1.verifalia.com/v2.7/email-validations`, Standard quality; bearer `VERIFALIA_TOKEN` or environment Basic credentials. Only `freeCredits` funds the automatic free pool. A pending Location must be the exact official same-origin job URL without credentials, query or fragment. Saved jobs use GET, never another POST.
- Clearout: instant verification only. Preserve `safe_to_send`, risk/status and the returned recipient. Total credits require manual free attestation. A provider timeout is not an invalid mailbox.
- ZeroBounce: single verification and finder only. Preserve `email_confidence` without interpreting high confidence as a mailbox test. Preserve `catchall_domain` even when status says valid. Finder queries have their own pool and must stop before falling back to expensive validation credits. Total credits require manual free attestation. The toolkit has no batch route: batch requests were rejected in one account during the original investigation, but that is not evidence of a universal provider defect.
- Hunter Email Insight and DISIFY: signals only. They do not consume an independent verifier slot or prove a recipient exists.

For changed schemas, reproduce the shape with synthetic fixtures before modifying an adapter. A positive result requires a recognized status and a returned recipient matching the request. Unknown or malformed schemas are errors, not positive results. Do not store gender, inferred demographic data, arbitrary raw error bodies or secret-bearing URLs.

- QuickEmailVerification, emailvalidation.io, EmailListVerify (verify): QEV `api.quickemailverification.com/v1/verify` (key as `apikey` query, 100 free per day); emailvalidation.io `api.emailvalidation.io/v1/info` (`apikey` header, 100/month, 10/min, catch-all reported as status `catch_all`); EmailListVerify `api.emaillistverify.com/api/verifyEmailDetailed` (`secret` query, `ok_for_all` = catch-all). Daily/monthly allowances refill only through `history refill add`.
- Enrow and Lusha (find): Enrow `api.enrow.io/email/find/single` is asynchronous (submit, then a free GET read via `resume`); `valid` means found and verified by Enrow, still not a fresh check here. Lusha `api.lusha.com/v2/person` (`api_key` header) is paid-plan only: a Free account has no API access. Lusha emails are sourced data (finder status `unknown`), so they still go through verification.
- Zenserp, Linkup, You.com (search): result rows share the Serper shape, so firm resolution and LinkedIn x-ray can use them. Pools: zenserp `searches`, linkup/youcom `queries`.

## Before live use

1. Check current provider docs, terms and account entitlement
2. Keep the account's billing controls consistent with a free-only run
3. Confirm small explicit pools, review the dry plan, execute one canary
4. Read back actual usage and retained risk fields
5. Scale only within that confirmed allowance

Treat API keys, short-lived bearer tokens and account passwords differently. A token working now does not establish durable authentication. Save one-time credentials in a configured secure manager if available; otherwise mark storage blocked, do not put them in a release or claim they were saved.

## Terms and publication

Provider APIs are independent products with their own licenses and data-handling terms. A local client using the user's own credentials is not the same as a hosted lead database, shared-key service, reseller or API proxy. For example, ZeroBounce's standard terms restrict external third-party product/service use without approval. Obtain the relevant provider's permission before offering hosted or resale use. Publishing this source does not grant redistribution rights to any contact dataset.

Official references, to recheck before live use:

- https://hunter.io/api-documentation/v2
- https://verifalia.com/developers
- https://clearout.io/email-verification-api/
- https://www.zerobounce.net/docs/email-validation-api-quickstart/v2-validate-emails
- https://www.zerobounce.net/docs/email-finder-api
- https://www.zerobounce.net/terms-of-use
- https://www.disify.com/

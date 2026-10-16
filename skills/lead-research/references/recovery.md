# Recovery without duplicate charges

## Start with facts

Run `status` and `audit`. Inspect the saved attempt state, provider pause, request timestamps, and confirmed pools. Keep the original workspace for the same accounts. A new workspace does not reset a service's usage or remove an access denial.

An HTTP success is not necessarily an operation success. Providers can return an authentication/credit error inside HTTP200. The adapters mark that as a provider error and stop the run. Error text and arbitrary raw bodies are not kept as recipient evidence.

## State meanings

| State | Meaning | Allowed next action |
|---|---|---|
| reserved | Allocation debited, not marked sent | Explicit `cancel-unsent` with reason; refunds only this proven unsent reservation |
| sent | Request may have reached the provider | Keep funds held; no re-POST, automatic reset or replay |
| pending | Saved Verifalia job awaiting result | `resume` plans; `resume --execute` sends one bounded GET to that exact job |
| completed | Provider response saved | Reuse cache; an explicit independent cross-check may be eligible |
| uncertain | Network/processing outcome unavailable | Stop provider, retain original charge allocation, investigate |
| reviewed | Explicit review recorded after uncertainty | Other eligible work may resume; the failed lookup remains held |
| cancelled | Proven never-sent reservation cancelled | Original history retained; funds returned locally |

Pending polling is limited to 12 claimed polls, with the original allocation and attempt ID. Polling never resubmits the validation. The global lease prevents simultaneous requests; the database is not locked for the duration of HTTP.

## Access or service failure

Pause the affected provider. Do not test another IP, account or host to evade a restriction. Confirm that normal access is restored with the provider before `provider review --reason ... --evidence ...`. Record fresh, actual allowance evidence. Enabling a provider or updating a budget does not clear its pause.

Budget increases are refused while requests are reserved, sent, pending or uncertain, because an account balance may not yet include the eventual charge. A lower/equal confirmation is safe but does not refund old attempts. Recipient unknowns may be unbilled at a vendor; this is different from proof that a request was never sent. The tool does not automatically recycle either case.

## Deliberate v0.1 limits

- A crashed `sent` request with no saved job URL cannot be automatically recovered. Even explicit review will not release a reserved/sent/pending lease. Preserve the workspace and investigate provider-side results; do not delete or edit SQLite to resend it.
- Error/uncertain lookup history remains held across providers. Provider review can permit other jobs, not silently retry the failed lookup. Same-input retry authorization and evidence-bound sent-request reconciliation are not implemented.
- Completed observations are cached without automatic expiry or re-probing. Inspect timestamps; cache reuse is not a new verification.
- There is no Clearout or ZeroBounce batch adapter. Use the supported single operation only when authorized.
- Audit verification checks the append-only event chain, not a cryptographic guarantee against an owner deliberately rewriting the entire local database.

A future recovery feature must preserve original results and held charges, consume fresh budget for any authorized retry, distinguish no-work rejections from actual recipient results, and prove that an active worker is not being duplicated. Never solve recovery by deleting history.

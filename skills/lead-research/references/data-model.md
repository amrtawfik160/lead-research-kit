# Data labels

The private workspace contains SQLite records, provider state, budget confirmations, attempts and a hash-chained audit log. It is user data, not repository content.

## Contact and acquisition

`id` is stable and string-normalized. A canonical profile URL can match a later import even if its row number changed. Updating an existing contact preserves its original acquisition channel, known hold and evidenced primary email. New sources are secondary provenance, not a way to turn a directory record into a LinkedIn capture.

LinkedIn qualification requires accepted captured country/industry filters plus a leadership title. It describes that capture scope, not an independently verified current role. Other-source qualification is explicitly operator-supplied. Brand/deceased/role-review holds stay outside target totals. Missing revenue is not estimated.

## Email attribution

| Category | What it establishes | What it does not establish |
|---|---|---|
| public_person_linked | Operator supplies a source URL, excerpt containing the address, and person-match attestation | Delivery, ownership uniqueness, current employment or consent |
| company_inbox | Published generic/role contact route | An individual's mailbox |
| source_candidate | Address appeared in a source but person/current affiliation is unresolved | Current person-linked work email |
| name_domain_guess | Common pattern applied to an evidenced company domain | That the company actually uses the pattern |
| provider_finder | Finder returned a candidate, with its confidence kept separate | Fresh recipient validation or identity |
| user_supplied_unattributed | User imported an email without supported public attribution | Public sourcing or identity verification |

The primary `email` field is reserved for source-supported person attribution. Finder or verifier results never populate it. Affiliation mismatches remain a review flag even if the mailbox tests positive.

## Mailbox observations

Recognized verifier responses are `provider_valid`, `provider_invalid`, `catch_all`, or `unknown`. Errors are not recipient-invalid results. Malformed responses fail closed. A positive response without a recognized returned recipient is rejected.

Signals are syntax, DNS, disposable-domain or pattern checks. They do not vote on mailbox validity. Independent groups are vendor-level; two tools from one vendor are not two independent verifiers. Positive and negative votes conflict rather than silently overwriting each other.

A combined assessment can remain unknown/review-needed when risk flags are present, even if an underlying vendor vote is positive. The exact votes and retained fields remain in the exports. Catch-all, disposable, abuse, trap, role and provider risk warnings stay visible. Free-email provider information alone is not treated as a delivery-risk flag.

`identity_confirmed` and `delivery_proven` remain false for provider-derived options. Published attribution and mailbox acceptance answer different questions.

## Exports

- `contacts.csv`: active contact records, original source and attribution fields
- `published-emails.csv`: source-attributed addresses and evidence IDs
- `candidates.csv`: guesses, imported addresses and finder suggestions
- `provider-results.csv`: actual attempt states, observed times and selected response facts
- `provider-accepted-options.csv`: one selected positive option per active contact, excluding risk/affiliation flags; not an outreach approval list
- `summary.json`: separate acquisition, hold, public-email and accepted-option totals

CSV cells are protected from spreadsheet formula interpretation. All exports are private by default and must be kept out of a public release.

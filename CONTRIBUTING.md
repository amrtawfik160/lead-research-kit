# Contributing

Use Python 3.11 or newer. The runtime uses the standard library only.

Run the offline unit suite and public-release checker listed in README before submitting changes. New providers need synthetic contract fixtures, exact budget-pool definitions, stable public error codes, recipient/schema checks and documented authentication. Never add a live test requiring someone else's account or credentials.

Keep credentials out of request representations, logs, saved results, examples and source control. Keep evidence and mailbox checks separate. A valid response is not proof of identity, delivery or consent.

Changes to retries, billing reconciliation, active leases or cached observations need explicit adversarial tests. Preserve old outcomes and held funds; do not make a failed run appear successful by deleting state. Recovery limits are documented in the skill references.

Before hosted or resale deployment, independently check each provider's terms and the rights to any imported data. The MIT license covers this source, not third-party APIs or contact datasets.

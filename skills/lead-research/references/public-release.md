# Public release boundary

Publish code and synthetic fixtures, not the research session.

Before packaging or publishing:

1. Run the full offline unit suite and an offline demo in a temporary private workspace
2. Run `scripts/release_check.py` on the exact source tree being packaged
3. Exclude workspaces, databases, exports, logs, captures, account/provider state, filled environment files, tokens, cookies, passwords, keys and signed download URLs
4. Keep only explicitly synthetic examples using reserved example domains; never relabel real contacts as synthetic
5. Review license, provider terms, contact-data rights, privacy requirements and hosted/resale restrictions
6. Review the proposed repository diff and archive contents; the checker cannot inspect Git history or recognize every possible secret format
7. Publish only when the user explicitly requests publication and selects the destination

The release checker is a guardrail, not a certification that an arbitrary repository is safe. It rejects runtime artifacts, source-tree symlinks, contact-like data files outside examples, credential-shaped literals and host/session-specific paths. Inspect its findings rather than adding exemptions for private content.

CI uses synthetic fixtures without API credentials. Live compatibility checks require a separate private workspace, the user's own authorized account, a confirmed allowance and explicit execution. A paid service plan or a historical successful response does not grant hosted resale rights.

For future development, prioritize evidence-bound retry/reconciliation, controlled cache freshness, provider contract fixtures and licensed import integrations. Preserve failure and billing history rather than hiding it to make a run appear successful.

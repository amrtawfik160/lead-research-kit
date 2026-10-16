# Browser capture branch

This toolkit does not log into or scrape LinkedIn. Use an authorized export or the browser's normal interface, with the site's applicable terms and access limits. Browser captures are operator evidence, not a license to automate restricted access.

1. Read any available LinkedIn/site/browser skill before interacting with the account. Check for a relevant open tab rather than duplicating it.
2. Inspect the actual current page and accepted filters. For the US real-estate workflow, country and industry must both visibly be accepted. Save the exact observed source URL and observation time. Do not fabricate URLs, profile paths or filter acceptance.
3. Keep one collection/research worker. Use a conservative task budget, at most40 additional result pages per run and at least10 seconds between browser navigations, while also respecting stricter service limits and other account activity. Stop if the remaining account budget is uncertain. These are internal ceilings, not claims about the provider's allowed quota.
4. Capture only rendered evidence: stable profile URL, name, title/headline, company if explicit, location and current-role snippet. Snapshot/ref reading comes first when available. Attempted, blank or transitional pages do not count as captured pages.
5. Save after every accepted page, not only at the end. Persist source URL/query, last captured page, raw records and filter evidence. Resume after the last actual capture; deduplicate canonical profile URLs before incrementing target totals.
6. Hold nonpersons, deceased individuals, contradicted current leadership and unresolved namesakes. Do not substitute another person to fill a row. Company revenue remains unknown without evidence.
7. On a checkpoint, CAPTCHA, login challenge, 403/429/999, access block or commercial-use restriction, save progress and stop. Use only permitted human verification or provider review. No IP/account rotation or alternate endpoint to bypass the restriction.
8. At a natural query end, a normal different relevant leadership query may be used if the service allows it and required filters remain accepted. Do not evade a denied search.
9. Import the saved normalized JSONL with `source_channel: linkedin` and per-record `filter_evidence: {accepted:true,country:...,industry:...}`. These are attestations of what was observed, not tool-side remote verification.

If progress depends on a future budget window, schedule a normal follow-up instead of sleeping in a tool or forcing more reads. Read fresh state on the next run and avoid overlapping an existing research worker. Public-company/directory additions remain a separate acquisition channel and do not count toward a LinkedIn-only target.

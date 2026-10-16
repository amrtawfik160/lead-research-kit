# Security and privacy

Do not post API keys, bearer tokens, passwords, cookies, contact databases or unredacted account responses in public issues. Rotate any credential accidentally exposed and review its provider's account activity.

The workspace is private local data, not an encrypted vault. Filesystem permissions reduce accidental access but do not replace disk encryption, secure backups or a password manager. Environment variables are not stored by the tool, but may still be visible to privileged processes on the host.

Default execution is dry, providers are disabled, budgets are zero, request redirects are denied, and only fixed official provider origins are supported. Stop on an access denial; do not use account, key, IP or host rotation to evade it.

The audit log is tamper-evident, not a signature from a provider or protection against an owner rewriting the whole database. Manual free-budget confirmation is an attestation; APIs with mixed paid/free balances cannot prove the billing source. See the recovery and provider-operation references for limits.

No hosted endpoint, email sender, SMTP probe or automatic account registration is included. No public security reporting address has been configured yet; a future maintainer should add a private reporting channel before publication.

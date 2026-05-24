# Security

Do not include real `auth.json`, account vaults, registry files, rollout logs,
or screenshots containing account identifiers in public reports.

This project is designed for local subscription-backed Codex accounts that the
operator controls. It does not provide hosted credential storage and does not
support sharing OAuth tokens between users.

Security-sensitive reports should include:

- the command used;
- expected behavior;
- observed behavior;
- redacted config shape;
- sandbox reproduction steps when possible.

Never paste token material into an issue.

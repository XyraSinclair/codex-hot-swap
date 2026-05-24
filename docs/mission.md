# Mission

Codex Hot Swap exists to make multiple ChatGPT Pro Codex accounts feel like one
continuous working pool for terminal coding agents.

The user-facing contract is exact:

> As long as any configured account has remaining quota, no active Codex chat
> should surface a quota wall to the operator.

That does not mean hiding errors with brittle terminal scraping. It means
isolating every live Codex process, observing account quota through structured
usage state, migrating only the affected session, and preserving enough context
that the operator can keep working without learning a rescue ritual.

The project is successful when a power user can:

- install the wrapper safely;
- register multiple subscription-backed Codex accounts with local auth vaults;
- run many concurrent Codex chats through one command;
- see current account, tab, daemon, wall, and broken-ledger state with
  `codex-status`;
- enable higher-friction-risk smooth mode deliberately;
- recover cleanly when all accounts are exhausted or when browser reauth is
  genuinely required.

The project is not successful if it merely reduces manual intervention. The
target is a boring, observable, self-healing working loop.

## Non-Negotiables

- Do not expose tokens in logs, tests, docs, screenshots, or errors.
- Do not share a mutable `auth.json` across live sessions.
- Do not rewrite global auth while live tabs exist.
- Do not poll usage metadata casually.
- Do not scrape terminal output for quota-wall strings.
- Do not kill or migrate healthy sessions because of low-confidence signals.
- Do not truncate or delete rollout JSONL.
- Do not silently fall back to API-key billing.

## Porting Standard

The public repo should only ship code that has a sandbox test proving its safety
property. If a capability cannot be tested without real credentials, it belongs
behind an explicit manual command and a documented limitation until a better
harness exists.

# Refresh Broker Design

OAuth refresh tokens are rotating credentials. A successful refresh returns a
new refresh token and invalidates the old one. If two Codex processes refresh
the same copied chain at the same time, one can lose and the local chain may
require browser reauth.

Codex Hot Swap v1 reduces risk by giving each process a private `CODEX_HOME`
and syncing back only fresher auth snapshots under a lock. That prevents global
auth rewrites and shared-file races, but it does not control refreshes that
happen inside upstream Codex after launch.

The v2 refresh broker exists to close that structural gap.

## Goals

- Serialize refresh-token rotation per account.
- Keep token material local and never log it.
- Preserve rollout continuity and per-tab state isolation.
- Make degraded modes explicit instead of silently risking token-chain damage.
- Avoid API-key fallback and billing-surface changes.

## Non-Goals

- Hosted credential storage.
- Sharing OAuth tokens between users.
- Silent browser automation for reauth.
- Patching upstream Codex at install time.

## Constraints

The current Codex CLI reads `auth.json` at startup and refreshes internally.
Without upstream support for an external refresh provider, a local broker cannot
transparently intercept every refresh made by a running Codex process.

That leaves three viable paths.

## Path A: Strict Lease Mode

The broker grants account leases before `codex-safe` launches a tab.

Policy:

- `max_concurrent_tabs_per_account = 1` in strict mode;
- a tab must hold a lease for the account whose auth snapshot it receives;
- stale leases expire when wrapper and child PIDs are dead;
- status shows lease holders;
- if all accounts are leased or walled, new launches wait or fail clearly.

Pros:

- enforceable without upstream Codex changes;
- prevents two live Codex processes from refreshing the same account chain;
- simple to test with fake processes.

Cons:

- reduces concurrency when the account pool is smaller than the chat count;
- may trade friction for account safety.

## Path B: Cooperative Refresh Provider

Codex would need a supported hook or local socket for token refresh:

```text
Codex process -> local broker -> account vault -> refreshed token response
```

The broker would hold the only refresh-capable credential copy for each
account. Child processes would receive access tokens or broker references, not
independent refresh-token chains.

Pros:

- ideal safety and concurrency;
- no copied refresh chains.

Cons:

- requires upstream Codex support that may not exist today;
- must be designed with strict token redaction and local socket permissions.

## Path C: Hybrid Lease + Preflight Refresh

Before launching a tab, the broker refreshes or validates the vault under an
account lock, then hands the wrapper a fresh snapshot. This reduces stale
snapshot failures but still cannot stop an internal refresh race later.

Pros:

- improves v1 without upstream changes;
- can be implemented incrementally.

Cons:

- does not fully serialize refresh after launch;
- should not be advertised as a complete fix.

## Proposed v2 Shape

```text
codex-safe
  |
  | request lease(account candidates, mode)
  v
codex-refresh-broker
  |
  | locks per account
  | validates vault freshness
  | writes one-use tab snapshot
  v
~/.codex/tabs/<uuid>/auth.json
```

Broker state:

```text
~/.codex/accounts/broker/leases.json
~/.codex/accounts/broker/events.jsonl
~/.codex/accounts/broker/<account>.lock
```

Lease record:

```json
{
  "email": "user@example.com",
  "tab_id": "uuid",
  "wrapper_pid": 123,
  "child_pid": 456,
  "created_at": 1779600000,
  "expires_at": 1779603600,
  "mode": "strict"
}
```

Events must never contain token material.

## Operator Modes

`balanced`:

- current v1 behavior;
- prefer fewer live tabs per account;
- sync back fresher snapshots under lock;
- warn when multiple live tabs share an account.

`strict`:

- one live tab per account;
- new launches refuse or wait when all accounts are leased;
- safest mode for token-chain integrity.

`experimental-brokered-refresh`:

- only if upstream Codex exposes a supported refresh hook;
- broker owns refresh-token rotation.

## Tests Required

- two wrappers contend for one strict lease; one wins, one waits/fails;
- dead wrapper/child PIDs release stale leases;
- broker never logs auth JSON fields;
- balanced mode warns on shared account concurrency;
- strict mode still avoids fresh quota walls;
- lease state survives daemon restart;
- manual browser reauth updates vault and clears broken markers.

## Recommendation

Ship v1 as the tested default with clear residual-risk documentation. Add
strict lease mode before claiming complete refresh-token serialization. Pursue
cooperative refresh only if Codex exposes a stable integration point.

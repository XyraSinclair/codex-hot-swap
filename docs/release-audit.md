# Release Gate Audit

Audit date: 2026-05-24.

Primary verifier:

```bash
make check
```

`make check` runs Python compilation, shell syntax checks, unit tests, fake
Codex/fake codex-auth/fake cmux integration tests, installer tests, and
`git diff --check`.

## Evidence Matrix

| Gate | Evidence | Status |
| --- | --- | --- |
| `./install.sh --dry-run` changes nothing | `tests/run-tests.sh` verifies no installed `codex-safe` after dry-run | Proven |
| Default install copies scripts/config only | `tests/run-tests.sh` checks installed scripts/config and no `.zshrc` or LaunchAgents | Proven |
| Daemon install is opt-in | Default install test verifies no LaunchAgents; plist render is separate from bootstrap | Proven |
| Alias install is opt-in and idempotent | `tests/run-tests.sh` runs `--with-alias` twice and checks one alias | Proven |
| Existing config is not overwritten | `tests/run-tests.sh` writes custom config then re-runs installer | Proven |
| Launchd plist can be tested without bootstrap | `./install.sh --render-launchd-plist PATH` and tests verify plist fields | Proven |
| Tests use sandbox homes | `tests/run-tests.sh` creates a temp sandbox and sets `CODEX_HOME`/`HOME` for commands that would write state | Proven for automated gates |
| Fake CLIs cover relied-on behavior | `tests/fakes/codex`, `tests/fakes/codex-auth`, and `tests/fakes/cmux` exercise wrapper/daemon/rescue contracts | Proven |
| Normal child exit code is preserved | `tests/run-tests.sh` asserts fake Codex exit `42` propagates | Proven |
| Management subcommands pass through | `login`, `mcp list`, and `completion zsh` pass-through tests | Proven |
| Broad quota text does not trigger broken marking | Fake Codex prints `You've hit your usage limit` with exit 0; no broken ledger | Proven |
| Auth failure requires nonzero child exit | Auth marker with exit 0 does not mark broken; marker with exit 1 does | Proven |
| Stale tab auth does not mark fresh vault broken | Fake Codex advances vault mtime during auth failure; no broken ledger | Proven |
| Live tabs get private `CODEX_HOME` | Fake Codex logs per-tab home and tab auth copy exists | Proven |
| New launches avoid fresh wall cache | Wall cache for account A causes new launch on account B | Proven |
| Stale wall cache ignored | Unit test for old `written_at` | Proven |
| Expired reset windows ignored | Unit test for expired reset | Proven |
| Active weekly wall survives expired 5h wall | Unit test for mixed windows | Proven |
| Migration excludes walled accounts | Live migration test relaunches from A to B with A walled | Proven |
| All accounts walled reports cleanly | Fake wall cache for all accounts returns no usable account and never launches fake Codex | Proven |
| Usage refresh defaults off | Fake codex-auth configured to fail if called; daemon `--once` succeeds without calling it | Proven |
| Daemon refuses global auth rewrite with live tabs | Same fake codex-auth failure guard with live tab and `switch_default=true` | Proven |
| Daemon avoids default flapping inside tolerance | Previous selected account remains selected when candidate improvement is within tolerance | Proven |
| Daemon state has timestamps for status | `predictive_state.json` includes `written_at`; status tests consume it | Proven |
| Rollout lookup uses tab SQLite | Unit test selects latest `threads.rollout_path` by `updated_at` | Proven |
| SQLite read is read-only/immutable | `latest_rollout_from_sqlite()` opens `file:...?mode=ro&immutable=1` | Proven by code inspection |
| Wrapped tabs record cmux metadata | Fake launch with cmux env verifies tab JSON metadata | Proven |
| Transfer prompt preserves user and assistant context | `codex-continue --print` test checks both user and assistant text | Proven |
| Migration launches interactive Codex, not `codex exec` | Live migration fake logs second launch argv with transfer prompt; `codex-validate` separately tests `codex exec` | Proven |
| Codex CLI prompt support is probed | Unit test accepts supported help text and rejects unsupported help text | Proven |
| cmux rescue validates terminal surface before send | Fake cmux valid/stale/non-terminal tests | Proven |
| Non-cmux/stale surfaces degrade to reporting | Fake non-cmux and stale cmux tests send nothing | Proven |
| Status reports remaining quota terms | `codex-status` displays 5h/weekly remaining percentages | Proven by code inspection and status test |
| Status distinguishes broken/walled/low/stale/unknown | Status integration test checks all flags and stale daemon/fresh wall cache | Proven |
| Status never prints tokens | Status reads registry metadata, wall cache, predictive state, and tab state; it never opens auth vaults | Proven by code inspection |
| README and docs explain safe install, smooth mode, token risk, usage polling, daemon/wrapper split, and troubleshooting | README plus `docs/safety.md`, `docs/architecture.md`, `docs/troubleshooting.md` | Proven |

## Residual Risk

The v1 architecture isolates mutable state and avoids unsafe global rewrites,
but it cannot fully serialize refresh-token rotation inside upstream Codex.
Once Codex has a copied `auth.json`, its internal OAuth refresh behavior is not
brokered by this repo. The current mitigation is:

- account ranking that prefers fewer live tabs per account;
- per-tab snapshots instead of shared mutable auth;
- vault sync-back under an account-specific lock;
- stale-snapshot detection before marking accounts broken;
- explicit browser OAuth repair path for truly dead chains.

The structural fix is the v2 refresh broker design in
[refresh-broker.md](refresh-broker.md). Until upstream Codex exposes a refresh
hook or reloadable token source, strict serialization requires a conservative
lease mode that limits concurrent tabs per account.

## Current Release Status

The automated v1 release gates are proven by `make check` and GitHub Actions.
The repo remains labeled as a staging repo because the refresh-broker work is a
known architectural v2 safety improvement, not because the current public code
is untested.

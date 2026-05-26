# Changelog

## v0.1.5 — sub-5s tab startup + refresh-token race fix

Two independent fixes that together make multi-account hot-swap feel
operational rather than fragile.

**Startup speed (7x faster).** When codex 0.132+ runs against a tab dir
without a pre-existing `logs_2.sqlite`, it spends ~9 seconds of CPU on cold-
start schema setup every single launch. Tab dirs were tab-private for this
file. Result: a wrapped `codex exec "Reply OK"` took ~19s instead of ~4s. The
DB is a write-shared logging table (SQLite WAL handles concurrent writers
fine), so adding it to `SHARED_TAB_NAMES` lets every tab symlink back to
the global file. Measured: 19s → 3.9s end-to-end through the wrapper.

**Refresh-token race.** Two codex processes sharing the same OAuth refresh
token race on token rotation: whichever loses gets the
`refresh_token_reused` 401 and the RT chain is permanently broken until the
account is re-OAuthed. Vault file locking can't close the window because
codex CLI does the refresh inside its own process. `pick_account` now hard-
excludes accounts with a live tab; new chats land on a different account.
Override with `CODEX_SAFE_ALLOW_SHARED_ACCOUNTS=1` for legacy workflows.
When every candidate is occupied the rule relaxes rather than starve the
chat.

Added tests: `test_logs_sqlite_symlinked_into_tab_home`,
`test_pick_account_prefers_unoccupied_accounts`.

## v0.1.4 — per-tab account pinning fix

Before this release, every tab inherited the global `active_account_key` from
`~/.codex/accounts/registry.json` regardless of which vault file the wrapper
copied into the tab's `auth.json`. Codex 0.132+ reads `accounts/registry.json`
to decide which account is active and loads
`accounts/<base64url(account_key)>.auth.json` for the actual token. Result: the
wrapper "pinned to gmail" while codex actually ran as whichever account was
globally active, producing wrong-account quota walls on every chat.

`create_tab_home` now materializes a per-tab `accounts/` directory with a
single-account `registry.json` whose `active_account_key` matches the chosen
account, plus the chosen vault under the codex-expected base64url filename.
Top-level `auth.json`, per-tab `accounts/registry.json`, and the per-account
vault file all agree.

Added a regression test in `tests/test_lib.py` covering: per-tab `accounts/`
is a real directory (not a symlink), the registry contains only the chosen
account, `active_account_key` matches, and the vault is materialized under
the expected filename.

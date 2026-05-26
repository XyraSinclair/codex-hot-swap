# Changelog

## v0.2.0 — operational hot-swap

Major rebase. The lib in v0.1.x had a design that diverged from what
actually works in production. This release ports the live, battle-tested
install (the one carrying the heavy real-world load) onto the public repo
as the new source of truth. The pre-v0.2.0 public API is preserved as a
thin compat shim so existing scripts and integrations keep working.

### What's new

* **Per-tab `accounts/` pinning.** Each tab gets a single-account
  `registry.json` with its `active_account_key` set to the chosen account.
  Without this, codex 0.132+ reads the global active account and pins every
  tab to the same one regardless of which vault the wrapper copied. This is
  THE root-cause fix for "wrapper said gmail, codex actually used proton".
* **Sessions symlinked back to global.** Rollouts survive
  `cleanup_tab_home` — `codex resume <id>` works after a tab exits.
  `_rescue_sessions` mirrors any legacy tab-private rollouts into the
  global tree on first cleanup so existing tabs aren't lost.
* **`logs_2.sqlite` shared back to global** (was tab-private). Codex was
  spending ~9 seconds of CPU on schema bootstrap every launch. Measured:
  wrapped `codex exec` 19s → ~4s end-to-end.
* **Mid-stream quota-wall detection in `codex-safe`.** The exact codex CLI
  fingerprint (`"You've hit your usage limit. Visit
  https://chatgpt.com/codex/settings/usage"`) is tight enough to safely
  match in-stream without false-positiving on model output. On match: kill
  codex, persist the wall, repick another account, retry. User sees a
  single banner instead of a dead chat.
* **Persisted quota walls** at `accounts/recover/quota-walled.json` with
  parsed reset epochs, 15-minute defensive default when reset text is
  unparseable, auto-expiry.
* **Verified-working ledger** at `accounts/recover/verified-working.json`.
  After every successful run, the account is stamped. `rank_accounts`
  treats verified-working as the top sort tier, beating the (sometimes
  lying) usage API.
* **Observed-token tracking** at `accounts/recover/usage-observed.json`.
  Scraped from codex's `tokens used N` trailer. Used for predictive
  headroom calculation.
* **Predictive headroom ranking.** Accounts approaching the observed 5h cap
  rank LAST so the wrapper rotates BEFORE the wall, not after.
* **One-tab-per-account rule** (override:
  `CODEX_SAFE_ALLOW_SHARED_ACCOUNTS=1`). Two codex processes sharing one
  refresh token race; the loser gets `refresh_token_reused`. Hard-excluding
  occupied accounts eliminates that race.
* **Per-account refresh lock** (`_exclusive_lock`) around all vault
  reads/writes. Periodic tab→vault sync inside the PTY loop pushes
  freshly-refreshed RTs back to the vault every 30s.
* **`next-account.json` daemon hint.** The predictive daemon precomputes
  the next best account; the wrapper's cold-start is now a single-file
  read instead of a full ranker rebuild.
* **Live-tab wall notifier.** The daemon writes `.walled-notice` files
  into tab dirs whose pinned account is walled, so status lines / menubars
  can surface affected tabs.
* **`codex-evac` utility.** Migrate a walled cmux pane onto a fresh
  account without losing conversation context. Runs from inside the
  affected pane, SIGTERMs codex, re-execs `codex resume <rollout>` via
  the wrapper.

### Compatibility

`wall_cache_path`, `write_quota_wall_cache`,
`codex_interactive_prompt_supported`, `latest_rollout_from_sqlite`,
`find_account` are retained as compat shims that dispatch to the new
canonical paths or no-op when the concept has been superseded.

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

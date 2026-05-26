# Changelog

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

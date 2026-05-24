# Troubleshooting

## `codex-safe` says no usable account is available

Check:

```bash
codex-status
```

Common causes:

- no account registry exists;
- account vault files are missing;
- all accounts are in the broken ledger;
- all accounts are freshly quota-walled.

If a refresh chain is genuinely broken, browser reauth is the correct repair
path. Do not restore old refresh-token backups.

## Smooth mode refuses to enable

`codex-smooth-mode --enable` refuses while live tabs exist. That is deliberate:
smooth mode turns on usage refresh polling and proactive migration thresholds.

Wait for live tabs to exit, or use `--allow-live` only after accepting that
existing sessions may migrate based on cached low-quota state.

## Quota wall cache looks wrong

The wrapper ignores wall cache files that are stale or whose reset windows have
expired. Run:

```bash
codex-predictive-daemon --once
codex-status
```

Usage refresh still only happens if `refresh_codex_auth_usage` is enabled in
the config.

## A live chat did not migrate

Check:

- `CODEX_SAFE_NO_AUTO_QUOTA_MIGRATE` is not set;
- the daemon has written a fresh wall cache;
- the account email in the tab state matches the wall cache;
- the terminal has been idle for `live_migrate_idle_seconds`;
- migration has not exceeded `max_live_migrations_per_tab`.

The wrapper intentionally does not scrape terminal text for quota strings.

## A chat migrated but lost tool state

Migration reconstructs context from rollout JSONL. Tool calls, tool outputs,
MCP server state, and hidden process state may be missing. The transferred
prompt should tell the new agent to re-orient before continuing.

## The daemon does not switch the global default

Global auth switching is disabled by default. Even when enabled, the daemon
refuses to switch while live tabs exist.

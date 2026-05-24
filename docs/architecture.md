# Architecture

Codex Hot Swap splits the system into an observer and an actor.

- The daemon observes account state and writes atomic cache files.
- The wrapper owns process launch, per-tab isolation, and live migration.
- Status tools read the same state files and explain what is happening.

This prevents one dangerous pattern: letting every process independently decide
when to poll usage APIs, switch global auth, or kill a live chat.

## Data Roots

The default Codex home is:

```text
~/.codex/
```

Account helpers commonly maintain:

```text
~/.codex/accounts/registry.json
~/.codex/accounts/<account-key>.auth.json
```

Each wrapped Codex launch gets:

```text
~/.codex/tabs/<uuid>/
```

The tab home keeps mutable account/process state private while symlinking
durable shared assets such as configuration, skills, plugins, and rollout
sessions.

## Shared vs Private State

Usually shared:

```text
config.toml
hooks.json
sessions/
skills/
plugins/
```

Usually private per tab:

```text
auth.json
history.jsonl
session_index.jsonl
state.db
state_5.sqlite
logs_2.sqlite
sqlite/
tmp/
log/
debug/
shell_snapshots/
```

`sessions/` is shared because rollout JSONL is append-only evidence and the
source for context transfer. SQLite thread indexes stay private because they can
make one account try to resume another account's thread.

## Quota Wall Flow

```text
codex-predictive-daemon
  |
  | optional usage refresh
  v
registry/account usage data
  |
  | exact 0-percent windows only
  v
predictive_quota_walls.json
  |
  | fresh file + reset time not expired
  v
codex-safe live migration
```

The wall cache must include:

- write timestamp;
- source timestamp if available;
- per-account 5h and weekly remaining percentages;
- reset timestamps;
- freshness/expiry semantics.

Wrappers must ignore stale cache files and expired reset windows.

## Migration Flow

When a pinned account is freshly walled:

1. wait for an idle terminal window;
2. terminate only that child process;
3. locate the tab's authoritative rollout from its own `state_5.sqlite`;
4. build a transfer prompt from local rollout JSONL;
5. choose a healthy account excluding all fresh walls and failed migration
   targets;
6. launch interactive Codex with the transfer prompt as initial input;
7. preserve the old rollout and write observable tab state.

The transfer prompt is not magic. Tool calls, MCP server state, and hidden
process state can be lost. The prompt must say that explicitly so the new agent
re-orients instead of assuming invisible continuity.

## Global Default Switching

Global default auth switching is allowed only when no live tabs exist. While
tabs are live, the wrapper should choose account vaults directly for new
launches and leave global `~/.codex/auth.json` alone.

# Codex Hot Swap

Make several ChatGPT Pro Codex accounts behave like one reliable working pool.

Codex Hot Swap is a local-first wrapper and daemon design for subscription-backed
Codex CLI sessions. The promise is simple: if any configured account still has
usable quota, a working chat should not strand the operator at a visible quota
wall. Running sessions get isolated auth snapshots, quota state comes from a
structured cache, and context migration happens locally from append-only rollout
logs.

> Status: v1 automated release gates are green. The remaining major design item
> is optional v2 strict refresh-token serialization, documented in
> [docs/refresh-broker.md](docs/refresh-broker.md).

## Goal

Codex Hot Swap will make multiple ChatGPT Pro Codex accounts behave like one
continuous working pool, without exposing quota exhaustion to the person using
the terminal. If any configured account still has usable quota, active Codex
chats should keep working in place: preserving rollouts, avoiding token-chain
damage, and migrating only when the system has a fresh, authoritative quota
signal. The standard is not "less manual recovery"; it is "no manual recovery
during normal work."

The public repo will ship a clean, reproducible toolchain: safe installer
defaults, clear commands, conservative daemon behavior, sandboxed tests, honest
limitations, and documentation that lets another power user get the same smooth
experience without private knowledge from one machine. It must protect OAuth
refresh-token chains above all else, never rewrite global auth while live tabs
exist, never poll the Usage API casually, never delete rollouts, and never
disrupt healthy chats based on terminal-string guesses. The daemon observes
quota state; the wrapper acts locally; shared state is atomic, fresh, and
inspectable.

Success means a user can install Codex Hot Swap, add accounts, run Codex through
the wrapper, enable smooth mode deliberately, and trust that `codex-status`
tells the truth about accounts, live tabs, quota walls, daemon health, and
recovery options. When automation cannot safely recover, the failure should be
explicit and calm, not silent, destructive, or mysterious.

## Quick Start

Prerequisites:

- macOS or Linux shell environment;
- `python3`;
- Codex CLI installed as `codex`;
- a local multi-account Codex registry, typically from `codex-auth`, with
  account vaults under `~/.codex/accounts/`.

Install:

```bash
git clone https://github.com/XyraSinclair/codex-hot-swap.git
cd codex-hot-swap
make check
./install.sh --trial --dry-run
./install.sh --trial
./install.sh --dry-run
./install.sh
```

If you already have local scripts named `codex-safe`, `codex-status`, or similar
in the target prefix, the installer refuses to overwrite them unless they are
known to be from this installer. Use a side-by-side prefix while evaluating:

```bash
./install.sh --prefix "$HOME/.local/codex-hot-swap/bin"
```

Use without changing your shell aliases:

```bash
codex-safe
codex-status
```

Use a side-by-side trial without changing the active `codex` command:

```bash
"$HOME/.local/codex-hot-swap/trial/bin/codex-safe" --help
CODEX_HOME="$HOME/.codex" "$HOME/.local/codex-hot-swap/trial/bin/codex-status"
```

Optional alias:

```bash
./install.sh --with-alias
```

Optional daemon:

```bash
./install.sh --with-daemon
```

Optional smooth mode, which enables usage refresh polling and proactive
migration:

```bash
codex-smooth-mode --enable
```

If live tabs are already running, smooth mode refuses unless you pass
`--allow-live`.

Uninstall:

```bash
./install.sh --uninstall
```

Uninstall removes only files the installer can prove it owns. It keeps accounts,
credentials, tab homes, rollout logs, and `codex-hotswap.json` by default.
Pass `--purge-config` only if you also want to remove this tool's config file.

## What This Solves

The stock Codex CLI reads `~/.codex/auth.json` at startup, keeps tokens in
memory, and writes conversation rollouts under `~/.codex/sessions/`. Changing
the global auth file helps only future launches. A live session already pinned
to an exhausted account can print a usage-limit wall and sit there.

Codex Hot Swap separates responsibilities:

| Layer | Responsibility |
| --- | --- |
| `codex-safe` | Launch each Codex process with a private per-tab `CODEX_HOME` and a copied account auth snapshot. |
| Predictive daemon | Read account usage state at a configured cadence and publish an atomic quota-wall cache. |
| Wall cache | Record fresh, account-specific 0-percent quota windows with reset timestamps. |
| Context transfer | Reconstruct enough context from rollout JSONL to relaunch on a healthy account. |
| Status tools | Show live tabs, pinned accounts, quota walls, broken accounts, and daemon health. |

The key rule: terminal output is not a safe quota signal. Quota detection must
flow from structured account usage state, not from text like "rate limit" or
"usage limit" that can appear in normal model output.

## Safety Model

Codex Hot Swap is built around four invariants:

1. Never let live sessions share one mutable auth file.
2. Never rewrite global `auth.json` while live tabs exist.
3. Never poll usage metadata unless the operator deliberately enables it.
4. Never truncate or delete Codex rollout JSONL.

OAuth refresh tokens rotate. Two processes refreshing the same chain at the
same time can permanently break that local chain until browser reauth. The
wrapper therefore copies account auth into a private tab home, syncs back only
fresher token material under a lock, and treats old tab snapshots differently
from broken accounts.

## Command Surface

Implemented in v1:

```bash
codex-safe --help
codex-status
codex-predictive-daemon --once
codex-smooth-mode --enable
codex-validate --to <email>
codex-continue --switch --launch
codex-rescue
codex-rescue --apply --yes
```

Shell aliases are optional and explicit:

```bash
alias codex='codex-safe'
```

The installer will not add aliases, start launchd, or enable smooth mode unless
asked.

## Install Shape

The final installer must be safe by default:

```bash
./install.sh --dry-run
./install.sh --trial
./install.sh
./install.sh --uninstall
```

Optional behavior must be opt-in:

```bash
./install.sh --with-daemon
./install.sh --with-alias
codex-smooth-mode --enable
```

Smooth mode deliberately enables usage refresh polling and proactive migration.
It must refuse to enable while live tabs exist unless the operator passes an
explicit force-style flag.

## Architecture

```text
codex-safe
  |
  | chooses account from registry + wall cache
  v
~/.codex/tabs/<uuid>/auth.json      ~/.codex/accounts/*.auth.json
~/.codex/tabs/<uuid>/state_5.sqlite          ^
~/.codex/tabs/<uuid>/tab.json                |
  |                                          |
  | launches Codex with CODEX_HOME           | locked fresher sync-back
  v                                          |
codex CLI process --------------------------+

codex-predictive-daemon
  |
  | optional usage refresh, never casual polling
  v
~/.codex/predictive_quota_walls.json
  |
  | fresh structured signal only
  v
codex-safe live migration
```

## Release Gates

The automated v1 gates in [docs/release-gates.md](docs/release-gates.md) are
mapped to evidence in [docs/release-audit.md](docs/release-audit.md). In short:

- sandbox tests prove real credentials are never touched;
- broad quota text in output cannot trigger migration;
- stale wall cache entries are ignored;
- expired reset windows are ignored;
- global auth rewrites are refused while live tabs exist;
- migration launches interactive Codex, not `codex exec`;
- installer defaults do not start services or edit shell rc files.

Current sandbox evidence is available with:

```bash
make check
```

## Documentation

- [Mission](docs/mission.md)
- [Architecture](docs/architecture.md)
- [Safety model](docs/safety.md)
- [Release gates](docs/release-gates.md)
- [Release audit](docs/release-audit.md)
- [Safe side-by-side trial](docs/safe-trial.md)
- [Codex compatibility](docs/codex-compatibility.md)
- [cmux rescue](docs/cmux-rescue.md)
- [Refresh broker design](docs/refresh-broker.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Roadmap](docs/roadmap.md)
- [Contributing](CONTRIBUTING.md)

## License

MIT. See [LICENSE](LICENSE).

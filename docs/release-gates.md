# Release Gates

This checklist defines what "shippable" means for Codex Hot Swap.

## Installer Gates

- `./install.sh --dry-run` prints intended actions and changes nothing.
- `./install.sh` copies scripts/config only.
- Default install does not start launchd.
- Default install does not edit shell rc files.
- Optional daemon install is gated behind `--with-daemon`.
- Optional alias install is gated behind `--with-alias`.
- Re-running install is idempotent and does not overwrite user config.
- Uninstall instructions are printed after install.
- Launchd plist rendering can be tested without bootstrapping launchd.

## Sandbox Gates

- Every test runs under a temporary `CODEX_HOME`.
- Tests fail if real `~/.codex/auth.json` is read or written.
- Tests fail if real launchd state is touched.
- Fake `codex` and fake `codex-auth` model the production behaviors relied on
  by the wrapper.

Current local gate:

```bash
make check
```

## Wrapper Gates

- Normal child exit code is preserved.
- `login`, `mcp`, and shell completion subcommands pass through unwrapped.
- Broad quota text in output does not trigger a swap.
- Auth failure is classified only after nonzero child exit.
- Stale tab auth does not mark a fresh vault broken.
- Live tabs get distinct private `CODEX_HOME` directories.
- New launches avoid accounts in a fresh quota-wall cache.
- Stale quota-wall cache entries are ignored.
- Expired reset windows are ignored.
- Active weekly walls are not hidden by expired 5h state.
- Migration excludes every currently walled account.
- Migration rate limits loops and reports all-accounts-walled cleanly.

## Daemon Gates

- Usage refresh defaults off.
- Daemon writes quota wall cache atomically.
- Daemon refuses global auth rewrites while live tabs exist.
- Daemon never flaps defaults inside the configured tolerance band.
- Daemon state includes enough timestamps for status tools to detect staleness.

## Migration Gates

- Rollout lookup uses the tab's own `state_5.sqlite`, not cwd matching.
- SQLite reads use read-only/immutable access where supported.
- Transfer prompt prioritizes user intent but preserves assistant context when
  budget allows.
- Migration launches interactive Codex, not `codex exec`.
- Migration probes the installed Codex CLI for interactive initial-prompt
  support before relaunch.
- Send-keys based rescue validates the target terminal surface before acting.
- Cursor/non-cmux terminals degrade to explicit reporting rather than unsafe
  relaunch attempts.

## Status Gates

- `codex-status` reports accounts in remaining-quota terms.
- Status distinguishes broken, walled, low, stale, and unknown.
- Status shows daemon freshness.
- Status shows live tabs and pinned accounts.
- Status never prints tokens.

## Documentation Gates

- README explains safe default install and opt-in smooth mode.
- Security docs explain token-chain risk and usage polling risk.
- Architecture docs explain daemon-as-observer and wrapper-as-actor.
- Troubleshooting includes all-accounts-walled, broken refresh chain, stale
  wall cache, daemon stopped, and inaccessible terminal surface.

# Safe Side-by-Side Trial

Use trial mode when you want to evaluate Codex Hot Swap without disturbing an
existing local wrapper, shell alias, LaunchAgent, Codex config, accounts, tabs,
or rollout logs.

```bash
./install.sh --trial --dry-run
./install.sh --trial
```

Trial mode installs only scripts, by default under:

```text
~/.local/codex-hot-swap/trial/bin/
```

It does not:

- add `alias codex='codex-safe'`;
- start or render launchd jobs;
- write `~/.codex/codex-hotswap.json`;
- copy credentials;
- modify account vaults;
- modify tab homes;
- modify rollout JSONL.

Run commands by absolute path while evaluating:

```bash
"$HOME/.local/codex-hot-swap/trial/bin/codex-safe" --help
CODEX_HOME="$HOME/.codex" "$HOME/.local/codex-hot-swap/trial/bin/codex-status"
```

The status command reads registry, wall-cache, predictive-state, and tab metadata.
It does not open auth vaults and does not call `codex-auth list`.

During a transition from an older private install, trial status should still be
able to read:

- list-shaped `codex-auth` registries;
- base64url `account_key` vault filenames;
- `last_usage.primary` and `last_usage.secondary` quota windows;
- older `pinned_email` + `pid` tab metadata;
- older daemon `updated_at_iso` timestamps.

Uninstall the trial:

```bash
./install.sh --trial --uninstall
```

Trial uninstall uses the manifest stored inside the trial prefix and removes only
owned files. It leaves the existing live install untouched.

## Safe Promotion Checklist

Promote a trial to the default `codex` command only after:

- current live sessions are either complete or explicitly accepted as safe to
  leave on the old wrapper;
- `make check` passes;
- `./install.sh --dry-run` reports no unmanaged-file conflicts, or you choose a
  deliberate side-by-side prefix;
- `codex-status` from the trial binary reports expected accounts and tabs;
- no daemon or alias changes are made unless intentionally requested.

Do not run `codex-smooth-mode --enable`, `./install.sh --with-daemon`, or
`./install.sh --with-alias` as part of a trial.

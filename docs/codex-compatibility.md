# Codex CLI Compatibility

Codex Hot Swap depends on two Codex launch modes:

- interactive Codex with an initial prompt: `codex [OPTIONS] [PROMPT]`;
- one-shot validation: `codex exec [OPTIONS] [PROMPT]`.

Migration must use the interactive mode. `codex exec` runs non-interactively and
exits, so it is valid for `codex-validate` but not for live chat migration.

The wrapper probes `codex --help` before using an automatic transfer prompt. If
the installed Codex CLI does not advertise interactive prompt support, migration
refuses to relaunch instead of silently falling back to `codex exec`.

Known local compatibility:

| Codex CLI | Evidence | Result |
| --- | --- | --- |
| `codex-cli 0.132.0` | local `codex --help` shows `Usage: codex [OPTIONS] [PROMPT]` | interactive initial prompt supported |

Re-run the local check with:

```bash
codex --version
codex --help | sed -n '1,20p'
```

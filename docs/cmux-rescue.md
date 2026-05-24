# cmux Rescue

`codex-safe` records cmux metadata from the launch environment when present:

- `CMUX_WORKSPACE_ID`;
- `CMUX_PANEL_ID`;
- `CMUX_TAB_ID`;
- `CMUX_SURFACE_ID` when available.

`codex-rescue` uses that metadata only for tabs pinned to a fresh structured
quota wall. It does not act on terminal output text.

## Default Mode

```bash
codex-rescue
```

Default mode reports affected tabs and whether rescue is blocked or ready. It
does not kill processes, send text, or start new Codex sessions.

## Apply Mode

```bash
codex-rescue --apply --yes
```

Apply mode relaunches only when all checks pass:

1. the tab has cmux metadata;
2. `cmux --json list-pane-surfaces` can see the captured pane;
3. the captured surface is still present;
4. the captured surface is a terminal;
5. `cmux read-screen` matches a shell prompt regex.

Then it sends:

```bash
CODEX_HOME=<home> codex-continue --tab-home <tab-home> --launch
```

and presses Enter via `cmux send-key`.

## Safety Behavior

Rescue refuses to send into:

- stale pane/surface identifiers;
- browser or other non-terminal surfaces;
- tabs with no cmux metadata;
- terminals that do not show a shell prompt before timeout.

That means some sessions require manual recovery. This is deliberate: a false
positive send into the wrong active chat can destroy continuity.

## Testing

The sandbox suite includes a fake `cmux` that proves:

- valid terminal surfaces receive the recovery command;
- stale surfaces do not receive any send command;
- non-terminal surfaces do not receive any send command;
- non-cmux tabs degrade to explicit reporting.

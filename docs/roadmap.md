# Roadmap

## Phase 0: Public Scaffold

- Capture mission, architecture, safety model, and release gates.
- Create a public-safe README.
- Add conservative installer placeholder.
- Keep the repo honest that production code is still being ported.

## Phase 1: Port Proven Codex Implementation

- Port `codex_hot_swap_lib.py`.
- Port `codex-safe`.
- Port `codex-predictive-daemon`.
- Port `codex-status`.
- Port `codex-continue`.
- Port `codex-smooth-mode`.
- Port sandbox fake `codex` and fake `codex-auth` tests.

## Phase 2: Harden Public Installer

- Default to script/config install only.
- Make launchd opt-in.
- Make shell aliases opt-in.
- Add `--dry-run`.
- Add sandbox installer tests.
- Add uninstall instructions.

## Phase 3: Verify Smooth Mode

- Prove usage refresh defaults off.
- Prove smooth mode refuses to enable while live tabs exist unless explicitly
  allowed.
- Prove wrappers use cached wall state rather than polling Usage API.
- Prove all-accounts-walled exits cleanly.

## Phase 4: Publish

- Run release gates.
- Create GitHub repo `codex-hot-swap`.
- Push public main branch.
- Open issues for non-blocking v2 work.

## Phase 5: v2 Refresh Broker

The structural v2 is a local refresh broker that serializes refresh-token
rotation per account. That can reduce the risk inherent in multiple processes
holding snapshots of one rotating chain.

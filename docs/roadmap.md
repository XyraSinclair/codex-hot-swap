# Roadmap

## Phase 0: Public Scaffold

- Done: capture mission, architecture, safety model, and release gates.
- Done: create a public-safe README.
- Done: publish public GitHub repo.
- Done: keep the repo honest that full production release gates are still open.

## Phase 1: Port Proven Codex Implementation

- Done: port core `codex_hot_swap_lib.py` helpers.
- Done: implement `codex-safe`.
- Done: implement `codex-predictive-daemon`.
- Done: implement `codex-status`.
- Done: implement `codex-continue`.
- Done: implement `codex-validate`.
- Done: implement cmux-aware `codex-rescue` with stale-surface validation.
- Done: implement `codex-smooth-mode`.
- Done: add sandbox fake `codex` and fake `codex-auth` tests.
- Done: replace private-checkout assumptions with public tests and release-gate
  evidence.

## Phase 2: Harden Public Installer

- Done: default to script/config install only.
- Done: make launchd opt-in.
- Done: make shell aliases opt-in.
- Done: add `--dry-run`.
- Done: add sandbox installer tests.
- Done: add uninstall instructions.
- Done: render launchd plist to an arbitrary path without bootstrapping launchd.

## Phase 3: Verify Smooth Mode

- Done: prove usage refresh defaults off.
- Done: prove smooth mode refuses to enable while live tabs exist unless explicitly
  allowed.
- Done: prove wrappers use cached wall state rather than polling Usage API.
- Done: prove all-accounts-walled exits cleanly.
- Done: prove live migration relaunches with reconstructed prompt under a
  long-running fake Codex process.
- Done: prove Codex CLI interactive prompt probing accepts supported help text
  and rejects unsupported help text.
- Done: prove cmux rescue validates terminal surfaces, rejects stale surfaces,
  and degrades safely for non-cmux tabs.

## Phase 4: Publish

- Done: create GitHub repo `codex-hot-swap`.
- Done: push public main branch.
- Done: run automated v1 release gates.
- Done: document release-gate evidence in `docs/release-audit.md`.
- Done: open and resolve tracked hardening issues for v1.

## Phase 5: v2 Refresh Broker

The structural v2 is a local refresh broker that serializes refresh-token
rotation per account. That can reduce the risk inherent in multiple processes
holding snapshots of one rotating chain.

- Done: document the broker design, feasible modes, and upstream constraints in
  `docs/refresh-broker.md`.
- Future: implement strict lease mode if the project wants complete
  refresh-token serialization without upstream Codex changes.

# Safety Model

This project exists near credential material. Safety is a product requirement,
not an implementation detail.

## Credential Rules

- Treat `auth.json`, account vaults, and OAuth refresh tokens as
  password-equivalent.
- Never print token material.
- Never commit local Codex state.
- Never create example files with real account identifiers unless they are
  obviously redacted.
- Never store old refresh-token files as "backups"; rotating refresh chains do
  not work that way.

## Refresh-Token Integrity

OAuth refresh tokens rotate. A successful refresh returns a new token and
invalidates the old one. Two live processes using the same refresh chain can
race and break the local chain.

Required defenses:

- per-tab `auth.json` snapshots;
- account-specific locks for sync-back;
- stale-snapshot detection before marking an account broken;
- browser OAuth as the clear repair path for truly dead chains.

These are v1 mitigations. Full serialization of refresh-token rotation requires
a refresh broker or a strict account lease mode. See
[`refresh-broker.md`](refresh-broker.md).

## Usage API Risk

Usage polling is the only authoritative quota source, but excessive polling may
carry account risk. Therefore:

- usage refresh defaults off;
- smooth mode is explicit;
- polling interval is configurable;
- wrappers consume cached state and must not poll per loop iteration.

## False-Positive Disruption

Healthy sessions must not be killed because a model said "quota" in a normal
answer. The wrapper may classify:

- structured fresh quota walls from the daemon cache;
- narrow auth-chain failures after child process exit.

The wrapper must not classify:

- broad terminal output;
- low-confidence `401`/`Unauthorized` strings;
- stale wall cache entries;
- expired reset windows.

## Rollout Preservation

Rollouts are append-only. Project tooling may read rollout JSONL and point new
sessions at transfer prompts, but must not truncate, rewrite, or delete
rollout files.

## Public Repo Hygiene

The repository `.gitignore` must exclude local credentials and state files.
Tests must create sandbox homes and prove they do not touch real `~/.codex`.

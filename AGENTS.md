# Agent Notes

This repository handles local auth-adjacent tooling. Keep changes conservative.

- Do not read or write real `~/.codex` during tests.
- Do not invoke real `codex-auth list` from automated tests.
- Do not start launchd from tests.
- Do not log token material.
- Prefer sandbox homes, fake CLIs, and explicit fixtures.
- Treat the release gates in `docs/release-gates.md` as the definition of done.

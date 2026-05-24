#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
os.sys.path.insert(0, str(REPO / "bin"))

from codex_hot_swap_lib import (  # noqa: E402
    account_states,
    load_config,
    mark_broken,
    pick_account,
    quota_walled_emails,
    wall_cache_path,
    write_json_atomic,
    write_quota_wall_cache,
)
from codex_safe_import import migration_reason_for  # noqa: E402


class HotSwapLibTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "codex"
        (self.home / "accounts").mkdir(parents=True)
        future = time.time() + 3600
        registry = {
            "accounts": {
                "a": {
                    "email": "a@example.com",
                    "auth_path": "a.auth.json",
                    "usage": {
                        "5h": {"used_percent": 10, "resets_at": future},
                        "weekly": {"used_percent": 20, "resets_at": future},
                    },
                },
                "b": {
                    "email": "b@example.com",
                    "auth_path": "b.auth.json",
                    "usage": {
                        "5h": {"used_percent": 100, "resets_at": future},
                        "weekly": {"used_percent": 50, "resets_at": future},
                    },
                },
            }
        }
        (self.home / "accounts" / "registry.json").write_text(
            json.dumps(registry),
            encoding="utf-8",
        )
        (self.home / "accounts" / "a.auth.json").write_text("{}", encoding="utf-8")
        (self.home / "accounts" / "b.auth.json").write_text("{}", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_registry_used_percent_means_used_not_remaining(self) -> None:
        states = {state.email: state for state in account_states(self.home)}
        self.assertEqual(states["a@example.com"].remaining_5h, 90)
        self.assertEqual(states["a@example.com"].remaining_weekly, 80)
        self.assertEqual(states["b@example.com"].remaining_5h, 0)

    def test_wall_cache_uses_fresh_zero_remaining_with_future_reset(self) -> None:
        states = account_states(self.home)
        write_quota_wall_cache(self.home, states)
        self.assertEqual(quota_walled_emails(self.home), {"b@example.com"})

    def test_stale_wall_cache_is_ignored(self) -> None:
        write_json_atomic(
            wall_cache_path(self.home),
            {
                "written_at": time.time() - 9999,
                "accounts": {
                    "a@example.com": {
                        "email": "a@example.com",
                        "windows": {"weekly": {"remaining_percent": 0}},
                    }
                },
            },
        )
        self.assertEqual(quota_walled_emails(self.home), set())

    def test_expired_reset_window_is_ignored(self) -> None:
        write_json_atomic(
            wall_cache_path(self.home),
            {
                "written_at": time.time(),
                "accounts": {
                    "a@example.com": {
                        "email": "a@example.com",
                        "windows": {
                            "weekly": {
                                "remaining_percent": 0,
                                "resets_at": time.time() - 5,
                            }
                        },
                    }
                },
            },
        )
        self.assertEqual(quota_walled_emails(self.home), set())

    def test_pick_account_excludes_walled_and_broken_accounts(self) -> None:
        config = load_config(self.home)
        states = account_states(self.home, config)
        self.assertEqual(
            pick_account(states, config, walled={"b@example.com"}).email,
            "a@example.com",
        )
        mark_broken(self.home, "a@example.com")
        states = account_states(self.home, config)
        self.assertIsNone(
            pick_account(
                states,
                config,
                walled={"b@example.com"},
            )
        )

    def test_proactive_migration_uses_cached_registry_state(self) -> None:
        config = load_config(self.home)
        config["live_migrate_below_5h_percent"] = 1
        self.assertEqual(
            migration_reason_for("b@example.com", self.home, config),
            "5h quota at 0%",
        )
        self.assertIsNone(migration_reason_for("a@example.com", self.home, config))


if __name__ == "__main__":
    unittest.main()

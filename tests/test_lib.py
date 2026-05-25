#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import unittest
import base64
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
os.sys.path.insert(0, str(REPO / "bin"))

from codex_hot_swap_lib import (  # noqa: E402
    account_states,
    codex_interactive_prompt_supported,
    latest_rollout_from_sqlite,
    live_tabs,
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

    def test_codex_auth_list_registry_uses_account_key_and_last_usage(self) -> None:
        future = time.time() + 3600
        account_key = "encoded-account-key::account-uuid"
        filename_key = base64.urlsafe_b64encode(
            account_key.encode("utf-8")
        ).decode("ascii").rstrip("=")
        registry = {
            "accounts": [
                {
                    "account_key": account_key,
                    "email": "list@example.com",
                    "last_usage": {
                        "primary": {"used_percent": 25, "resets_at": future},
                        "secondary": {"used_percent": 75, "resets_at": future},
                    },
                }
            ]
        }
        (self.home / "accounts" / "registry.json").write_text(
            json.dumps(registry),
            encoding="utf-8",
        )
        (self.home / "accounts" / f"{filename_key}.auth.json").write_text(
            "{}",
            encoding="utf-8",
        )

        states = account_states(self.home)
        self.assertEqual(len(states), 1)
        state = states[0]
        self.assertEqual(state.email, "list@example.com")
        self.assertTrue(state.auth_exists)
        self.assertEqual(state.auth_path.name, f"{filename_key}.auth.json")
        self.assertEqual(state.remaining_5h, 75)
        self.assertEqual(state.remaining_weekly, 25)

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

    def test_active_weekly_wall_survives_expired_5h_window(self) -> None:
        write_json_atomic(
            wall_cache_path(self.home),
            {
                "written_at": time.time(),
                "accounts": {
                    "a@example.com": {
                        "email": "a@example.com",
                        "windows": {
                            "5h": {
                                "remaining_percent": 0,
                                "resets_at": time.time() - 5,
                            },
                            "weekly": {
                                "remaining_percent": 0,
                                "resets_at": time.time() + 3600,
                            },
                        },
                    }
                },
            },
        )
        self.assertEqual(quota_walled_emails(self.home), {"a@example.com"})

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

    def test_live_tabs_accepts_legacy_pinned_email_and_pid(self) -> None:
        tab_home = self.home / "tabs" / "legacy-tab"
        tab_home.mkdir(parents=True)
        (tab_home / "tab.json").write_text(
            json.dumps(
                {
                    "tab_id": "legacy-tab",
                    "pinned_email": "a@example.com",
                    "wrapper_pid": os.getpid(),
                    "pid": 123456789,
                }
            ),
            encoding="utf-8",
        )
        tabs = live_tabs(self.home)
        self.assertEqual(len(tabs), 1)
        self.assertEqual(tabs[0]["email"], "a@example.com")
        self.assertEqual(tabs[0]["child_pid"], 123456789)

    def test_proactive_migration_uses_cached_registry_state(self) -> None:
        config = load_config(self.home)
        config["live_migrate_below_5h_percent"] = 1
        self.assertEqual(
            migration_reason_for("b@example.com", self.home, config),
            "5h quota at 0%",
        )
        self.assertIsNone(migration_reason_for("a@example.com", self.home, config))

    def test_rollout_lookup_uses_tab_sqlite_latest_thread(self) -> None:
        old_rollout = self.home / "sessions" / "old" / "rollout-old.jsonl"
        new_rollout = self.home / "sessions" / "new" / "rollout-new.jsonl"
        old_rollout.parent.mkdir(parents=True)
        new_rollout.parent.mkdir(parents=True)
        old_rollout.write_text("{}\n", encoding="utf-8")
        new_rollout.write_text("{}\n", encoding="utf-8")
        db = self.home / "state_5.sqlite"
        conn = sqlite3.connect(db)
        try:
            conn.execute("create table threads (rollout_path text, updated_at integer)")
            conn.execute(
                "insert into threads (rollout_path, updated_at) values (?, ?)",
                (str(old_rollout), 1),
            )
            conn.execute(
                "insert into threads (rollout_path, updated_at) values (?, ?)",
                (str(new_rollout), 2),
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(latest_rollout_from_sqlite(self.home), new_rollout)

    def test_codex_interactive_prompt_probe(self) -> None:
        fake_path = REPO / "tests" / "fakes"
        env = dict(os.environ)
        env["PATH"] = f"{fake_path}{os.pathsep}{env['PATH']}"
        env["FAKE_CODEX_HELP_MODE"] = "modern"
        self.assertTrue(codex_interactive_prompt_supported(env))
        env["FAKE_CODEX_HELP_MODE"] = "no-prompt"
        self.assertFalse(codex_interactive_prompt_supported(env))


if __name__ == "__main__":
    unittest.main()

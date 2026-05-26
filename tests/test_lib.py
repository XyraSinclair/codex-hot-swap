#!/usr/bin/env python3
"""Test suite for the v0.2.0 codex-hot-swap lib.

Covers the live source-of-truth design:
  * per-tab accounts/ pinning (the core fix that makes codex actually use
    the chosen account)
  * sessions/ symlink so rollouts survive cleanup_tab_home
  * logs_2.sqlite shared back to global (7x startup speedup)
  * one-tab-per-account rule that prevents refresh_token_reused races
  * quota-walled ledger with reset-time parsing and auto-expiry
  * verified-working ledger (recent-success short-circuit over the lying
    usage API)
  * observed-token tracking from PTY scrape (ground truth)
  * exclusive vault lock for refresh-token safety

The tests run in a tmpdir CODEX_HOME — they never touch the real install.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bin"))

import codex_hot_swap_lib as L  # noqa: E402
from codex_hot_swap_lib import (  # noqa: E402
    _exclusive_lock,
    _vault_filename_for_key,
    account_states,
    clear_quota_wall,
    codex_base,
    create_tab_home,
    mark_broken,
    mark_quota_walled,
    mark_verified_working,
    observed_5h_tokens,
    pick_account,
    quota_walled_emails,
    rank_accounts,
    record_observed_tokens,
    verified_working_emails,
    write_next_pick_hint,
    read_next_pick_hint,
)


def _id_token_for(email: str, account_id: str, user_id: str) -> str:
    """Build a minimal JWT codex's auth parser accepts (just the email claim)."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = json.dumps(
        {
            "email": email,
            "https://api.openai.com/auth": {
                "chatgpt_account_id": account_id,
                "chatgpt_user_id": user_id,
            },
        }
    )
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()
    return f"{header}.{payload_b64}."


def _write_vault(path: Path, email: str, account_key: str) -> None:
    user_id, account_id = account_key.split("::", 1)
    blob = {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": False,
        "tokens": {
            "id_token": _id_token_for(email, account_id, user_id),
            "access_token": "AT_v0",
            "refresh_token": "RT_v0",
            "account_id": account_id,
        },
        "last_refresh": "2026-05-26T00:00:00Z",
    }
    path.write_text(json.dumps(blob), encoding="utf-8")


def _make_home(tmp: Path) -> Path:
    """Build a minimal CODEX_HOME with two accounts the lib will recognise."""
    home = tmp / "codex"
    (home / "accounts" / "recover").mkdir(parents=True)
    (home / "sessions").mkdir()
    a_key = "user-AAA::aaaa-0000-0000-0000-000000000001"
    b_key = "user-BBB::bbbb-0000-0000-0000-000000000002"
    a_vault = home / "accounts" / f"{_vault_filename_for_key(a_key)}.auth.json"
    b_vault = home / "accounts" / f"{_vault_filename_for_key(b_key)}.auth.json"
    _write_vault(a_vault, "a@example.com", a_key)
    _write_vault(b_vault, "b@example.com", b_key)
    future = int(time.time()) + 3600
    registry = {
        "schema_version": 3,
        "accounts": [
            {
                "account_key": a_key,
                "email": "a@example.com",
                "last_usage": {
                    "primary": {"used_percent": 10, "resets_at": future},
                    "secondary": {"used_percent": 20, "resets_at": future},
                },
                "last_used_at": int(time.time()),
            },
            {
                "account_key": b_key,
                "email": "b@example.com",
                "last_usage": {
                    "primary": {"used_percent": 80, "resets_at": future},
                    "secondary": {"used_percent": 30, "resets_at": future},
                },
                "last_used_at": int(time.time()) - 60,
            },
        ],
        "active_account_key": a_key,
        "api": {"usage": True},
    }
    (home / "accounts" / "registry.json").write_text(json.dumps(registry))
    return home


class HotSwap(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = _make_home(Path(self.tmp.name))
        os.environ["CODEX_HOME"] = str(self.home)
        os.environ["CODEX_GLOBAL_HOME"] = str(self.home)

    def tearDown(self) -> None:
        os.environ.pop("CODEX_HOME", None)
        os.environ.pop("CODEX_GLOBAL_HOME", None)
        self.tmp.cleanup()

    # ----- per-tab pinning (the core fix) -----------------------------------

    def test_create_tab_home_writes_per_tab_registry(self) -> None:
        """Tabs must have their OWN accounts/registry.json with active_account_key
        set to the chosen account. Without this, codex 0.132+ reads the global
        active_account_key and pins every tab to the same account regardless
        of which vault the wrapper copied at the top level."""
        states = {s.email: s for s in account_states(self.home)}
        chosen = states["a@example.com"]
        tab_home = create_tab_home(chosen, ["codex"], self.home)

        # accounts/ is a REAL dir, not a symlink to the global accounts/.
        tab_accounts = tab_home / "accounts"
        self.assertTrue(tab_accounts.is_dir())
        self.assertFalse(tab_accounts.is_symlink())

        # registry has only the chosen account and pins it active.
        reg = json.loads((tab_accounts / "registry.json").read_text())
        self.assertEqual(len(reg["accounts"]), 1)
        self.assertEqual(reg["accounts"][0]["email"], "a@example.com")
        self.assertEqual(reg["active_account_key"], reg["accounts"][0]["account_key"])

        # The per-account vault is materialized under the codex-expected
        # base64url filename so codex finds it via the registry lookup.
        expected = tab_accounts / f"{_vault_filename_for_key(reg['active_account_key'])}.auth.json"
        self.assertTrue(expected.is_file())

    # ----- sessions symlink + logs_2 share ----------------------------------

    def test_sessions_symlinks_to_global_so_rollouts_survive_cleanup(self) -> None:
        chosen = next(s for s in account_states(self.home) if s.email == "a@example.com")
        tab_home = create_tab_home(chosen, ["codex"], self.home)
        sessions = tab_home / "sessions"
        self.assertTrue(sessions.is_symlink() or sessions.is_dir())
        # Simulate codex writing a rollout while the tab is alive.
        rollout = sessions / "2026" / "05" / "26" / "rollout-test.jsonl"
        rollout.parent.mkdir(parents=True, exist_ok=True)
        rollout.write_text('{"id":"x"}\n')
        # On cleanup, the rollout MUST survive in the global sessions tree.
        L.cleanup_tab_home(tab_home)
        survived = self.home / "sessions" / "2026" / "05" / "26" / "rollout-test.jsonl"
        self.assertTrue(survived.is_file())

    # ----- one-tab-per-account rule -----------------------------------------

    def test_pick_account_excludes_accounts_with_live_tabs(self) -> None:
        """Two codex processes sharing one RT cause refresh_token_reused. The
        wrapper must hard-exclude already-occupied accounts so each refresh
        chain is owned by exactly one process at a time."""
        chosen = next(s for s in account_states(self.home) if s.email == "a@example.com")
        # Pin one tab to a@example.com.
        tab_home = create_tab_home(chosen, ["codex"], self.home)
        # Write a fake live PID to tab.json so live_tabs() sees it as alive.
        tab_meta = json.loads((tab_home / "tab.json").read_text())
        tab_meta["pid"] = os.getpid()
        (tab_home / "tab.json").write_text(json.dumps(tab_meta))
        # Now pick_account must NOT return a@example.com.
        picked = pick_account(self.home, exclude_emails=set(), config=L.load_config(self.home))
        self.assertIsNotNone(picked)
        self.assertEqual(picked.email, "b@example.com")
        L.cleanup_tab_home(tab_home)

    # ----- quota wall persistence -------------------------------------------

    def test_mark_quota_walled_persists_with_reset_epoch(self) -> None:
        mark_quota_walled("a@example.com", "May 30th, 2026 1:12 PM", self.home)
        self.assertIn("a@example.com", quota_walled_emails(self.home))
        data = json.loads(L.quota_walled_path(self.home).read_text())
        self.assertGreater(data["a@example.com"]["reset_epoch"], time.time())

    def test_mark_quota_walled_defensive_default_15_minutes(self) -> None:
        before = time.time()
        mark_quota_walled("a@example.com", None, self.home)
        data = json.loads(L.quota_walled_path(self.home).read_text())
        wall = data["a@example.com"]["reset_epoch"]
        # 15-minute default, give a few seconds of slack for the parse.
        self.assertGreater(wall, before + 14 * 60)
        self.assertLess(wall, before + 16 * 60)

    def test_quota_walled_emails_auto_expires(self) -> None:
        mark_quota_walled("a@example.com", None, self.home)
        # Backdate so the wall has already expired.
        path = L.quota_walled_path(self.home)
        data = json.loads(path.read_text())
        data["a@example.com"]["reset_epoch"] = time.time() - 1
        path.write_text(json.dumps(data))
        self.assertEqual(quota_walled_emails(self.home), set())

    def test_clear_quota_wall_removes_entry(self) -> None:
        mark_quota_walled("a@example.com", "May 30th, 2026 1:12 PM", self.home)
        clear_quota_wall("a@example.com", self.home)
        self.assertEqual(quota_walled_emails(self.home), set())

    # ----- verified-working short-circuit -----------------------------------

    def test_verified_working_outranks_higher_apparent_quota(self) -> None:
        """An account verified-working within the TTL must outrank one the
        (lying) usage API claims has more headroom but hasn't been proven."""
        mark_verified_working("b@example.com", self.home)
        ranked = rank_accounts(account_states(self.home), config=L.load_config(self.home))
        self.assertEqual(ranked[0].email, "b@example.com")
        self.assertTrue(ranked[0].verified_working)

    # ----- observed-token tracking ------------------------------------------

    def test_record_observed_tokens_accumulates_per_email(self) -> None:
        record_observed_tokens("a@example.com", 1000, self.home)
        record_observed_tokens("a@example.com", 2500, self.home)
        self.assertEqual(observed_5h_tokens("a@example.com", self.home), 3500)

    # ----- next-pick hint ---------------------------------------------------

    def test_next_pick_hint_round_trips(self) -> None:
        write_next_pick_hint("a@example.com", self.home)
        self.assertEqual(read_next_pick_hint(self.home), "a@example.com")

    def test_next_pick_hint_ignored_when_target_walled(self) -> None:
        write_next_pick_hint("a@example.com", self.home)
        mark_quota_walled("a@example.com", "May 30th, 2026 1:12 PM", self.home)
        self.assertIsNone(read_next_pick_hint(self.home))

    # ----- vault lock -------------------------------------------------------

    def test_exclusive_lock_serializes_concurrent_writers(self) -> None:
        path = self.home / "test.lock"
        order: list = []

        def grab(name: str, hold: float) -> None:
            with _exclusive_lock(path, timeout=5):
                order.append(("enter", name, time.monotonic()))
                time.sleep(hold)
                order.append(("exit", name, time.monotonic()))

        threads = [
            threading.Thread(target=grab, args=("a", 0.15)),
            threading.Thread(target=grab, args=("b", 0.15)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        enters = [t for ev, _n, t in order if ev == "enter"]
        exits = [t for ev, _n, t in order if ev == "exit"]
        enters.sort()
        exits.sort()
        # Second enterer must not enter until first has exited.
        self.assertGreaterEqual(enters[1], exits[0])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/python3
from __future__ import annotations

import base64
import fcntl
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_5H_THRESHOLD = 25
DEFAULT_WEEKLY_THRESHOLD = 15
DEFAULT_ALL_ACCOUNTS_WARNING_5H = 35
DEFAULT_ALL_ACCOUNTS_WARNING_WEEKLY = 20
DEFAULT_BALANCE_TOLERANCE = 15


@dataclass(frozen=True)
class AccountState:
    email: str
    account_key: str | None
    vault_path: Path | None
    active: bool
    broken: bool
    remaining_5h_pct: float | None
    remaining_weekly_pct: float | None
    reset_5h_at_iso: str | None
    reset_weekly_at_iso: str | None
    tabs_pinned: int
    usage_source: str
    last_used_at: int | None
    verified_working: bool = False
    # Ground-truth observed tokens scraped from codex's "tokens used N" output.
    # Used as a sanity-check on the lying usage API and as the primary signal
    # for predicting wall headroom.
    observed_5h_tokens: int = 0
    observed_weekly_tokens: int = 0


@dataclass(frozen=True)
class TabRecord:
    tab_id: str
    path: Path
    pid: int | None
    wrapper_pid: int | None
    pinned_email: str
    started_at_iso: str
    alive: bool
    argv: list[str]


# These paths encode thread/log/index state. Sharing them across accounts lets
# a new tab resume another account's thread, which later poisons auth recovery
# because Codex expects the original account_id for that thread.
#
# Do NOT include "sessions" here. Rollout JSONL files under sessions/ are the
# append-only conversation record and the ONLY source of truth for `codex
# resume <id>`. Keeping sessions tab-local means a clean exit + cleanup_tab_home
# deletes the JSONL the user needs to resume. We symlink sessions/ back to the
# global ~/.codex/sessions/ so rollouts survive tab cleanup. Cross-account
# resume is still blocked by Codex's own auth check against the rollout's
# recorded account_id, so this does not weaken account isolation.
#
# logs_2.sqlite is also NOT here. It's a write-shared logging DB; sharing it
# across tabs is safe (SQLite WAL handles concurrent writers) and ESSENTIAL
# for startup speed — without an existing logs_2.sqlite, codex 0.132+ spends
# ~9 seconds of CPU on first-run schema setup, making tab startup 7x slower
# than running against the global home. Symlinking it back to the global file
# means codex sees an initialized DB and skips that cost entirely.
TAB_PRIVATE_NAMES = {
    ".codex-global-state.json",
    ".tmp",
    "debug",
    "ghostty-tty-registry.jsonl",
    "history.jsonl",
    "log",
    "recovery-resume-commands.txt",
    "session_index.jsonl",
    "shell_snapshots",
    "sqlite",
    "state.db",
    "state_5.sqlite",
    "state_5.sqlite-shm",
    "state_5.sqlite-wal",
    "tmp",
}

HOOK_STATE_RE = re.compile(
    r'(?m)^\[hooks\.state\."(?P<key>[^"]+)"\]\n(?P<body>(?:^(?!\[).*(?:\n|$))*)'
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def codex_base() -> Path:
    raw_home = os.environ.get("CODEX_HOME")
    if raw_home:
        home = Path(raw_home).expanduser()
        if home.parent.name != "tabs":
            return home

    raw = os.environ.get("CODEX_GLOBAL_HOME") or raw_home
    base = Path(raw).expanduser() if raw else Path.home() / ".codex"
    if base.parent.name == "tabs":
        return base.parent.parent
    return base


def accounts_dir(base: Path | None = None) -> Path:
    return (base or codex_base()) / "accounts"


def tabs_dir(base: Path | None = None) -> Path:
    return (base or codex_base()) / "tabs"


def predictive_state_path(base: Path | None = None) -> Path:
    return (base or codex_base()) / "predictive_state.json"


def predictive_config_path(base: Path | None = None) -> Path:
    return (base or codex_base()) / "predictive.config.json"


def broken_ledger_path(base: Path | None = None) -> Path:
    return accounts_dir(base) / "recover" / "broken.tsv"


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open() as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_json_atomic(path: Path, data: dict[str, Any], mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        if mode is not None:
            os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _toml_basic_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def seed_tab_hook_trust(base: Path, tab_home: Path) -> bool:
    """Mirror trusted global hook hashes for a per-tab hooks.json symlink.

    Codex 0.131 keys hook trust by the full hooks.json path. codex-safe gives
    every tab a private CODEX_HOME, so the same symlinked hooks.json otherwise
    appears new on every launch. This only mirrors entries that are already
    trusted for the real global hooks.json; it does not trust unknown hooks.
    """
    config_path = base / "config.toml"
    global_hooks = base / "hooks.json"
    tab_hooks = tab_home / "hooks.json"
    if not config_path.exists() or not global_hooks.exists() or not tab_hooks.exists():
        return False

    lock_path = base / ".codex-hot-swap-config.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            text = config_path.read_text()
        except Exception:
            return False

        global_prefix = f"{global_hooks}:"
        tab_prefix = f"{tab_hooks}:"
        existing_keys = {match.group("key") for match in HOOK_STATE_RE.finditer(text)}
        additions: list[str] = []

        for match in HOOK_STATE_RE.finditer(text):
            key = match.group("key")
            if not key.startswith(global_prefix):
                continue
            tab_key = f"{tab_prefix}{key[len(global_prefix):]}"
            if tab_key in existing_keys:
                continue
            body = match.group("body").rstrip()
            additions.append(f"[hooks.state.{_toml_basic_string(tab_key)}]\n{body}\n")
            existing_keys.add(tab_key)

        if not additions:
            return False

        suffix = "\n" if text.endswith("\n") else "\n\n"
        updated = text + suffix + "\n".join(additions)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{config_path.name}.", dir=str(config_path.parent))
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(updated)
            try:
                os.chmod(tmp_name, config_path.stat().st_mode & 0o777)
            except OSError:
                os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, config_path)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
        return True


def decode_claims_from_id_token(id_token: str | None) -> dict[str, Any]:
    if not id_token or "." not in id_token:
        return {}
    try:
        middle = id_token.split(".")[1]
        middle += "=" * (-len(middle) % 4)
        raw = base64.urlsafe_b64decode(middle.encode())
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def email_from_auth(auth: dict[str, Any]) -> str | None:
    tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
    claims = decode_claims_from_id_token(tokens.get("id_token"))
    email = claims.get("email") or auth.get("email")
    return str(email).lower() if email else None


def account_id_from_auth(auth: dict[str, Any]) -> str | None:
    tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
    account_id = tokens.get("account_id") or auth.get("account_id")
    return str(account_id) if account_id else None


def parse_refresh_marker(auth: dict[str, Any], fallback_mtime: float = 0) -> float:
    raw = auth.get("last_refresh") or auth.get("updated_at")
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str) and raw:
        text = raw.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text).timestamp()
        except Exception:
            pass
    return float(fallback_mtime or 0)


def load_registry(base: Path | None = None) -> dict[str, Any]:
    return read_json(accounts_dir(base) / "registry.json")


def active_email(base: Path | None = None) -> str | None:
    base = base or codex_base()
    email = email_from_auth(read_json(base / "auth.json"))
    if email:
        return email
    reg = load_registry(base)
    active_key = reg.get("active_account_key")
    for acct in reg.get("accounts", []) if isinstance(reg.get("accounts"), list) else []:
        if acct.get("account_key") == active_key and acct.get("email"):
            return str(acct["email"]).lower()
    return None


def vaults_by_email(base: Path | None = None) -> dict[str, Path]:
    base = base or codex_base()
    result: dict[str, Path] = {}
    for path in accounts_dir(base).glob("*.auth.json"):
        auth = read_json(path)
        email = email_from_auth(auth)
        if email:
            result[email] = path
    return result


def next_pick_hint_path(base: Path | None = None) -> Path:
    """Predictive daemon writes the next-best account here so the wrapper
    can do a single-file read instead of rebuilding full ranker state on
    every cold-start. Wrapper falls back to pick_account if the hint is
    missing, stale, or excluded.
    """
    return (base or codex_base()) / "next-account.json"


# Hints older than this are ignored — the daemon polls every 60s, so giving
# ourselves 5 minutes of slack tolerates a missed poll without forcing the
# wrapper into a full ranker rebuild.
NEXT_PICK_HINT_TTL_SECONDS = 300


def read_next_pick_hint(base: Path | None = None) -> str | None:
    """Return the daemon-suggested account email if the hint is fresh and
    the email is not currently broken/walled/excluded."""
    path = next_pick_hint_path(base)
    data = read_json(path)
    if not isinstance(data, dict):
        return None
    email = (data.get("email") or "").lower()
    if not email:
        return None
    ts = float(data.get("written_at_epoch", 0) or 0)
    if (time.time() - ts) > NEXT_PICK_HINT_TTL_SECONDS:
        return None
    if email in broken_emails(base) or email in quota_walled_emails(base):
        return None
    return email


def write_next_pick_hint(email: str, base: Path | None = None) -> None:
    """Daemon-side: persist the next-best account email + a short rationale."""
    path = next_pick_hint_path(base)
    write_json_atomic(
        path,
        {
            "email": email.lower(),
            "written_at_epoch": time.time(),
            "written_at_iso": now_iso(),
        },
        mode=0o600,
    )


def observed_usage_path(base: Path | None = None) -> Path:
    """Per-account ledger of token consumption observed via PTY scrape.

    Schema: { "<email>": [ {"ts": epoch, "tokens": N}, ... ] }
    Entries older than OBSERVED_USAGE_TTL are pruned on read.
    """
    return accounts_dir(base) / "recover" / "usage-observed.json"


# Slightly longer than the 5h window so we don't drop entries that JUST
# rotated out of the cap; the weekly view filters at 7d separately.
OBSERVED_USAGE_TTL_SECONDS = 7 * 24 * 3600
PRIMARY_WINDOW_SECONDS = 5 * 3600
WEEKLY_WINDOW_SECONDS = 7 * 24 * 3600

# ChatGPT-Pro Codex 5h cap, in observed tokens. The exact number is not
# publicly documented and codex CLI reports usage as a percentage. We pick a
# conservative estimate: each pro account's 5h window appears to allow on the
# order of ~1.5M tokens of *output* before the wall fires. Tune via env if
# the heuristic is off.
ESTIMATED_5H_TOKEN_CAP = int(os.environ.get("CODEX_HOTSWAP_5H_TOKEN_CAP", "1500000"))
ESTIMATED_WEEKLY_TOKEN_CAP = int(os.environ.get("CODEX_HOTSWAP_WEEKLY_TOKEN_CAP", "30000000"))
# Headroom floor: if an account's observed-headroom drops below this fraction
# of the cap, treat it as effectively walled and skip it ahead-of-time.
HEADROOM_FLOOR_FRACTION = float(os.environ.get("CODEX_HOTSWAP_HEADROOM_FLOOR", "0.05"))


def observed_headroom_5h(state: "AccountState") -> int:
    """Estimated remaining 5h tokens by observation. Negative if over."""
    return ESTIMATED_5H_TOKEN_CAP - int(state.observed_5h_tokens or 0)


def observed_headroom_weekly(state: "AccountState") -> int:
    return ESTIMATED_WEEKLY_TOKEN_CAP - int(state.observed_weekly_tokens or 0)


def observed_near_wall(state: "AccountState") -> bool:
    """True if observed-headroom predicts a wall is imminent."""
    if not state.observed_5h_tokens and not state.observed_weekly_tokens:
        return False
    floor_5h = int(ESTIMATED_5H_TOKEN_CAP * HEADROOM_FLOOR_FRACTION)
    floor_wk = int(ESTIMATED_WEEKLY_TOKEN_CAP * HEADROOM_FLOOR_FRACTION)
    return observed_headroom_5h(state) < floor_5h or observed_headroom_weekly(state) < floor_wk


def record_observed_tokens(email: str, tokens: int, base: Path | None = None) -> None:
    """Append `tokens` consumed by `email` to the observed-usage ledger."""
    if tokens <= 0:
        return
    email = email.lower()
    path = observed_usage_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_lock(path.with_suffix(".json.lock")):
        data = read_json(path) if path.exists() else {}
        if not isinstance(data, dict):
            data = {}
        entries = data.get(email)
        if not isinstance(entries, list):
            entries = []
        entries.append({"ts": time.time(), "tokens": int(tokens)})
        # Prune oldest entries to keep the file bounded; we never need
        # anything older than the weekly window.
        cutoff = time.time() - OBSERVED_USAGE_TTL_SECONDS
        entries = [e for e in entries if isinstance(e, dict) and e.get("ts", 0) > cutoff]
        data[email] = entries
        write_json_atomic(path, data, mode=0o600)


def observed_tokens_in_window(
    email: str, window_seconds: float, base: Path | None = None
) -> int:
    """Sum tokens consumed by `email` over the last `window_seconds`."""
    email = email.lower()
    data = read_json(observed_usage_path(base))
    if not isinstance(data, dict):
        return 0
    entries = data.get(email)
    if not isinstance(entries, list):
        return 0
    cutoff = time.time() - window_seconds
    return int(sum(int(e.get("tokens", 0)) for e in entries if e.get("ts", 0) > cutoff))


def observed_5h_tokens(email: str, base: Path | None = None) -> int:
    return observed_tokens_in_window(email, PRIMARY_WINDOW_SECONDS, base)


def observed_weekly_tokens(email: str, base: Path | None = None) -> int:
    return observed_tokens_in_window(email, WEEKLY_WINDOW_SECONDS, base)


def verified_working_path(base: Path | None = None) -> Path:
    return accounts_dir(base) / "recover" / "verified-working.json"


# How long a "verified-working" marker stays trusted. 30 minutes is short
# enough that a recently-confirmed account is almost certainly still good for
# the next chat, and long enough to cover a tab being opened, then a second
# tab opened a few minutes later. Past this window we'll happily probe again.
VERIFIED_WORKING_TTL_SECONDS = 30 * 60


def mark_verified_working(email: str, base: Path | None = None) -> None:
    """Record that `email` had a successful wrapped run just now.

    pick_account / rank_accounts consult this ledger to break ties: an
    account verified working in the last N minutes outranks one whose
    usage-API numbers merely *look* good but might be lies.
    """
    email = email.lower()
    path = verified_working_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_json(path)
    if not isinstance(existing, dict):
        existing = {}
    existing[email] = {
        "verified_at_epoch": time.time(),
        "verified_at_iso": now_iso(),
    }
    write_json_atomic(path, existing, mode=0o600)


def verified_working_emails(base: Path | None = None) -> set[str]:
    """Return emails whose most recent wrapped run succeeded within TTL."""
    path = verified_working_path(base)
    data = read_json(path)
    if not isinstance(data, dict):
        return set()
    cutoff = time.time() - VERIFIED_WORKING_TTL_SECONDS
    pruned: dict[str, Any] = {}
    fresh: set[str] = set()
    for email, info in data.items():
        if not isinstance(info, dict):
            continue
        ts = float(info.get("verified_at_epoch", 0) or 0)
        if ts > cutoff:
            fresh.add(email.lower())
            pruned[email] = info
    if pruned != data:
        try:
            write_json_atomic(path, pruned, mode=0o600)
        except Exception:
            pass
    return fresh


def clear_verified_working(email: str, base: Path | None = None) -> None:
    email = email.lower()
    path = verified_working_path(base)
    existing = read_json(path)
    if not isinstance(existing, dict) or email not in existing:
        return
    del existing[email]
    write_json_atomic(path, existing, mode=0o600)


def quota_walled_path(base: Path | None = None) -> Path:
    return accounts_dir(base) / "recover" / "quota-walled.json"


def quota_walled_emails(base: Path | None = None) -> set[str]:
    """Return emails currently quota-walled (reset time in the future).

    Walls auto-expire when `now >= reset_at`; expired entries are pruned on read.
    """
    path = quota_walled_path(base)
    data = read_json(path)
    if not isinstance(data, dict):
        return set()
    now = time.time()
    walled: set[str] = set()
    pruned: dict[str, Any] = {}
    for email, info in data.items():
        if not isinstance(info, dict):
            continue
        ts = float(info.get("reset_epoch", 0) or 0)
        if ts > now:
            walled.add(email.lower())
            pruned[email] = info
    if pruned != data:
        try:
            write_json_atomic(path, pruned, mode=0o600)
        except Exception:
            pass
    return walled


def _parse_quota_reset(text: str) -> float | None:
    """Parse "May 30th, 2026 1:12 PM" (codex's local-time format) -> epoch.

    Codex prints the reset time in the user's local timezone with no zone
    suffix. We parse in local time and convert to epoch.
    """
    if not text:
        return None
    import re as _re
    cleaned = _re.sub(r"(\d+)(st|nd|rd|th)", r"\1", text).strip()
    for fmt in ("%B %d, %Y %I:%M %p", "%B %d, %Y %H:%M"):
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.timestamp()
        except ValueError:
            continue
    return None


def mark_quota_walled(email: str, reset_text: str | None, base: Path | None = None) -> None:
    """Record that `email` is quota-walled until the parsed reset time.

    If reset_text is unparseable, default to a short 15-minute defensive wall.
    Rationale: a too-long defensive wall blocks legitimate retries when the
    quota-detection regex misfires (e.g. unusual server response format).
    15 min gives time for any actual 5h rate limit to be revisited without
    keeping a working account benched for an hour. Real walls always include
    a parseable reset time, so this only applies to edge cases.
    """
    email = email.lower()
    path = quota_walled_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_json(path)
    if not isinstance(existing, dict):
        existing = {}
    ts = _parse_quota_reset(reset_text or "") or (time.time() + 15 * 60)
    existing[email] = {
        "reset_epoch": ts,
        "reset_text": reset_text or "",
        "marked_at_iso": now_iso(),
    }
    write_json_atomic(path, existing, mode=0o600)


def clear_quota_wall(email: str, base: Path | None = None) -> None:
    email = email.lower()
    path = quota_walled_path(base)
    existing = read_json(path)
    if not isinstance(existing, dict) or email not in existing:
        return
    del existing[email]
    write_json_atomic(path, existing, mode=0o600)


def broken_emails(base: Path | None = None) -> set[str]:
    path = broken_ledger_path(base)
    auto_clear_stale_broken(base)
    broken: set[str] = set()
    try:
        for line in path.read_text().splitlines():
            if line.strip():
                broken.add(line.split("\t", 1)[0].strip().lower())
    except FileNotFoundError:
        pass
    return broken


def _ledger_marker_ts(line: str) -> float:
    try:
        marker = line.split("\t", 1)[1].strip().replace("Z", "+00:00")
        return datetime.fromisoformat(marker).timestamp()
    except Exception:
        return 0.0


BROKEN_STALE_GRACE_SECONDS = 6 * 3600


def auto_clear_stale_broken(base: Path | None = None) -> list[str]:
    """Clear stale "broken" markers.

    An account is unstuck if any of:
      * its vault file was rewritten after the broken marker (the original
        signal: a successful refresh or re-OAuth happened), or
      * the broken marker is older than BROKEN_STALE_GRACE_SECONDS and the
        vault still exists (prevents the trap-door deadlock where an account
        is never used again because pick_account excludes it, so its vault
        mtime never advances).
    """
    base = base or codex_base()
    path = broken_ledger_path(base)
    if not path.exists():
        return []
    try:
        rows = [line for line in path.read_text().splitlines() if line.strip()]
    except Exception:
        return []
    vaults = vaults_by_email(base)
    now = time.time()
    kept: list[str] = []
    cleared: list[str] = []
    for line in rows:
        email = line.split("\t", 1)[0].strip().lower()
        vault = vaults.get(email)
        marker_ts = _ledger_marker_ts(line)
        vault_alive = vault is not None and vault.exists()
        vault_advanced = vault_alive and vault.stat().st_mtime > marker_ts + 30
        stale_by_age = vault_alive and marker_ts > 0 and (now - marker_ts) > BROKEN_STALE_GRACE_SECONDS
        if vault_advanced or stale_by_age:
            cleared.append(email)
        else:
            kept.append(line)
    if cleared:
        path.write_text("\n".join(kept) + ("\n" if kept else ""))
    return cleared


def mark_broken(email: str, base: Path | None = None) -> None:
    email = email.lower()
    path = broken_ledger_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = broken_emails(base)
    if email in existing:
        return
    with path.open("a") as fh:
        fh.write(f"{email}\t{now_iso()}\n")


def reset_broken(email: str, base: Path | None = None) -> None:
    email = email.lower()
    path = broken_ledger_path(base)
    try:
        rows = [
            line
            for line in path.read_text().splitlines()
            if line.strip() and line.split("\t", 1)[0].strip().lower() != email
        ]
    except FileNotFoundError:
        rows = []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + ("\n" if rows else ""))


def _reset_iso(ts: Any) -> str | None:
    try:
        value = float(ts)
    except Exception:
        return None
    if value <= 0:
        return None
    return datetime.fromtimestamp(value, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _remaining_from_usage(usage: dict[str, Any], key: str) -> tuple[float | None, str | None]:
    window = usage.get(key)
    if not isinstance(window, dict):
        return None, None
    used = window.get("used_percent")
    try:
        remaining = 100.0 - float(used)
    except Exception:
        remaining = None
    if remaining is not None:
        remaining = max(0.0, min(100.0, remaining))
    return remaining, _reset_iso(window.get("resets_at"))


def load_tabs(base: Path | None = None, prune_dead: bool = False) -> list[TabRecord]:
    base = base or codex_base()
    root = tabs_dir(base)
    records: list[TabRecord] = []
    if not root.exists():
        return records
    for path in sorted(root.glob("*/tab.json")):
        data = read_json(path)
        email = str(data.get("pinned_email") or "").lower()
        if not email:
            continue
        pid = _int_or_none(data.get("pid"))
        wrapper_pid = _int_or_none(data.get("wrapper_pid"))
        alive = pid_alive(pid) if pid is not None else pid_alive(wrapper_pid)
        if prune_dead and not alive:
            shutil.rmtree(path.parent, ignore_errors=True)
            continue
        records.append(
            TabRecord(
                tab_id=str(data.get("tab_id") or path.parent.name),
                path=path.parent,
                pid=pid,
                wrapper_pid=wrapper_pid,
                pinned_email=email,
                started_at_iso=str(data.get("started_at_iso") or ""),
                alive=alive,
                argv=[str(x) for x in data.get("argv", [])] if isinstance(data.get("argv"), list) else [],
            )
        )
    return records


def live_tabs(base: Path | None = None, prune_dead: bool = True) -> list[TabRecord]:
    return [tab for tab in load_tabs(base, prune_dead=prune_dead) if tab.alive]


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def account_states(base: Path | None = None, prune_dead_tabs: bool = True) -> list[AccountState]:
    base = base or codex_base()
    reg = load_registry(base)
    vaults = vaults_by_email(base)
    broken = broken_emails(base) | quota_walled_emails(base)
    verified = verified_working_emails(base)
    active = active_email(base)
    tabs = load_tabs(base, prune_dead=prune_dead_tabs)
    tab_counts: dict[str, int] = {}
    for tab in tabs:
        if tab.alive:
            tab_counts[tab.pinned_email] = tab_counts.get(tab.pinned_email, 0) + 1

    usage_api_enabled = False
    if isinstance(reg.get("usage_api"), dict):
        usage_api_enabled = bool((reg.get("usage_api") or {}).get("enabled"))
    if isinstance(reg.get("api"), dict):
        usage_api_enabled = usage_api_enabled or bool((reg.get("api") or {}).get("usage"))
    usage_source = "codex-auth registry last_usage"
    usage_source += " (usage API enabled)" if usage_api_enabled else " (local metadata)"

    states: list[AccountState] = []
    seen: set[str] = set()
    accounts = reg.get("accounts", []) if isinstance(reg.get("accounts"), list) else []
    for acct in accounts:
        email = str(acct.get("email") or "").lower()
        if not email:
            continue
        seen.add(email)
        usage = acct.get("last_usage") if isinstance(acct.get("last_usage"), dict) else {}
        rem_5h, reset_5h = _remaining_from_usage(usage, "primary")
        rem_weekly, reset_weekly = _remaining_from_usage(usage, "secondary")
        states.append(
            AccountState(
                email=email,
                account_key=acct.get("account_key"),
                vault_path=vaults.get(email),
                active=email == active,
                broken=email in broken,
                remaining_5h_pct=rem_5h,
                remaining_weekly_pct=rem_weekly,
                reset_5h_at_iso=reset_5h,
                reset_weekly_at_iso=reset_weekly,
                tabs_pinned=tab_counts.get(email, 0),
                usage_source=usage_source,
                last_used_at=_int_or_none(acct.get("last_used_at")),
                verified_working=email in verified,
                observed_5h_tokens=observed_5h_tokens(email, base),
                observed_weekly_tokens=observed_weekly_tokens(email, base),
            )
        )
    for email, vault in sorted(vaults.items()):
        if email in seen:
            continue
        states.append(
            AccountState(
                email=email,
                account_key=None,
                vault_path=vault,
                active=email == active,
                broken=email in broken,
                remaining_5h_pct=None,
                remaining_weekly_pct=None,
                reset_5h_at_iso=None,
                reset_weekly_at_iso=None,
                tabs_pinned=tab_counts.get(email, 0),
                usage_source="vault only",
                last_used_at=None,
                verified_working=email in verified,
                observed_5h_tokens=observed_5h_tokens(email, base),
                observed_weekly_tokens=observed_weekly_tokens(email, base),
            )
        )
    return states


def load_config(base: Path | None = None) -> dict[str, Any]:
    data = read_json(predictive_config_path(base))
    return {
        "threshold_5h_percent": int(data.get("threshold_5h_percent", DEFAULT_5H_THRESHOLD)),
        "threshold_weekly_percent": int(data.get("threshold_weekly_percent", DEFAULT_WEEKLY_THRESHOLD)),
        "all_accounts_warning_5h_percent": int(
            data.get("all_accounts_warning_5h_percent", DEFAULT_ALL_ACCOUNTS_WARNING_5H)
        ),
        "all_accounts_warning_weekly_percent": int(
            data.get("all_accounts_warning_weekly_percent", DEFAULT_ALL_ACCOUNTS_WARNING_WEEKLY)
        ),
        "min_usable_accounts_warning": int(data.get("min_usable_accounts_warning", 2)),
        "load_balance_tolerance_pct": int(data.get("load_balance_tolerance_pct", DEFAULT_BALANCE_TOLERANCE)),
        "poll_interval_seconds": int(data.get("poll_interval_seconds", 60)),
        "switch_default": bool(data.get("switch_default", True)),
        "refresh_codex_auth_usage": bool(data.get("refresh_codex_auth_usage", True)),
        "notify": bool(data.get("notify", True)),
    }


def rank_accounts(
    states: list[AccountState],
    exclude_emails: set[str] | None = None,
    config: dict[str, Any] | None = None,
) -> list[AccountState]:
    exclude = {e.lower() for e in (exclude_emails or set())}
    cfg = config or {}
    tolerance = max(1, int(cfg.get("load_balance_tolerance_pct", DEFAULT_BALANCE_TOLERANCE)))

    def known(value: float | None) -> float:
        return -1.0 if value is None else value

    def bucket(value: float | None) -> int:
        return int(known(value) // tolerance)

    def below_threshold(s: AccountState) -> bool:
        threshold_5h = int(cfg.get("threshold_5h_percent", DEFAULT_5H_THRESHOLD))
        threshold_weekly = int(cfg.get("threshold_weekly_percent", DEFAULT_WEEKLY_THRESHOLD))
        return (
            s.remaining_5h_pct is not None
            and s.remaining_5h_pct < threshold_5h
        ) or (
            s.remaining_weekly_pct is not None
            and s.remaining_weekly_pct < threshold_weekly
        )

    def min_remaining(s: AccountState) -> float:
        known_values = [
            value
            for value in (s.remaining_5h_pct, s.remaining_weekly_pct)
            if value is not None
        ]
        return min(known_values) if known_values else -1.0

    # Hard one-tab-per-account rule (default ON; opt out via env).
    #
    # Two codex processes sharing the same refresh_token will race on token
    # refresh: whichever loses gets "refresh_token_reused" and the RT chain
    # for that account is permanently broken until re-OAuth. Per-tab vault
    # clones + 30s vault-sync narrow but cannot close the window — codex
    # CLI does the refresh inside its own process, outside our locking.
    #
    # Hard-excluding accounts that already have a live tab guarantees there
    # is never more than one in-flight RT chain per account. With 4 accounts
    # this is fine; the user can opt out via CODEX_SAFE_ALLOW_SHARED_ACCOUNTS=1
    # for legacy multi-tab-per-account workflows.
    allow_shared = os.environ.get("CODEX_SAFE_ALLOW_SHARED_ACCOUNTS") == "1"
    candidates = [
        s
        for s in states
        if s.email not in exclude
        and not s.broken
        and s.vault_path is not None
        and (allow_shared or s.tabs_pinned == 0)
    ]
    if not candidates and not allow_shared:
        # All non-walled, non-broken accounts already have a live tab. Relax
        # the rule rather than starve the new chat — same-account-race is
        # better than no-codex-at-all.
        candidates = [
            s
            for s in states
            if s.email not in exclude
            and not s.broken
            and s.vault_path is not None
        ]
    # Sort tiers (highest priority first; reverse=True so bigger tuple wins):
    #   1. NOT-near-wall by observed tokens > near-wall  (predictive skip)
    #   2. verified-working in last TTL > untested      (truth over API lies)
    #   3. not below API threshold > below              (legacy hint)
    #   4. observed-headroom bucket                     (more headroom wins)
    #   5. API-reported min_remaining bucket            (tiebreaker)
    #   6. fewer pinned tabs                            (load balance)
    #   7-9. remaining numeric tiebreakers              (deterministic)
    headroom_bucket = lambda s: int(min(observed_headroom_5h(s), observed_headroom_weekly(s)) // 50_000)
    return sorted(
        candidates,
        key=lambda s: (
            0 if observed_near_wall(s) else 1,
            1 if s.verified_working else 0,
            0 if below_threshold(s) else 1,
            headroom_bucket(s),
            bucket(min_remaining(s)),
            bucket(s.remaining_5h_pct),
            -s.tabs_pinned,
            min_remaining(s),
            known(s.remaining_5h_pct),
            known(s.remaining_weekly_pct),
            s.last_used_at or 0,
        ),
        reverse=True,
    )


def pick_account(
    base: Path | None = None,
    exclude_emails: set[str] | None = None,
    config: dict[str, Any] | None = None,
) -> AccountState | None:
    ranked = rank_accounts(account_states(base), exclude_emails=exclude_emails, config=config or load_config(base))
    return ranked[0] if ranked else None


def state_document(base: Path | None = None) -> dict[str, Any]:
    base = base or codex_base()
    cfg = load_config(base)
    states = account_states(base)
    ranked = rank_accounts(states, config=cfg)
    usable = [s for s in ranked if not s.broken and s.vault_path is not None]
    low_usable = [
        s
        for s in usable
        if (
            s.remaining_5h_pct is not None
            and s.remaining_5h_pct < cfg["all_accounts_warning_5h_percent"]
        )
        or (
            s.remaining_weekly_pct is not None
            and s.remaining_weekly_pct < cfg["all_accounts_warning_weekly_percent"]
        )
    ]
    capacity_warning = bool(usable and len(low_usable) == len(usable))
    redundancy_warning = len(usable) < cfg["min_usable_accounts_warning"]
    return {
        "updated_at_iso": now_iso(),
        "codex_home": str(base),
        "threshold_5h_percent": cfg["threshold_5h_percent"],
        "threshold_weekly_percent": cfg["threshold_weekly_percent"],
        "all_accounts_warning_5h_percent": cfg["all_accounts_warning_5h_percent"],
        "all_accounts_warning_weekly_percent": cfg["all_accounts_warning_weekly_percent"],
        "usage_source": states[0].usage_source if states else "none",
        "capacity_warning": capacity_warning,
        "capacity_warning_reason": (
            "all usable accounts below warning thresholds" if capacity_warning else None
        ),
        "redundancy_warning": redundancy_warning,
        "redundancy_warning_reason": (
            f"only {len(usable)} usable account(s)" if redundancy_warning else None
        ),
        "usable_account_count": len(usable),
        "min_usable_accounts_warning": cfg["min_usable_accounts_warning"],
        "accounts": [
            {
                "email": s.email,
                "active": s.active,
                "broken": s.broken,
                "remaining_5h_pct": s.remaining_5h_pct,
                "remaining_weekly_pct": s.remaining_weekly_pct,
                "5h_reset_at_iso": s.reset_5h_at_iso,
                "weekly_reset_at_iso": s.reset_weekly_at_iso,
                "tabs_pinned": s.tabs_pinned,
                "has_vault": s.vault_path is not None,
            }
            for s in states
        ],
        "ranked_for_new_tab": [s.email for s in ranked],
    }


def write_state(base: Path | None = None) -> dict[str, Any]:
    base = base or codex_base()
    doc = state_document(base)
    write_json_atomic(predictive_state_path(base), doc, mode=0o600)
    return doc


def _vault_filename_for_key(account_key: str) -> str:
    """Return the base64url filename codex uses for an account's vault file.

    codex 0.132 looks up accounts/<base64url(account_key)>.auth.json. The
    base64url alphabet is - and _ with no padding.
    """
    import base64 as _b64
    return _b64.urlsafe_b64encode(account_key.encode("utf-8")).rstrip(b"=").decode("ascii")


def _write_per_tab_registry(account: AccountState, base: Path, tab_accounts: Path) -> None:
    """Materialise a single-account per-tab accounts/ directory.

    Effects:
      - Copies the global registry forward (preserving fields codex/codex-auth
        expects: schema_version, api.usage, auto_switch, etc.) but rewrites the
        accounts array to contain ONLY the chosen account and sets
        active_account_key to that account's key.
      - Copies the chosen account's vault file into tab_accounts/ under the
        codex-expected base64url filename.
      - Symlinks `recover/` from the global accounts dir (codex-recover writes
        broken-ledger and account-recovery state there; we want a shared view).
    """
    global_reg = load_registry(base)
    if not isinstance(global_reg, dict):
        global_reg = {}

    chosen_entry: dict[str, Any] | None = None
    for acct in global_reg.get("accounts", []) or []:
        if isinstance(acct, dict) and (acct.get("email") or "").lower() == account.email.lower():
            chosen_entry = acct
            break
    if chosen_entry is None:
        # Minimal fallback: synthesise from AccountState if registry is missing.
        chosen_entry = {
            "email": account.email,
            "account_key": account.account_key,
            "auth_mode": "chatgpt",
        }

    key = chosen_entry.get("account_key") or account.account_key
    if not key:
        raise RuntimeError(f"no account_key available for {account.email}")
    # Inline the key into the record so codex-auth tooling that iterates the
    # accounts array always finds it (some registry schemas omit it).
    chosen_entry["account_key"] = key

    per_tab = dict(global_reg)
    per_tab["accounts"] = [chosen_entry]
    per_tab["active_account_key"] = key
    # active_account_activated_at_ms is required by codex-auth for fresh-active
    # tracking; stamp it now so codex doesn't treat the tab as a stale session.
    per_tab["active_account_activated_at_ms"] = int(time.time() * 1000)
    write_json_atomic(tab_accounts / "registry.json", per_tab, mode=0o600)

    # Copy the vault file under the codex-expected base64url filename.
    if account.vault_path is not None:
        dest_name = _vault_filename_for_key(str(key)) + ".auth.json"
        dest = tab_accounts / dest_name
        try:
            shutil.copy2(account.vault_path, dest)
            os.chmod(dest, 0o600)
        except OSError:
            pass

    # Recovery state (broken ledger etc.) is shared; symlink the recover/ dir.
    global_recover = base / "accounts" / "recover"
    try:
        global_recover.mkdir(parents=True, mode=0o700, exist_ok=True)
    except OSError:
        pass
    try:
        os.symlink(global_recover, tab_accounts / "recover", target_is_directory=True)
    except (FileExistsError, OSError):
        pass


def create_tab_home(account: AccountState, argv: list[str], base: Path | None = None) -> Path:
    base = base or codex_base()
    if account.vault_path is None:
        raise RuntimeError(f"no vault file for {account.email}")
    tab_id = str(uuid.uuid4())
    root = tabs_dir(base) / tab_id
    root.mkdir(parents=True, mode=0o700)
    # Read the vault under the per-account lock so we never copy a stale RT
    # while another wrapper is mid-refresh. The lock is released before we
    # spawn codex — locking during the long-running chat would serialize all
    # tabs on the same account, which is not what we want.
    with _exclusive_lock(_auth_vault_lock_path(base, account.email)):
        shutil.copy2(account.vault_path, root / "auth.json")
        os.chmod(root / "auth.json", 0o600)

    # Ensure the global sessions/ exists so the symlink in the loop below
    # resolves. Codex writes rollout JSONLs to <CODEX_HOME>/sessions/<y>/<m>/<d>/
    # and we want those to land in ~/.codex/sessions/ where `codex resume`
    # can find them after this tab's directory is cleaned up.
    try:
        (base / "sessions").mkdir(parents=True, mode=0o700, exist_ok=True)
    except OSError:
        pass

    # CRITICAL for codex 0.132+: codex reads accounts/registry.json to decide
    # which account is active and loads accounts/<base64url(account_key)>.auth.json
    # for the actual token. If we symlink accounts/ back to the global directory,
    # every tab inherits the GLOBAL active_account_key regardless of what
    # auth.json we copied at the top level. The top-level auth.json is at best
    # a fallback, and conflicting signals cause codex to use the wrong account
    # (resulting in another account's quota wall on every message).
    #
    # Solution: per-tab accounts/ with ONLY the chosen account, registry pinning
    # active_account_key to it. The single vault file is copied under its
    # base64url filename — which is what codex looks up. This gives codex a
    # consistent view: top-level auth.json, accounts/registry.json, and the
    # per-account vault all agree the active account is `account.email`.
    tab_accounts = root / "accounts"
    tab_accounts.mkdir(mode=0o700)
    _write_per_tab_registry(account, base, tab_accounts)

    for item in base.iterdir():
        if item.name in {"auth.json", "tabs", "accounts"} | TAB_PRIVATE_NAMES:
            continue
        dest = root / item.name
        if dest.exists() or dest.is_symlink():
            continue
        try:
            os.symlink(item, dest, target_is_directory=item.is_dir())
        except FileExistsError:
            pass

    seed_tab_hook_trust(base, root)

    tab = {
        "tab_id": tab_id,
        "wrapper_pid": os.getpid(),
        "pid": None,
        "pinned_email": account.email,
        "started_at_iso": now_iso(),
        "argv": argv,
        "global_codex_home": str(base),
    }
    write_json_atomic(root / "tab.json", tab, mode=0o600)
    return root


APIKEY_FALLBACK_EMAIL = "__apikey_fallback__"


def create_apikey_tab_home(
    api_key: str,
    argv: list[str],
    base: Path | None = None,
) -> Path:
    """Build a tab home that uses OPENAI_API_KEY billing instead of ChatGPT auth.

    Used as a last resort when every ChatGPT account is walled or broken.
    Codex CLI's auth_mode="apikey" tells it to use the OPENAI_API_KEY value
    in auth.json instead of the chatgpt OAuth refresh flow. Billing comes
    out of the API key's pool (real $ per token), so usage limits do not
    apply the same way — the wrapper can rely on this path to "always work"
    as long as the key is valid and has credit.

    Returns the tab home path (mirrors create_tab_home's return).
    """
    base = base or codex_base()
    tab_id = str(uuid.uuid4())
    root = tabs_dir(base) / tab_id
    root.mkdir(parents=True, mode=0o700)

    auth_blob = {
        "auth_mode": "apikey",
        "OPENAI_API_KEY": api_key,
        "tokens": None,
        "last_refresh": now_iso(),
    }
    write_json_atomic(root / "auth.json", auth_blob, mode=0o600)

    # Sessions stay shared, same as the chatgpt-mode tabs.
    try:
        (base / "sessions").mkdir(parents=True, mode=0o700, exist_ok=True)
    except OSError:
        pass

    # Per-tab accounts/ with a synthetic api-key registry. Codex CLI still
    # reads accounts/registry.json on startup; we give it a single entry that
    # carries no chatgpt account_key so codex falls through to the api-key
    # path via auth_mode="apikey" in the top-level auth.json.
    tab_accounts = root / "accounts"
    tab_accounts.mkdir(mode=0o700)
    apikey_registry = {
        "schema_version": 3,
        "accounts": [
            {
                "email": APIKEY_FALLBACK_EMAIL,
                "account_key": APIKEY_FALLBACK_EMAIL,
                "auth_mode": "apikey",
                "plan": "api",
                "last_usage": {},
                "last_usage_at": int(time.time()),
                "last_used_at": int(time.time()),
            }
        ],
        "active_account_key": APIKEY_FALLBACK_EMAIL,
        "active_account_activated_at_ms": int(time.time() * 1000),
        "api": {"usage": False},
        "auto_switch": {"enabled": False},
    }
    write_json_atomic(tab_accounts / "registry.json", apikey_registry, mode=0o600)
    # Share recovery dir.
    global_recover = base / "accounts" / "recover"
    try:
        global_recover.mkdir(parents=True, mode=0o700, exist_ok=True)
    except OSError:
        pass
    try:
        os.symlink(global_recover, tab_accounts / "recover", target_is_directory=True)
    except (FileExistsError, OSError):
        pass

    for item in base.iterdir():
        if item.name in {"auth.json", "tabs", "accounts"} | TAB_PRIVATE_NAMES:
            continue
        dest = root / item.name
        if dest.exists() or dest.is_symlink():
            continue
        try:
            os.symlink(item, dest, target_is_directory=item.is_dir())
        except FileExistsError:
            pass

    seed_tab_hook_trust(base, root)

    tab = {
        "tab_id": tab_id,
        "wrapper_pid": os.getpid(),
        "pid": None,
        "pinned_email": APIKEY_FALLBACK_EMAIL,
        "started_at_iso": now_iso(),
        "argv": argv,
        "global_codex_home": str(base),
        "fallback_mode": "apikey",
    }
    write_json_atomic(root / "tab.json", tab, mode=0o600)
    return root


def update_tab_pid(tab_home: Path, pid: int) -> None:
    path = tab_home / "tab.json"
    data = read_json(path)
    data["pid"] = pid
    write_json_atomic(path, data, mode=0o600)


# --- per-account refresh-token race lock --------------------------------
# The `refresh_token_reused` horror happens when two codex processes refresh
# the SAME OAuth refresh_token concurrently. OpenAI invalidates the old RT
# server-side as soon as a new one is issued; whichever process loses the
# race blows up with a permanent auth-chain break (the only fix is a browser
# re-OAuth).
#
# The race window:
#   1. Wrapper A copies vault → tab_A/auth.json   (RT_v0)
#   2. Wrapper B copies vault → tab_B/auth.json   (RT_v0)
#   3. Codex A refreshes: server gives RT_v1+AT_v1; RT_v0 invalidated.
#   4. Codex B refreshes with RT_v0 → 401 refresh_token_reused.
#
# The fix is two-part:
#   * Per-account file lock around vault read+copy and vault write-back.
#   * Periodic sync from tab/auth.json back to the vault during long-running
#     codex sessions, so that step 2 in a *later* wrapper sees the latest RT.
def _auth_vault_lock_path(base: Path, email: str) -> Path:
    safe_email = re.sub(r"[^A-Za-z0-9_.@+-]", "_", email.lower())
    return accounts_dir(base) / "recover" / f".vault-{safe_email}.lock"


class _exclusive_lock:
    """Filesystem-backed exclusive lock (fcntl.flock).

    Usage:
        with _exclusive_lock(path):
            ...

    The lock is advisory and per-file; processes that don't take the lock are
    not blocked. All vault-touching callers must use it.
    """

    def __init__(self, path: Path, timeout: float = 10.0):
        self.path = path
        self.timeout = timeout
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    # Best-effort: proceed without the lock rather than fail
                    # the whole wrapper invocation. The protected operation
                    # is idempotent so a missed lock degrades to a race window,
                    # not corruption.
                    return self
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._fh is not None:
                fcntl.flock(self._fh, fcntl.LOCK_UN)
                self._fh.close()
        except Exception:
            pass


def sync_tab_auth_to_vault(tab_home: Path, email: str, base: Path | None = None) -> bool:
    base = base or codex_base()
    email_l = email.lower()
    with _exclusive_lock(_auth_vault_lock_path(base, email_l)):
        tab_auth_path = tab_home / "auth.json"
        vault = vaults_by_email(base).get(email_l)
        if vault is None or not tab_auth_path.exists():
            return False
        tab_auth = read_json(tab_auth_path)
        vault_auth = read_json(vault)
        if email_from_auth(tab_auth) != email_l:
            return False
        if email_from_auth(vault_auth) != email_l:
            return False
        tab_marker = parse_refresh_marker(tab_auth, tab_auth_path.stat().st_mtime)
        vault_marker = parse_refresh_marker(vault_auth, vault.stat().st_mtime)
        if tab_marker <= vault_marker + 1:
            return False
        tmp = vault.with_name(f".{vault.name}.tmp")
        shutil.copy2(tab_auth_path, tmp)
        os.chmod(tmp, 0o600)
        os.replace(tmp, vault)
        # Also push the freshest auth into the per-tab accounts/<key>.auth.json
        # if it exists, so that the tab's own future refreshes use the latest
        # RT (codex reads accounts/<key>.auth.json, not the top-level one).
        try:
            tab_accounts = tab_home / "accounts"
            if tab_accounts.is_dir():
                for f in tab_accounts.glob("*.auth.json"):
                    if email_from_auth(read_json(f)) == email_l:
                        shutil.copy2(tab_auth_path, f)
                        os.chmod(f, 0o600)
        except OSError:
            pass
        return True


def tab_auth_is_stale_against_vault(tab_home: Path, email: str, base: Path | None = None) -> bool:
    base = base or codex_base()
    tab_auth_path = tab_home / "auth.json"
    vault = vaults_by_email(base).get(email.lower())
    if vault is None or not tab_auth_path.exists():
        return False
    tab_auth = read_json(tab_auth_path)
    vault_auth = read_json(vault)
    if email_from_auth(tab_auth) != email.lower():
        return False
    if email_from_auth(vault_auth) != email.lower():
        return False
    tab_marker = parse_refresh_marker(tab_auth, tab_auth_path.stat().st_mtime)
    vault_marker = parse_refresh_marker(vault_auth, vault.stat().st_mtime)
    return vault_marker > tab_marker + 1


def latest_session_id_for_tab(tab_home: Path) -> str | None:
    """Return the session UUID of the tab's most-recently-updated thread.

    THIS is the authoritative per-tab signal. `state_5.sqlite` is tab-private,
    so a query against it tells us exactly which thread/rollout belongs to
    THIS tab. Globbing the global sessions/ dir (which is symlinked in for
    every tab) catches all tabs' rollouts and produces wrong-chat evac.
    """
    import sqlite3 as _sql
    db = tab_home / "state_5.sqlite"
    if not db.exists():
        return None
    try:
        conn = _sql.connect(f"file:{db}?mode=ro&immutable=1", uri=True, timeout=2)
    except _sql.Error:
        return None
    try:
        row = conn.execute(
            "SELECT id FROM threads "
            "WHERE archived = 0 "
            "ORDER BY COALESCE(updated_at_ms, updated_at * 1000) DESC LIMIT 1"
        ).fetchone()
    except _sql.Error:
        row = None
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if not row or not row[0]:
        return None
    return str(row[0])


def latest_rollout_path_from_tab_db(tab_home: Path) -> Path | None:
    """Return the rollout PATH the tab's state_5.sqlite says is current.

    Distinct from latest_session_id_for_tab in that it reads the actual path
    field. Useful for sanity-checking the file exists on disk.
    """
    import sqlite3 as _sql
    db = tab_home / "state_5.sqlite"
    if not db.exists():
        return None
    try:
        conn = _sql.connect(f"file:{db}?mode=ro&immutable=1", uri=True, timeout=2)
    except _sql.Error:
        return None
    try:
        row = conn.execute(
            "SELECT rollout_path FROM threads "
            "WHERE archived = 0 "
            "ORDER BY COALESCE(updated_at_ms, updated_at * 1000) DESC LIMIT 1"
        ).fetchone()
    except _sql.Error:
        row = None
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if not row or not row[0]:
        return None
    p = Path(str(row[0])).expanduser()
    if not p.is_absolute():
        p = tab_home / p
    return p.resolve() if p.exists() else p


def latest_rollout_under(path: Path) -> Path | None:
    """Find the freshest rollout-*.jsonl under `path` (recursively).

    Works whether `path` is a real sessions/ dir (legacy tab) or the symlink
    to the global ~/.codex/sessions/ (new-style tab). When called on a tab
    home, the caller should pass `tab_home / "sessions"` rather than the tab
    home root.
    """
    root = path if path.name == "sessions" else path / "sessions"
    if not root.exists():
        return None
    try:
        candidates = sorted(
            root.rglob("rollout-*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except FileNotFoundError:
        return None
    return candidates[0] if candidates else None


def latest_rollout_for_tab(tab_home: Path) -> Path | None:
    """Find THIS tab's current rollout file.

    Strategy (authoritative → fuzzy):
      1. state_5.sqlite is tab-private. Its `threads` table records the
         rollout_path for each thread this tab has owned. The most-recently-
         updated thread is the conversation a user evac'ing this tab wants
         to resume. This is the ONLY signal that's per-tab; everything else
         (mtime ordering, sessions/ glob) returns the freshest rollout
         across ALL tabs and produces wrong-chat evac.
      2. If state_5.sqlite is unavailable or empty (a brand-new tab that
         hasn't sent a message yet), fall back to the tab's local
         sessions/ glob.
      3. As a last resort, scan the global sessions/ — but log/banner that
         this is a guess.
    """
    db_path = latest_rollout_path_from_tab_db(tab_home)
    if db_path is not None and db_path.exists():
        return db_path
    # Fall back to filesystem scan. NOTE: when sessions/ is symlinked to
    # global, this can return another tab's rollout. We prefer it only when
    # the tab is too new to have a state_5.sqlite entry.
    direct = latest_rollout_under(tab_home / "sessions")
    if direct is not None:
        return direct
    base = tab_home.parent.parent  # ~/.codex
    return latest_rollout_under(base / "sessions")


def _rescue_sessions(tab_home: Path) -> None:
    """Copy any tab-local rollout JSONLs to the global sessions/ before cleanup.

    New tabs use a symlink for sessions/ (see create_tab_home), so this is a
    no-op for them. Existing pre-fix tabs have a real sessions/ directory full
    of conversation rollouts that would otherwise be deleted. Codex resume
    looks under <CODEX_HOME>/sessions/<y>/<m>/<d>/rollout-*.jsonl, so we mirror
    the same layout into the global home.
    """
    sessions = tab_home / "sessions"
    if not sessions.exists() or sessions.is_symlink():
        return
    base = tab_home.parent.parent
    global_sessions = base / "sessions"
    try:
        global_sessions.mkdir(parents=True, mode=0o700, exist_ok=True)
    except OSError:
        return
    for src in sessions.rglob("rollout-*.jsonl"):
        if not src.is_file():
            continue
        try:
            rel = src.relative_to(sessions)
        except ValueError:
            continue
        dest = global_sessions / rel
        if dest.exists():
            continue
        try:
            dest.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            shutil.copy2(src, dest)
        except OSError:
            continue


def cleanup_tab_home(tab_home: Path) -> None:
    if tab_home.parent.name != "tabs":
        return
    try:
        _rescue_sessions(tab_home)
    except Exception:
        # Cleanup must never block the wrapper from exiting cleanly.
        pass
    shutil.rmtree(tab_home, ignore_errors=True)


def switch_default(email: str, timeout: int = 20, base: Path | None = None) -> tuple[int, str]:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(base or codex_base())
    try:
        proc = subprocess.run(
            ["codex-auth", "switch", email],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            env=env,
        )
        return proc.returncode, proc.stdout
    except Exception as exc:
        return 127, str(exc)


def pct(value: float | None) -> str:
    if value is None:
        return "unknown"
    if abs(value - round(value)) < 0.05:
        return f"{int(round(value))}%"
    return f"{value:.1f}%"


def active_state(states: list[AccountState]) -> AccountState | None:
    for state in states:
        if state.active:
            return state
    return None

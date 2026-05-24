#!/usr/bin/env python3
"""Shared helpers for Codex Hot Swap.

The library is deliberately conservative:

* usage refresh defaults off;
* quota walls are consumed from a cached structured file;
* real credentials are never printed;
* rollout files are read-only evidence.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_CONFIG: dict[str, Any] = {
    "threshold_5h_percent": 25,
    "threshold_weekly_percent": 15,
    "all_accounts_warning_5h_percent": 35,
    "all_accounts_warning_weekly_percent": 20,
    "load_balance_tolerance_pct": 15,
    "min_usable_accounts_warning": 2,
    "poll_interval_seconds": 60,
    "quota_wall_max_age_seconds": 300,
    "live_migrate_below_5h_percent": 0,
    "live_migrate_below_weekly_percent": 0,
    "live_migrate_idle_seconds": 2,
    "max_live_migrations_per_tab": 4,
    "switch_default": False,
    "refresh_codex_auth_usage": False,
    "notify": True,
}

SHARED_TAB_NAMES = {
    "config.toml",
    "hooks.json",
    "sessions",
    "skills",
    "plugins",
    "AGENTS.md",
}

PRIVATE_TAB_NAMES = {
    "auth.json",
    "history.jsonl",
    "session_index.jsonl",
    "state.db",
    "state_5.sqlite",
    "logs_2.sqlite",
    "sqlite",
    "tmp",
    "log",
    "debug",
    "shell_snapshots",
}

AUTH_FAILURE_MARKERS = (
    "refresh token was already used",
    "refresh token was revoked",
    "your access token could not be refreshed",
    "authentication token has been invalidated",
    "token_revoked",
    "invalid_grant",
)


@dataclass(frozen=True)
class AccountState:
    key: str
    email: str
    auth_path: Path
    auth_exists: bool
    remaining_5h: float | None
    remaining_weekly: float | None
    reset_5h: float | None
    reset_weekly: float | None
    broken: bool
    live_tabs: int
    last_used: float
    raw: dict[str, Any]

    @property
    def usable_remaining(self) -> float:
        values = [
            value
            for value in (self.remaining_5h, self.remaining_weekly)
            if value is not None
        ]
        if not values:
            return 50.0
        return min(values)


def now() -> float:
    return time.time()


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def config_paths(home: Path) -> list[Path]:
    if os.environ.get("CODEX_HOTSWAP_CONFIG"):
        return [Path(os.environ["CODEX_HOTSWAP_CONFIG"]).expanduser()]
    return [
        home / "codex-hotswap.json",
        home / "predictive.config.json",
    ]


def load_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default
    except PermissionError:
        return default


def load_config(home: Path | None = None) -> dict[str, Any]:
    home = home or codex_home()
    config = dict(DEFAULT_CONFIG)
    for path in config_paths(home):
        data = load_json(path, None)
        if isinstance(data, dict):
            config.update(data)
            break
    return config


def write_json_atomic(path: Path, data: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


def accounts_dir(home: Path) -> Path:
    return home / "accounts"


def registry_path(home: Path) -> Path:
    return accounts_dir(home) / "registry.json"


def wall_cache_path(home: Path) -> Path:
    return home / "predictive_quota_walls.json"


def broken_ledger_path(home: Path) -> Path:
    return accounts_dir(home) / "recover" / "broken.tsv"


@contextlib.contextmanager
def locked_path(path: Path, exclusive: bool = True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        handle.seek(0)
        try:
            yield handle
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def broken_emails(home: Path) -> set[str]:
    path = broken_ledger_path(home)
    if not path.exists():
        return set()
    emails: set[str] = set()
    with locked_path(path, exclusive=False) as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if parts and parts[0]:
                emails.add(parts[0].lower())
    return emails


def mark_broken(home: Path, email: str) -> None:
    email = email.lower()
    path = broken_ledger_path(home)
    rows: dict[str, str] = {}
    with locked_path(path, exclusive=True) as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if parts and parts[0]:
                rows[parts[0].lower()] = parts[1] if len(parts) > 1 else "0"
        rows[email] = str(int(now()))
        handle.seek(0)
        handle.truncate()
        for key in sorted(rows):
            handle.write(f"{key}\t{rows[key]}\n")
        handle.flush()
        os.fsync(handle.fileno())


def reset_broken(home: Path, email: str) -> None:
    email = email.lower()
    path = broken_ledger_path(home)
    if not path.exists():
        return
    with locked_path(path, exclusive=True) as handle:
        rows = [
            line
            for line in handle.read().splitlines()
            if not line.lower().startswith(email + "\t")
        ]
        handle.seek(0)
        handle.truncate()
        for row in rows:
            handle.write(row + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _iter_registry_records(data: Any) -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(data, list):
        for idx, item in enumerate(data):
            if isinstance(item, dict):
                yield str(item.get("key") or item.get("id") or idx), item
        return

    if not isinstance(data, dict):
        return

    accounts = data.get("accounts")
    if isinstance(accounts, list):
        for idx, item in enumerate(accounts):
            if isinstance(item, dict):
                yield str(item.get("key") or item.get("id") or idx), item
        return
    if isinstance(accounts, dict):
        for key, item in accounts.items():
            if isinstance(item, dict):
                item = dict(item)
                item.setdefault("key", key)
                yield str(key), item
        return

    for key, item in data.items():
        if isinstance(item, dict) and (
            "email" in item
            or "auth" in key.lower()
            or "used_percent" in json.dumps(item, sort_keys=True).lower()
        ):
            item = dict(item)
            item.setdefault("key", key)
            yield str(key), item


def _email_for_record(key: str, record: dict[str, Any]) -> str:
    for name in ("email", "account_email", "user_email", "username", "name"):
        value = record.get(name)
        if isinstance(value, str) and value:
            return value
    return key


def _auth_path_for_record(home: Path, key: str, record: dict[str, Any]) -> Path:
    for name in ("auth_path", "auth_file", "auth_json", "path", "vault_path"):
        value = record.get(name)
        if isinstance(value, str) and value:
            path = Path(value).expanduser()
            return path if path.is_absolute() else accounts_dir(home) / path
    for candidate in (
        accounts_dir(home) / f"{key}.auth.json",
        accounts_dir(home) / f"{record.get('id', key)}.auth.json",
    ):
        if candidate.exists():
            return candidate
    return accounts_dir(home) / f"{key}.auth.json"


def _as_percent(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        return 0.0
    if numeric > 100:
        return 100.0
    return numeric


def _remaining_from_window(window: Any) -> tuple[float | None, float | None]:
    if not isinstance(window, dict):
        return None, None

    remaining = None
    for name in ("remaining_percent", "remaining", "available_percent"):
        if name in window:
            remaining = _as_percent(window.get(name))
            break
    if remaining is None and "used_percent" in window:
        used = _as_percent(window.get("used_percent"))
        remaining = None if used is None else max(0.0, 100.0 - used)

    reset = None
    for name in ("resets_at", "reset_at", "reset", "window_reset_at"):
        if name in window:
            try:
                reset = float(window.get(name))
            except (TypeError, ValueError):
                reset = None
            break
    return remaining, reset


def _find_window(record: dict[str, Any], names: tuple[str, ...]) -> tuple[float | None, float | None]:
    for name in names:
        if name in record:
            value = record[name]
            if isinstance(value, dict):
                remaining, reset = _remaining_from_window(value)
                if remaining is not None or reset is not None:
                    return remaining, reset
            remaining = _as_percent(value)
            if remaining is not None:
                return remaining, None

    usage = record.get("usage")
    if isinstance(usage, dict):
        for name in names:
            if name in usage:
                remaining, reset = _remaining_from_window(usage[name])
                if remaining is not None or reset is not None:
                    return remaining, reset

    # codex-auth registry stores used_percent literally. Search for nested
    # windows with recognizable names and convert used -> remaining.
    for key, value in record.items():
        lowered = key.lower().replace("-", "_")
        if any(name in lowered for name in names) and isinstance(value, dict):
            remaining, reset = _remaining_from_window(value)
            if remaining is not None or reset is not None:
                return remaining, reset

    return None, None


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


def live_tabs(home: Path) -> list[dict[str, Any]]:
    tabs_root = home / "tabs"
    if not tabs_root.exists():
        return []
    tabs: list[dict[str, Any]] = []
    for tab_file in tabs_root.glob("*/tab.json"):
        data = load_json(tab_file, None)
        if not isinstance(data, dict):
            continue
        wrapper_pid = data.get("wrapper_pid")
        child_pid = data.get("child_pid")
        if pid_alive(int(wrapper_pid or 0)) or pid_alive(int(child_pid or 0)):
            data["_tab_file"] = str(tab_file)
            tabs.append(data)
    return tabs


def account_states(home: Path | None = None, config: dict[str, Any] | None = None) -> list[AccountState]:
    home = home or codex_home()
    config = config or load_config(home)
    registry = load_json(registry_path(home), {})
    broken = broken_emails(home)
    live = live_tabs(home)
    live_by_email: dict[str, int] = {}
    for tab in live:
        email = str(tab.get("email") or "").lower()
        if email:
            live_by_email[email] = live_by_email.get(email, 0) + 1

    states: list[AccountState] = []
    for key, record in _iter_registry_records(registry):
        email = _email_for_record(key, record)
        auth_path = _auth_path_for_record(home, key, record)
        remaining_5h, reset_5h = _find_window(
            record,
            ("5h", "five_hour", "five_hours", "primary", "window_5h", "rolling"),
        )
        remaining_weekly, reset_weekly = _find_window(
            record,
            ("weekly", "week", "secondary", "window_weekly"),
        )
        last_used = 0.0
        for name in ("last_used", "last_used_at", "updated_at"):
            try:
                last_used = float(record.get(name) or 0)
                break
            except (TypeError, ValueError):
                pass
        states.append(
            AccountState(
                key=key,
                email=email,
                auth_path=auth_path,
                auth_exists=auth_path.exists(),
                remaining_5h=remaining_5h,
                remaining_weekly=remaining_weekly,
                reset_5h=reset_5h,
                reset_weekly=reset_weekly,
                broken=email.lower() in broken,
                live_tabs=live_by_email.get(email.lower(), 0),
                last_used=last_used,
                raw=record,
            )
        )
    return states


def _wall_entry_is_active(entry: dict[str, Any], clock: float) -> bool:
    remaining = _as_percent(entry.get("remaining_percent"))
    if remaining is None and "used_percent" in entry:
        used = _as_percent(entry.get("used_percent"))
        remaining = None if used is None else max(0.0, 100.0 - used)
    if remaining is None or remaining > 0:
        return False

    reset = None
    for name in ("resets_at", "reset_at", "reset"):
        if name in entry:
            try:
                reset = float(entry[name])
            except (TypeError, ValueError):
                reset = None
            break
    return reset is None or reset > clock


def quota_walled_account_info(
    home: Path | None = None,
    config: dict[str, Any] | None = None,
    clock: float | None = None,
) -> dict[str, dict[str, Any]]:
    home = home or codex_home()
    config = config or load_config(home)
    clock = clock or now()
    path = wall_cache_path(home)
    data = load_json(path, None)
    if not isinstance(data, dict):
        return {}

    written_at = data.get("written_at") or data.get("updated_at") or data.get("timestamp")
    try:
        age = clock - float(written_at)
    except (TypeError, ValueError):
        return {}
    if age < 0 or age > float(config.get("quota_wall_max_age_seconds", 300)):
        return {}

    raw_accounts = data.get("accounts", {})
    items: Iterable[Any]
    if isinstance(raw_accounts, dict):
        items = raw_accounts.items()
    elif isinstance(raw_accounts, list):
        items = [(None, item) for item in raw_accounts]
    else:
        return {}

    result: dict[str, dict[str, Any]] = {}
    for key, item in items:
        if not isinstance(item, dict):
            continue
        email = str(item.get("email") or key or "").lower()
        if not email:
            continue
        windows = item.get("windows", item)
        active: dict[str, Any] = {}
        if isinstance(windows, dict):
            for win_name, win_entry in windows.items():
                if isinstance(win_entry, dict) and _wall_entry_is_active(win_entry, clock):
                    active[str(win_name)] = win_entry
        if active:
            result[email] = {
                "email": email,
                "windows": active,
                "written_at": written_at,
            }
    return result


def quota_walled_emails(home: Path | None = None, config: dict[str, Any] | None = None) -> set[str]:
    return set(quota_walled_account_info(home, config).keys())


def pick_account(
    states: list[AccountState],
    config: dict[str, Any],
    excluded: set[str] | None = None,
    walled: set[str] | None = None,
) -> AccountState | None:
    excluded = {item.lower() for item in (excluded or set())}
    walled = {item.lower() for item in (walled or set())}
    candidates = [
        state
        for state in states
        if state.auth_exists
        and not state.broken
        and state.email.lower() not in excluded
        and state.email.lower() not in walled
    ]
    if not candidates:
        return None

    def score(state: AccountState) -> tuple[float, int, float, str]:
        return (
            state.usable_remaining,
            -state.live_tabs,
            -state.last_used,
            state.email,
        )

    return sorted(candidates, key=score, reverse=True)[0]


def run_usage_refresh_if_enabled(home: Path, config: dict[str, Any]) -> bool:
    if not config.get("refresh_codex_auth_usage", False):
        return False
    env = dict(os.environ)
    env["CODEX_HOME"] = str(home)
    try:
        subprocess.run(
            ["codex-auth", "list"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            timeout=120,
            check=False,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def write_quota_wall_cache(home: Path, states: list[AccountState]) -> dict[str, Any]:
    clock = now()
    accounts: dict[str, Any] = {}
    for state in states:
        windows: dict[str, Any] = {}
        for name, remaining, reset in (
            ("5h", state.remaining_5h, state.reset_5h),
            ("weekly", state.remaining_weekly, state.reset_weekly),
        ):
            if remaining is None or remaining > 0:
                continue
            if reset is not None and reset <= clock:
                continue
            windows[name] = {
                "remaining_percent": remaining,
                "resets_at": reset,
            }
        if windows:
            accounts[state.email.lower()] = {
                "email": state.email,
                "account_key": state.key,
                "windows": windows,
            }
    data = {
        "written_at": clock,
        "accounts": accounts,
    }
    write_json_atomic(wall_cache_path(home), data)
    return data


def ensure_tab_home(tab_home: Path, home: Path) -> None:
    tab_home.mkdir(parents=True, exist_ok=True)
    for name in SHARED_TAB_NAMES:
        src = home / name
        dst = tab_home / name
        if dst.exists() or dst.is_symlink() or not src.exists():
            continue
        dst.symlink_to(src, target_is_directory=src.is_dir())
    for name in ("tmp", "log", "debug", "sqlite", "shell_snapshots"):
        (tab_home / name).mkdir(exist_ok=True)


def create_tab_home(home: Path, account: AccountState) -> tuple[str, Path]:
    tab_id = str(uuid.uuid4())
    tab_home = home / "tabs" / tab_id
    ensure_tab_home(tab_home, home)
    shutil.copy2(account.auth_path, tab_home / "auth.json")
    os.chmod(tab_home / "auth.json", 0o600)
    return tab_id, tab_home


def write_tab_state(
    tab_home: Path,
    *,
    tab_id: str,
    account: AccountState,
    argv: list[str],
    wrapper_pid: int,
    child_pid: int | None,
    status: str,
    migrations: int = 0,
) -> None:
    write_json_atomic(
        tab_home / "tab.json",
        {
            "tab_id": tab_id,
            "email": account.email,
            "account_key": account.key,
            "auth_path": str(account.auth_path),
            "argv": argv,
            "wrapper_pid": wrapper_pid,
            "child_pid": child_pid,
            "status": status,
            "migrations": migrations,
            "updated_at": now(),
        },
    )


def tab_auth_is_stale_against_vault(tab_home: Path, account: AccountState) -> bool:
    tab_auth = tab_home / "auth.json"
    if not tab_auth.exists() or not account.auth_path.exists():
        return False
    return tab_auth.stat().st_mtime < account.auth_path.stat().st_mtime


def sync_tab_auth_to_vault(tab_home: Path, account: AccountState) -> bool:
    tab_auth = tab_home / "auth.json"
    vault = account.auth_path
    if not tab_auth.exists() or not vault.exists():
        return False
    if tab_auth.stat().st_mtime <= vault.stat().st_mtime:
        return False

    lock_path = vault.with_suffix(vault.suffix + ".lock")
    with locked_path(lock_path, exclusive=True):
        if vault.exists() and tab_auth.stat().st_mtime <= vault.stat().st_mtime:
            return False
        tmp = vault.with_name(f".{vault.name}.{os.getpid()}.tmp")
        shutil.copy2(tab_auth, tmp)
        os.chmod(tmp, 0o600)
        os.replace(tmp, vault)
    return True


def cleanup_tab_home(tab_home: Path) -> None:
    if os.environ.get("CODEX_HOTSWAP_KEEP_TABS"):
        return
    with contextlib.suppress(FileNotFoundError):
        shutil.rmtree(tab_home)


def output_has_auth_failure(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in AUTH_FAILURE_MARKERS)


def latest_rollout_from_sqlite(tab_home: Path) -> Path | None:
    db = tab_home / "state_5.sqlite"
    if not db.exists():
        return None
    uri = f"file:{db}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=1)
    except sqlite3.Error:
        return None
    try:
        rows = conn.execute(
            "select rollout_path from threads "
            "where rollout_path is not null "
            "order by updated_at desc limit 1"
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not rows:
        return None
    path = Path(str(rows[0][0])).expanduser()
    if not path.is_absolute():
        path = tab_home / path
    return path if path.exists() else None


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(part for part in (_extract_text(item) for item in value) if part)
    if isinstance(value, dict):
        for key in ("text", "content", "message"):
            if key in value:
                text = _extract_text(value[key])
                if text:
                    return text
        return "\n".join(
            part for part in (_extract_text(item) for item in value.values()) if part
        )
    return ""


def _role_and_text(event: dict[str, Any]) -> tuple[str | None, str | None]:
    candidates = [event]
    for key in ("item", "message", "response_item"):
        if isinstance(event.get(key), dict):
            candidates.append(event[key])
    for candidate in candidates:
        role = candidate.get("role") or candidate.get("author")
        if role in ("user", "assistant"):
            text = _extract_text(candidate.get("content") or candidate.get("text") or candidate)
            if text:
                return str(role), text.strip()
    return None, None


def build_transfer_prompt(rollout: Path, max_chars: int = 30000) -> str:
    turns: list[tuple[str, str]] = []
    try:
        with rollout.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                role, text = _role_and_text(event)
                if role and text:
                    turns.append((role, text))
    except OSError:
        turns = []

    body_parts: list[str] = []
    remaining = max_chars
    for role, text in reversed(turns):
        block = f"\n\n[{role}]\n{text}"
        if len(block) > remaining:
            if role == "user" and remaining > 500:
                block = block[:remaining]
            else:
                continue
        body_parts.append(block)
        remaining -= len(block)
        if remaining <= 0:
            break
    body = "".join(reversed(body_parts)).strip()
    if not body:
        body = "No usable rollout turns could be reconstructed."

    return (
        "This Codex chat was automatically migrated to a fresh account after "
        "the prior pinned account reached a quota wall.\n\n"
        "The following is reconstructed from local rollout JSONL. Tool calls, "
        "tool outputs, MCP server state, and hidden process state may be "
        "missing. Re-orient from the visible context, preserve the user's "
        "intent, and ask one concise question only if the next action is "
        "ambiguous.\n\n"
        f"{body}"
    )


def format_percent(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"{value:.0f}%"


def format_time(epoch: float | None) -> str:
    if not epoch:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(epoch))

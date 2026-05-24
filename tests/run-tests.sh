#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
sandbox="$(mktemp -d)"
trap 'rm -rf "$sandbox"' EXIT

cd "$repo"

python3 -m py_compile \
  bin/codex_hot_swap_lib.py \
  bin/codex-status \
  bin/codex-predictive-daemon \
  bin/codex-safe \
  bin/codex-continue \
  bin/codex-rescue \
  bin/codex-smooth-mode \
  bin/codex-validate
bash -n install.sh
python3 tests/test_lib.py

./bin/codex-safe --help >/dev/null

wrapper_home="$sandbox/wrapper-home"
mkdir -p "$wrapper_home/accounts"
cat >"$wrapper_home/accounts/registry.json" <<'JSON'
{
  "accounts": {
    "a": {
      "email": "a@example.com",
      "auth_path": "a.auth.json",
      "usage": {
        "5h": {"used_percent": 10},
        "weekly": {"used_percent": 10}
      }
    },
    "b": {
      "email": "b@example.com",
      "auth_path": "b.auth.json",
      "usage": {
        "5h": {"used_percent": 20},
        "weekly": {"used_percent": 20}
      }
    }
  }
}
JSON
printf '{}\n' >"$wrapper_home/accounts/a.auth.json"
printf '{}\n' >"$wrapper_home/accounts/b.auth.json"

fake_log="$sandbox/fake-codex.log"
PATH="$repo/tests/fakes:$PATH" CODEX_HOME="$wrapper_home" FAKE_CODEX_LOG="$fake_log" ./bin/codex-safe login >/dev/null
grep -q "argv=login" "$fake_log"
grep -q "home=$wrapper_home" "$fake_log"
: >"$fake_log"
PATH="$repo/tests/fakes:$PATH" CODEX_HOME="$wrapper_home" FAKE_CODEX_LOG="$fake_log" ./bin/codex-safe mcp list >/dev/null
grep -q "argv=mcp list" "$fake_log"
: >"$fake_log"
PATH="$repo/tests/fakes:$PATH" CODEX_HOME="$wrapper_home" FAKE_CODEX_LOG="$fake_log" ./bin/codex-safe completion zsh >/dev/null
grep -q "argv=completion zsh" "$fake_log"

: >"$fake_log"
PATH="$repo/tests/fakes:$PATH" CODEX_HOME="$wrapper_home" CODEX_HOTSWAP_KEEP_TABS=1 FAKE_CODEX_LOG="$fake_log" ./bin/codex-safe "hello" >/dev/null
wrapped_home="$(awk -F= '/^home=/{print $2; exit}' "$fake_log")"
case "$wrapped_home" in
  "$wrapper_home"/tabs/*) ;;
  *) echo "expected per-tab CODEX_HOME, got $wrapped_home" >&2; exit 1 ;;
esac
test -f "$wrapped_home/auth.json"

: >"$fake_log"
PATH="$repo/tests/fakes:$PATH" \
  CODEX_HOME="$wrapper_home" \
  CODEX_HOTSWAP_KEEP_TABS=1 \
  CMUX_WORKSPACE_ID=workspace-1 \
  CMUX_PANEL_ID=pane-1 \
  CMUX_TAB_ID=surface-1 \
  CMUX_SURFACE_ID=surface-1 \
  FAKE_CODEX_LOG="$fake_log" \
  ./bin/codex-safe "hello" >/dev/null
cmux_wrapped_home="$(awk -F= '/^home=/{print $2; exit}' "$fake_log")"
grep -q '"workspace_id": "workspace-1"' "$cmux_wrapped_home/tab.json"
grep -q '"panel_id": "pane-1"' "$cmux_wrapped_home/tab.json"
grep -q '"surface_id": "surface-1"' "$cmux_wrapped_home/tab.json"

cat >"$wrapper_home/predictive_quota_walls.json" <<JSON
{
  "written_at": $(python3 - <<'PY'
import time
print(time.time())
PY
),
  "accounts": {
    "a@example.com": {
      "email": "a@example.com",
      "windows": {
        "weekly": {
          "remaining_percent": 0,
          "resets_at": $(python3 - <<'PY'
import time
print(time.time() + 3600)
PY
)
        }
      }
    }
  }
}
JSON
: >"$fake_log"
PATH="$repo/tests/fakes:$PATH" CODEX_HOME="$wrapper_home" CODEX_HOTSWAP_KEEP_TABS=1 FAKE_CODEX_LOG="$fake_log" ./bin/codex-safe "hello" >/dev/null
wrapped_home="$(awk -F= '/^home=/{print $2; exit}' "$fake_log")"
grep -q '"email": "b@example.com"' "$wrapped_home/tab.json"

cat >"$wrapper_home/predictive_quota_walls.json" <<JSON
{
  "written_at": $(python3 - <<'PY'
import time
print(time.time())
PY
),
  "accounts": {
    "a@example.com": {
      "email": "a@example.com",
      "windows": {
        "weekly": {
          "remaining_percent": 0,
          "resets_at": $(python3 - <<'PY'
import time
print(time.time() + 3600)
PY
)
        }
      }
    },
    "b@example.com": {
      "email": "b@example.com",
      "windows": {
        "weekly": {
          "remaining_percent": 0,
          "resets_at": $(python3 - <<'PY'
import time
print(time.time() + 3600)
PY
)
        }
      }
    }
  }
}
JSON
: >"$fake_log"
if PATH="$repo/tests/fakes:$PATH" CODEX_HOME="$wrapper_home" FAKE_CODEX_LOG="$fake_log" ./bin/codex-safe "hello" >"$sandbox/all-walled.out"; then
  echo "codex-safe unexpectedly succeeded when all accounts were walled" >&2
  exit 1
fi
grep -q "no usable account" "$sandbox/all-walled.out"
test ! -s "$fake_log"

rm -f "$wrapper_home/predictive_quota_walls.json"
rm -f "$wrapper_home/accounts/recover/broken.tsv"
: >"$fake_log"
PATH="$repo/tests/fakes:$PATH" CODEX_HOME="$wrapper_home" CODEX_HOTSWAP_KEEP_TABS=1 FAKE_CODEX_LOG="$fake_log" FAKE_CODEX_OUTPUT="You've hit your usage limit" ./bin/codex-safe "hello" >/dev/null
test ! -e "$wrapper_home/accounts/recover/broken.tsv"

: >"$fake_log"
if PATH="$repo/tests/fakes:$PATH" CODEX_HOME="$wrapper_home" FAKE_CODEX_LOG="$fake_log" FAKE_CODEX_EXIT=42 ./bin/codex-safe "hello" >/dev/null; then
  echo "codex-safe unexpectedly succeeded for child exit 42" >&2
  exit 1
else
  code=$?
  test "$code" -eq 42
fi

: >"$fake_log"
PATH="$repo/tests/fakes:$PATH" CODEX_HOME="$wrapper_home" FAKE_CODEX_LOG="$fake_log" FAKE_CODEX_OUTPUT="refresh token was revoked" FAKE_CODEX_EXIT=0 ./bin/codex-safe "hello" >/dev/null
test ! -e "$wrapper_home/accounts/recover/broken.tsv"

stale_auth_home="$sandbox/stale-auth-home"
mkdir -p "$stale_auth_home/accounts"
cat >"$stale_auth_home/accounts/registry.json" <<'JSON'
{
  "accounts": {
    "a": {
      "email": "a@example.com",
      "auth_path": "a.auth.json",
      "usage": {
        "5h": {"used_percent": 10},
        "weekly": {"used_percent": 10}
      }
    }
  }
}
JSON
printf '{}\n' >"$stale_auth_home/accounts/a.auth.json"
: >"$fake_log"
if PATH="$repo/tests/fakes:$PATH" CODEX_HOME="$stale_auth_home" FAKE_CODEX_LOG="$fake_log" FAKE_CODEX_OUTPUT="refresh token was revoked" FAKE_CODEX_EXIT=1 FAKE_CODEX_TOUCH_PATH="$stale_auth_home/accounts/a.auth.json" ./bin/codex-safe "hello" >/dev/null; then
  echo "stale auth retry unexpectedly succeeded" >&2
  exit 1
fi
test ! -e "$stale_auth_home/accounts/recover/broken.tsv"

broken_auth_home="$sandbox/broken-auth-home"
mkdir -p "$broken_auth_home/accounts"
cat >"$broken_auth_home/accounts/registry.json" <<'JSON'
{
  "accounts": {
    "a": {
      "email": "a@example.com",
      "auth_path": "a.auth.json",
      "usage": {
        "5h": {"used_percent": 10},
        "weekly": {"used_percent": 10}
      }
    }
  }
}
JSON
printf '{}\n' >"$broken_auth_home/accounts/a.auth.json"
: >"$fake_log"
if PATH="$repo/tests/fakes:$PATH" CODEX_HOME="$broken_auth_home" FAKE_CODEX_LOG="$fake_log" FAKE_CODEX_OUTPUT="refresh token was revoked" FAKE_CODEX_EXIT=1 ./bin/codex-safe "hello" >/dev/null; then
  echo "broken auth retry unexpectedly succeeded" >&2
  exit 1
fi
grep -q "a@example.com" "$broken_auth_home/accounts/recover/broken.tsv"

rollout_dir="$wrapper_home/sessions/2026/05/24"
mkdir -p "$rollout_dir"
rollout="$rollout_dir/rollout-test.jsonl"
cat >"$rollout" <<'JSONL'
{"item":{"role":"user","content":[{"type":"text","text":"Please continue the work."}]}}
{"item":{"role":"assistant","content":[{"type":"text","text":"I will inspect the repo next."}]}}
JSONL
prompt_out="$sandbox/transfer-prompt.out"
CODEX_HOME="$wrapper_home" ./bin/codex-continue "$rollout" --print >"$prompt_out"
grep -q "automatically migrated" "$prompt_out"
grep -q "Please continue the work" "$prompt_out"
grep -q "I will inspect the repo next" "$prompt_out"

: >"$fake_log"
PATH="$repo/tests/fakes:$PATH" CODEX_HOME="$wrapper_home" FAKE_CODEX_LOG="$fake_log" ./bin/codex-validate --to a@example.com >/dev/null
grep -q "argv=exec --skip-git-repo-check" "$fake_log"
validate_home="$(awk -F= '/^home=/{print $2; exit}' "$fake_log")"
case "$validate_home" in
  "$wrapper_home"/tmp/validate-*) ;;
  *) echo "expected validate temp CODEX_HOME, got $validate_home" >&2; exit 1 ;;
esac
test ! -e "$validate_home"

CODEX_HOME="$wrapper_home" ./bin/codex-rescue >/dev/null

live_tab="$wrapper_home/tabs/live-test"
mkdir -p "$live_tab"
cat >"$live_tab/tab.json" <<JSON
{
  "tab_id": "live-test",
  "email": "a@example.com",
  "wrapper_pid": $$,
  "child_pid": 0,
  "status": "running"
}
JSON
if CODEX_HOME="$wrapper_home" ./bin/codex-smooth-mode --enable >"$sandbox/smooth.out" 2>"$sandbox/smooth.err"; then
  echo "smooth mode unexpectedly enabled with live tabs" >&2
  exit 1
fi
grep -q "Refusing to enable smooth mode" "$sandbox/smooth.err"

cat >"$wrapper_home/codex-hotswap.json" <<'JSON'
{
  "switch_default": true,
  "refresh_codex_auth_usage": false
}
JSON
cat >"$wrapper_home/predictive_quota_walls.json" <<JSON
{
  "written_at": $(python3 - <<'PY'
import time
print(time.time())
PY
),
  "accounts": {
    "a@example.com": {
      "email": "a@example.com",
      "windows": {
        "weekly": {
          "remaining_percent": 0,
          "resets_at": $(python3 - <<'PY'
import time
print(time.time() + 3600)
PY
)
        }
      }
    }
  }
}
JSON
if CODEX_HOME="$wrapper_home" ./bin/codex-rescue >"$sandbox/rescue.out"; then
  echo "rescue unexpectedly returned success for stuck tab report" >&2
  exit 1
fi
grep -q "live-test" "$sandbox/rescue.out"
grep -q "no cmux metadata" "$sandbox/rescue.out"

cmux_home="$sandbox/cmux-home"
mkdir -p "$cmux_home/accounts" "$cmux_home/tabs/cmux-tab"
cat >"$cmux_home/accounts/registry.json" <<'JSON'
{
  "accounts": {
    "a": {
      "email": "a@example.com",
      "auth_path": "a.auth.json",
      "usage": {
        "5h": {"used_percent": 100},
        "weekly": {"used_percent": 100}
      }
    }
  }
}
JSON
printf '{}\n' >"$cmux_home/accounts/a.auth.json"
cat >"$cmux_home/tabs/cmux-tab/tab.json" <<JSON
{
  "tab_id": "cmux-tab",
  "email": "a@example.com",
  "wrapper_pid": $$,
  "child_pid": 0,
  "status": "running",
  "cmux": {
    "workspace_id": "workspace-1",
    "panel_id": "pane-1",
    "tab_id": "surface-1",
    "surface_id": "surface-1"
  }
}
JSON
cat >"$cmux_home/predictive_quota_walls.json" <<JSON
{
  "written_at": $(python3 - <<'PY'
import time
print(time.time())
PY
),
  "accounts": {
    "a@example.com": {
      "email": "a@example.com",
      "windows": {
        "weekly": {
          "remaining_percent": 0,
          "resets_at": $(python3 - <<'PY'
import time
print(time.time() + 3600)
PY
)
        }
      }
    }
  }
}
JSON
cmux_log="$sandbox/cmux.log"
PATH="$repo/tests/fakes:$PATH" CODEX_HOME="$cmux_home" FAKE_CMUX_LOG="$cmux_log" FAKE_CMUX_MODE=valid FAKE_CMUX_SCREEN='$ ' ./bin/codex-rescue --apply --yes >"$sandbox/cmux-rescue.out"
grep -q "sent recovery command" "$sandbox/cmux-rescue.out"
grep -q "codex-continue --tab-home" "$cmux_log"
grep -q "send_key=.*Enter" "$cmux_log"

: >"$cmux_log"
if PATH="$repo/tests/fakes:$PATH" CODEX_HOME="$cmux_home" FAKE_CMUX_LOG="$cmux_log" FAKE_CMUX_MODE=stale ./bin/codex-rescue --apply --yes >"$sandbox/cmux-stale.out" 2>"$sandbox/cmux-stale.err"; then
  echo "rescue unexpectedly succeeded for stale cmux surface" >&2
  exit 1
fi
grep -q "stale or missing" "$sandbox/cmux-stale.out"
test ! "$(grep -c 'send_text=' "$cmux_log" 2>/dev/null || true)" -gt 0

: >"$cmux_log"
if PATH="$repo/tests/fakes:$PATH" CODEX_HOME="$cmux_home" FAKE_CMUX_LOG="$cmux_log" FAKE_CMUX_MODE=nonterminal ./bin/codex-rescue --apply --yes >"$sandbox/cmux-nonterminal.out" 2>"$sandbox/cmux-nonterminal.err"; then
  echo "rescue unexpectedly succeeded for nonterminal cmux surface" >&2
  exit 1
fi
grep -q "not a terminal" "$sandbox/cmux-nonterminal.out"
test ! "$(grep -c 'send_text=' "$cmux_log" 2>/dev/null || true)" -gt 0

auth_log="$sandbox/codex-auth.log"
PATH="$repo/tests/fakes:$PATH" CODEX_HOME="$wrapper_home" FAKE_CODEX_AUTH_LOG="$auth_log" FAKE_CODEX_AUTH_FAIL_IF_CALLED=1 ./bin/codex-predictive-daemon --once
test ! -e "$auth_log"

tolerance_home="$sandbox/tolerance-home"
mkdir -p "$tolerance_home/accounts"
cat >"$tolerance_home/accounts/registry.json" <<'JSON'
{
  "accounts": {
    "a": {
      "email": "a@example.com",
      "auth_path": "a.auth.json",
      "usage": {
        "5h": {"used_percent": 20},
        "weekly": {"used_percent": 20}
      }
    },
    "b": {
      "email": "b@example.com",
      "auth_path": "b.auth.json",
      "usage": {
        "5h": {"used_percent": 17},
        "weekly": {"used_percent": 17}
      }
    }
  }
}
JSON
printf '{}\n' >"$tolerance_home/accounts/a.auth.json"
printf '{}\n' >"$tolerance_home/accounts/b.auth.json"
cat >"$tolerance_home/codex-hotswap.json" <<'JSON'
{
  "switch_default": true,
  "refresh_codex_auth_usage": false,
  "load_balance_tolerance_pct": 15
}
JSON
cat >"$tolerance_home/predictive_state.json" <<'JSON'
{
  "written_at": 1,
  "selected_default_email": "a@example.com"
}
JSON
tolerance_auth_log="$sandbox/tolerance-codex-auth.log"
PATH="$repo/tests/fakes:$PATH" CODEX_HOME="$tolerance_home" FAKE_CODEX_AUTH_LOG="$tolerance_auth_log" FAKE_CODEX_AUTH_FAIL_IF_CALLED=1 ./bin/codex-predictive-daemon --once
test ! -e "$tolerance_auth_log"
grep -q '"global_switch_reason": "within-tolerance"' "$tolerance_home/predictive_state.json"
grep -q '"selected_default_email": "a@example.com"' "$tolerance_home/predictive_state.json"

status_home="$sandbox/status-home"
mkdir -p "$status_home/accounts/recover"
cat >"$status_home/accounts/registry.json" <<'JSON'
{
  "accounts": {
    "low": {
      "email": "low@example.com",
      "auth_path": "low.auth.json",
      "usage": {
        "5h": {"used_percent": 90},
        "weekly": {"used_percent": 10}
      }
    },
    "unknown": {
      "email": "unknown@example.com",
      "auth_path": "unknown.auth.json"
    },
    "broken": {
      "email": "broken@example.com",
      "auth_path": "broken.auth.json",
      "usage": {
        "5h": {"used_percent": 10},
        "weekly": {"used_percent": 10}
      }
    },
    "missing": {
      "email": "missing@example.com",
      "auth_path": "missing.auth.json",
      "usage": {
        "5h": {"used_percent": 10},
        "weekly": {"used_percent": 10}
      }
    },
    "walled": {
      "email": "walled@example.com",
      "auth_path": "walled.auth.json",
      "usage": {
        "5h": {"used_percent": 100},
        "weekly": {"used_percent": 100}
      }
    }
  }
}
JSON
for account in low unknown broken walled; do
  printf '{}\n' >"$status_home/accounts/$account.auth.json"
done
printf 'broken@example.com\t1\n' >"$status_home/accounts/recover/broken.tsv"
cat >"$status_home/predictive_state.json" <<'JSON'
{
  "written_at": 1,
  "global_switch_reason": "within-tolerance",
  "selected_default_email": "low@example.com"
}
JSON
cat >"$status_home/predictive_quota_walls.json" <<JSON
{
  "written_at": $(python3 - <<'PY'
import time
print(time.time())
PY
),
  "accounts": {
    "walled@example.com": {
      "email": "walled@example.com",
      "windows": {
        "weekly": {
          "remaining_percent": 0,
          "resets_at": $(python3 - <<'PY'
import time
print(time.time() + 3600)
PY
)
        }
      }
    }
  }
}
JSON
CODEX_HOME="$status_home" ./bin/codex-status >"$sandbox/status.out"
grep -q "daemon: stale" "$sandbox/status.out"
grep -q "wall-cache: fresh" "$sandbox/status.out"
grep -q "low@example.com.*low" "$sandbox/status.out"
grep -q "unknown@example.com.*unknown" "$sandbox/status.out"
grep -q "broken@example.com.*broken" "$sandbox/status.out"
grep -q "missing@example.com.*missing-vault" "$sandbox/status.out"
grep -q "walled@example.com.*walled" "$sandbox/status.out"

migrate_home="$sandbox/migrate-home"
mkdir -p "$migrate_home/accounts" "$migrate_home/sessions"
cat >"$migrate_home/accounts/registry.json" <<'JSON'
{
  "accounts": {
    "a": {
      "email": "a@example.com",
      "auth_path": "a.auth.json",
      "usage": {
        "5h": {"used_percent": 10},
        "weekly": {"used_percent": 10}
      }
    },
    "b": {
      "email": "b@example.com",
      "auth_path": "b.auth.json",
      "usage": {
        "5h": {"used_percent": 20},
        "weekly": {"used_percent": 20}
      }
    }
  }
}
JSON
printf '{}\n' >"$migrate_home/accounts/a.auth.json"
printf '{}\n' >"$migrate_home/accounts/b.auth.json"
cat >"$migrate_home/codex-hotswap.json" <<'JSON'
{
  "quota_wall_max_age_seconds": 300,
  "live_migrate_idle_seconds": 1,
  "max_live_migrations_per_tab": 2
}
JSON
migrate_log="$sandbox/migrate-fake-codex.log"
PATH="$repo/tests/fakes:$PATH" \
  CODEX_HOME="$migrate_home" \
  CODEX_HOTSWAP_KEEP_TABS=1 \
  FAKE_CODEX_LOG="$migrate_log" \
  FAKE_CODEX_SLEEP=6 \
  FAKE_CODEX_MIGRATED_SLEEP=0 \
  FAKE_CODEX_WRITE_ROLLOUT=1 \
  ./bin/codex-safe "hello" >"$sandbox/migrate.out" &
migrate_pid=$!
sleep 3
cat >"$migrate_home/predictive_quota_walls.json" <<JSON
{
  "written_at": $(python3 - <<'PY'
import time
print(time.time())
PY
),
  "accounts": {
    "a@example.com": {
      "email": "a@example.com",
      "windows": {
        "weekly": {
          "remaining_percent": 0,
          "resets_at": $(python3 - <<'PY'
import time
print(time.time() + 3600)
PY
)
        }
      }
    }
  }
}
JSON
wait "$migrate_pid"
test "$(grep -c '^home=' "$migrate_log")" -eq 2
first_migrate_home="$(awk -F= '/^home=/{print $2; exit}' "$migrate_log")"
second_migrate_home="$(awk -F= '/^home=/{count++; if (count == 2) {print $2; exit}}' "$migrate_log")"
grep -q '"email": "a@example.com"' "$first_migrate_home/tab.json"
grep -q '"email": "b@example.com"' "$second_migrate_home/tab.json"
grep -q "automatically migrated" "$migrate_log"

HOME="$sandbox/home" CODEX_HOME="$sandbox/home/codex" PREFIX="$sandbox/home/bin" ./install.sh --dry-run >"$sandbox/dry-run.out"
test ! -e "$sandbox/home/bin/codex-safe"
test ! -e "$sandbox/home/Library/LaunchAgents"

HOME="$sandbox/home" CODEX_HOME="$sandbox/home/codex" PREFIX="$sandbox/home/bin" ./install.sh >"$sandbox/install.out"
test -x "$sandbox/home/bin/codex-safe"
test -x "$sandbox/home/bin/codex-continue"
test -x "$sandbox/home/bin/codex-rescue"
test -x "$sandbox/home/bin/codex-status"
test -x "$sandbox/home/bin/codex-validate"
test -f "$sandbox/home/codex/codex-hotswap.json"
test ! -e "$sandbox/home/.zshrc"
test ! -e "$sandbox/home/Library/LaunchAgents"
printf '{"custom": true}\n' >"$sandbox/home/codex/codex-hotswap.json"
HOME="$sandbox/home" CODEX_HOME="$sandbox/home/codex" PREFIX="$sandbox/home/bin" ./install.sh >"$sandbox/install-again.out"
grep -q '"custom": true' "$sandbox/home/codex/codex-hotswap.json"
HOME="$sandbox/home" CODEX_HOME="$sandbox/home/codex" PREFIX="$sandbox/home/bin" ./install.sh --with-alias >"$sandbox/install-alias.out"
grep -q "alias codex='codex-safe'" "$sandbox/home/.zshrc"
HOME="$sandbox/home" CODEX_HOME="$sandbox/home/codex" PREFIX="$sandbox/home/bin" ./install.sh --with-alias >"$sandbox/install-alias-again.out"
test "$(grep -c "alias codex='codex-safe'" "$sandbox/home/.zshrc")" -eq 1

plist="$sandbox/rendered.plist"
HOME="$sandbox/home" CODEX_HOME="$sandbox/home/codex" PREFIX="$sandbox/home/bin" CODEX_HOTSWAP_LAUNCHD_LABEL="dev.codex-hot-swap.test" ./install.sh --render-launchd-plist "$plist" >"$sandbox/render-plist.out"
test -f "$plist"
grep -q "<string>dev.codex-hot-swap.test</string>" "$plist"
grep -q "<string>$sandbox/home/bin/codex-predictive-daemon</string>" "$plist"
grep -q "<string>$sandbox/home/codex</string>" "$plist"
grep -q "$sandbox/home/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin" "$plist"

dry_plist="$sandbox/dry-rendered.plist"
HOME="$sandbox/home" CODEX_HOME="$sandbox/home/codex" PREFIX="$sandbox/home/bin" ./install.sh --dry-run --render-launchd-plist "$dry_plist" >"$sandbox/dry-render-plist.out"
test ! -e "$dry_plist"
grep -q "dry-run: would render launchd plist" "$sandbox/dry-render-plist.out"

echo "all tests passed"

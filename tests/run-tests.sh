#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
sandbox="$(mktemp -d)"
trap 'rm -rf "$sandbox"' EXIT

cd "$repo"

python3 -m py_compile bin/codex_hot_swap_lib.py bin/codex-status bin/codex-predictive-daemon bin/codex-safe bin/codex-smooth-mode
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
PATH="$repo/tests/fakes:$PATH" CODEX_HOME="$wrapper_home" CODEX_HOTSWAP_KEEP_TABS=1 FAKE_CODEX_LOG="$fake_log" ./bin/codex-safe "hello" >/dev/null
wrapped_home="$(awk -F= '/^home=/{print $2; exit}' "$fake_log")"
case "$wrapped_home" in
  "$wrapper_home"/tabs/*) ;;
  *) echo "expected per-tab CODEX_HOME, got $wrapped_home" >&2; exit 1 ;;
esac
test -f "$wrapped_home/auth.json"

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

rm -f "$wrapper_home/predictive_quota_walls.json"
rm -f "$wrapper_home/accounts/recover/broken.tsv"
: >"$fake_log"
PATH="$repo/tests/fakes:$PATH" CODEX_HOME="$wrapper_home" CODEX_HOTSWAP_KEEP_TABS=1 FAKE_CODEX_LOG="$fake_log" FAKE_CODEX_OUTPUT="You've hit your usage limit" ./bin/codex-safe "hello" >/dev/null
test ! -e "$wrapper_home/accounts/recover/broken.tsv"

HOME="$sandbox/home" CODEX_HOME="$sandbox/home/codex" PREFIX="$sandbox/home/bin" ./install.sh --dry-run >"$sandbox/dry-run.out"
test ! -e "$sandbox/home/bin/codex-safe"

HOME="$sandbox/home" CODEX_HOME="$sandbox/home/codex" PREFIX="$sandbox/home/bin" ./install.sh >"$sandbox/install.out"
test -x "$sandbox/home/bin/codex-safe"
test -x "$sandbox/home/bin/codex-status"
test -f "$sandbox/home/codex/codex-hotswap.json"
test ! -e "$sandbox/home/.zshrc"

echo "all tests passed"

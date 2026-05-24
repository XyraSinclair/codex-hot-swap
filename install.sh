#!/usr/bin/env bash
set -euo pipefail
shopt -s lastpipe 2>/dev/null || true

usage() {
  cat <<'USAGE'
Codex Hot Swap installer

Usage:
  ./install.sh [--dry-run] [--prefix DIR] [--codex-home DIR]
  ./install.sh --with-daemon [--with-alias]
  ./install.sh --render-launchd-plist PATH
  ./install.sh --uninstall [--purge-config] [--dry-run]

Options:
  --dry-run        Show intended actions without changing files.
  --prefix DIR     Installation directory for scripts. Default: $HOME/bin.
  --codex-home DIR Codex home for config/launchd. Default: global Codex home.
  --with-daemon    Install and start a macOS LaunchAgent for the daemon.
  --render-launchd-plist PATH
                   Render the daemon plist to PATH without bootstrapping launchd.
  --with-alias     Add alias codex='codex-safe' to ~/.zshrc if missing.
  --uninstall      Remove only files this installer can prove it owns.
  --purge-config   With --uninstall, also remove codex-hotswap.json.
  -h, --help       Show this help.

Default behavior:
  - installs scripts into the prefix;
  - refuses to overwrite existing unmanaged files;
  - creates a conservative config if missing;
  - does not copy credentials;
  - does not edit shell startup files;
  - does not start launchd unless --with-daemon is passed.
USAGE
}

log() {
  printf '%s\n' "$*"
}

warn() {
  printf 'warning: %s\n' "$*" >&2
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

run() {
  if [ "$dry_run" -eq 1 ]; then
    printf 'dry-run:'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

write_file() {
  local path="$1"
  local mode="$2"
  local content="$3"
  if [ "$dry_run" -eq 1 ]; then
    log "dry-run: would write $path"
    return 0
  fi
  mkdir -p "$(dirname "$path")"
  local old_umask
  old_umask="$(umask)"
  umask 077
  printf '%s\n' "$content" > "$path"
  umask "$old_umask"
  chmod "$mode" "$path"
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

dry_run=0
prefix="${PREFIX:-$HOME/bin}"
codex_home="${CODEX_GLOBAL_HOME:-${CODEX_HOME:-$HOME/.codex}}"
if [ "$(basename "$(dirname "$codex_home")")" = "tabs" ]; then
  codex_home="$(dirname "$(dirname "$codex_home")")"
fi
with_daemon=0
with_alias=0
uninstall=0
purge_config=0
render_launchd_plist=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      dry_run=1
      ;;
    --prefix)
      shift
      [ "$#" -gt 0 ] || { echo "missing value for --prefix" >&2; exit 2; }
      prefix="$1"
      ;;
    --codex-home)
      shift
      [ "$#" -gt 0 ] || { echo "missing value for --codex-home" >&2; exit 2; }
      codex_home="$1"
      ;;
    --with-daemon)
      with_daemon=1
      ;;
    --render-launchd-plist)
      shift
      [ "$#" -gt 0 ] || { echo "missing value for --render-launchd-plist" >&2; exit 2; }
      render_launchd_plist="$1"
      ;;
    --with-alias|--with-aliases)
      with_alias=1
      ;;
    --uninstall)
      uninstall=1
      ;;
    --purge-config)
      purge_config=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
launchd_label="${CODEX_HOTSWAP_LAUNCHD_LABEL:-dev.codex-hot-swap.predictive}"
plist_template="$script_dir/launchd/codex-hot-swap.plist.template"
plist_path="$HOME/Library/LaunchAgents/${launchd_label}.plist"
config_path="$codex_home/codex-hotswap.json"
manifest_path="$codex_home/codex-hotswap-install-manifest.json"
launchd_path="$prefix:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
python_bin="${PYTHON:-}"
scripts=(
  codex-safe
  codex-continue
  codex-status
  codex-predictive-daemon
  codex-rescue
  codex-smooth-mode
  codex-validate
  codex_hot_swap_lib.py
)

if [ "$python_bin" = "" ]; then
  if [ -x /usr/bin/python3 ]; then
    python_bin="/usr/bin/python3"
  else
    python_bin="python3"
  fi
fi

manifest_hash_for() {
  local path="$1"
  [ -f "$manifest_path" ] || return 1
  "$python_bin" - "$manifest_path" "$path" <<'PY'
import json
import sys
from pathlib import Path

manifest = Path(sys.argv[1])
target = sys.argv[2]
try:
    data = json.loads(manifest.read_text(encoding="utf-8"))
except Exception:
    sys.exit(1)
for entry in data.get("files", []):
    if entry.get("path") == target and entry.get("sha256"):
        print(entry["sha256"])
        sys.exit(0)
sys.exit(1)
PY
}

write_manifest() {
  "$python_bin" - "$manifest_path" "$prefix" "$codex_home" "$launchd_label" "$config_path" "$plist_path" "${scripts[@]}" <<'PY'
import hashlib
import json
import os
import time
import sys
from pathlib import Path

manifest_path, prefix, codex_home, label, config_path, plist_path, *scripts = sys.argv[1:]

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

files = []
for script in scripts:
    path = os.path.join(prefix, script)
    if os.path.exists(path):
        files.append({
            "name": script,
            "path": path,
            "sha256": sha256(path),
        })

data = {
    "version": 1,
    "installed_at": time.time(),
    "prefix": prefix,
    "codex_home": codex_home,
    "launchd_label": label,
    "config_path": config_path,
    "plist_path": plist_path,
    "alias_block": "\n# Codex Hot Swap\nalias codex='codex-safe'\n",
    "files": files,
}

path = Path(manifest_path)
path.parent.mkdir(parents=True, exist_ok=True)
tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY
}

check_install_collisions() {
  local conflicts=0
  local script source target target_hash source_hash managed_hash

  for script in "${scripts[@]}"; do
    source="$script_dir/bin/$script"
    target="$prefix/$script"

    [ -e "$target" ] || continue

    if [ -L "$target" ]; then
      warn "$target is a symlink; refusing to replace it"
      conflicts=1
      continue
    fi

    if [ ! -f "$target" ]; then
      warn "$target exists and is not a regular file; refusing to replace it"
      conflicts=1
      continue
    fi

    source_hash="$(sha256_file "$source")"
    target_hash="$(sha256_file "$target")"
    managed_hash=""
    if managed_hash="$(manifest_hash_for "$target" 2>/dev/null)"; then
      :
    else
      managed_hash=""
    fi

    if [ "$target_hash" = "$source_hash" ]; then
      continue
    fi

    if [ "$managed_hash" != "" ] && [ "$target_hash" = "$managed_hash" ]; then
      continue
    fi

    warn "$target exists and is not owned by this installer"
    conflicts=1
  done

  if [ "$conflicts" -ne 0 ]; then
    cat >&2 <<EOF

Refusing to overwrite existing files.

Use a side-by-side prefix, for example:
  ./install.sh --prefix "\$HOME/.local/codex-hot-swap/bin"

Or uninstall/move the existing files yourself after confirming no live chats
depend on them.
EOF
    exit 1
  fi
}

render_plist() {
  local target="$1"
  if [ ! -f "$plist_template" ]; then
    echo "missing launchd template: $plist_template" >&2
    exit 1
  fi
  if [ "$dry_run" -eq 1 ]; then
    log "dry-run: would render launchd plist to $target"
    return 0
  fi
  mkdir -p "$(dirname "$target")"
  "$python_bin" - "$plist_template" "$target" "$launchd_label" "$prefix" "$codex_home" "$launchd_path" <<'PY'
import sys
from pathlib import Path

template, target, label, prefix, codex_home, path_value = sys.argv[1:]
text = Path(template).read_text(encoding="utf-8")
for needle, value in {
    "__LABEL__": label,
    "__PREFIX__": prefix,
    "__CODEX_HOME__": codex_home,
    "__PATH__": path_value,
}.items():
    text = text.replace(needle, value)
Path(target).write_text(text, encoding="utf-8")
PY
  chmod 644 "$target"
}

remove_public_alias_block() {
  local zshrc="$HOME/.zshrc"
  [ -f "$zshrc" ] || return 0
  if ! grep -q "# Codex Hot Swap" "$zshrc"; then
    return 0
  fi
  if [ "$dry_run" -eq 1 ]; then
    log "dry-run: would remove Codex Hot Swap alias block from $zshrc"
    return 0
  fi
  "$python_bin" - "$zshrc" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
block = "\n# Codex Hot Swap\nalias codex='codex-safe'\n"
text = text.replace(block, "\n")
path.write_text(text, encoding="utf-8")
PY
}

remove_launchd_plist() {
  [ -e "$plist_path" ] || return 0
  if [ "$dry_run" -eq 1 ]; then
    log "dry-run: would launchctl bootout gui/$(id -u) $plist_path"
    log "dry-run: would remove $plist_path"
    return 0
  fi
  if [ "$(uname -s)" = "Darwin" ]; then
    launchctl bootout "gui/$(id -u)" "$plist_path" >/dev/null 2>&1 || true
  fi
  rm -f "$plist_path"
}

uninstall_installation() {
  local script target target_hash source_hash managed_hash removed_any=0

  echo "Codex Hot Swap uninstaller"
  echo "prefix: $prefix"
  echo "CODEX_HOME: $codex_home"

  for script in "${scripts[@]}"; do
    target="$prefix/$script"
    [ -e "$target" ] || continue

    if [ -L "$target" ] || [ ! -f "$target" ]; then
      warn "leaving unmanaged path in place: $target"
      continue
    fi

    target_hash="$(sha256_file "$target")"
    managed_hash=""
    if managed_hash="$(manifest_hash_for "$target" 2>/dev/null)"; then
      :
    else
      managed_hash=""
    fi

    source_hash=""
    if [ -f "$script_dir/bin/$script" ]; then
      source_hash="$(sha256_file "$script_dir/bin/$script")"
    fi

    if [ "$managed_hash" != "" ] && [ "$target_hash" = "$managed_hash" ]; then
      run rm -f "$target"
      removed_any=1
      continue
    fi

    if [ "$managed_hash" = "" ] && [ "$source_hash" != "" ] && [ "$target_hash" = "$source_hash" ]; then
      run rm -f "$target"
      removed_any=1
      continue
    fi

    warn "leaving unmanaged or modified file in place: $target"
  done

  remove_public_alias_block
  remove_launchd_plist

  if [ "$purge_config" -eq 1 ]; then
    [ ! -e "$config_path" ] || run rm -f "$config_path"
  elif [ -e "$config_path" ]; then
    log "keeping config: $config_path"
  fi

  [ ! -e "$manifest_path" ] || run rm -f "$manifest_path"

  if [ "$removed_any" -eq 0 ]; then
    log "no managed scripts were removed"
  fi
  log "uninstall complete"
}

if [ "$purge_config" -eq 1 ] && [ "$uninstall" -ne 1 ]; then
  die "--purge-config requires --uninstall"
fi

if [ "$uninstall" -eq 1 ]; then
  if [ "$with_daemon" -eq 1 ] || [ "$render_launchd_plist" != "" ]; then
    die "--uninstall cannot be combined with install or render options"
  fi
  uninstall_installation
  exit 0
fi

echo "Codex Hot Swap installer"
echo "prefix: $prefix"
echo "CODEX_HOME: $codex_home"

check_install_collisions

run mkdir -p "$prefix"
run mkdir -p "$codex_home"

for script in "${scripts[@]}"; do
  run install -m 0755 "$script_dir/bin/$script" "$prefix/$script"
done

if [ "$dry_run" -eq 1 ]; then
  log "dry-run: would write install manifest to $manifest_path"
else
  write_manifest
fi

if [ ! -e "$config_path" ]; then
  write_file "$config_path" 600 '{
  "threshold_5h_percent": 25,
  "threshold_weekly_percent": 15,
  "poll_interval_seconds": 60,
  "quota_wall_max_age_seconds": 300,
  "live_migrate_below_5h_percent": 0,
  "live_migrate_below_weekly_percent": 0,
  "live_migrate_idle_seconds": 2,
  "switch_default": false,
  "refresh_codex_auth_usage": false,
  "notify": true
}'
else
  log "keeping existing config: $config_path"
fi

if [ "$with_alias" -eq 1 ]; then
  zshrc="$HOME/.zshrc"
  if [ "$dry_run" -eq 1 ]; then
    log "dry-run: would ensure alias in $zshrc"
  else
    touch "$zshrc"
    if ! grep -q "alias codex='codex-safe'" "$zshrc"; then
      printf "\n# Codex Hot Swap\nalias codex='codex-safe'\n" >> "$zshrc"
      log "added alias to $zshrc"
    else
      log "alias already present in $zshrc"
    fi
  fi
fi

if [ "$render_launchd_plist" != "" ]; then
  render_plist "$render_launchd_plist"
fi

if [ "$with_daemon" -eq 1 ]; then
  if [ "$(uname -s)" != "Darwin" ]; then
    echo "--with-daemon currently supports macOS launchd only" >&2
    exit 2
  fi
  if [ "$dry_run" -eq 1 ]; then
    log "dry-run: would write $plist_path"
    log "dry-run: would launchctl bootstrap gui/$(id -u) $plist_path"
  else
    render_plist "$plist_path"
    launchctl bootout "gui/$(id -u)" "$plist_path" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$(id -u)" "$plist_path"
    launchctl kickstart -k "gui/$(id -u)/$launchd_label" || true
    log "started launchd job: $launchd_label"
  fi
fi

cat <<'SUMMARY'

Install complete.

Next:
  codex-status
  codex-smooth-mode --enable   # optional; enables usage refresh polling

Uninstall:
  ./install.sh --uninstall

Uninstall keeps codex-hotswap.json by default and never removes accounts,
credentials, tab homes, or rollout logs. Use --purge-config to remove only this
tool's config file too.
SUMMARY

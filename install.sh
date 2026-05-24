#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Codex Hot Swap installer

Usage:
  ./install.sh [--dry-run] [--prefix DIR] [--codex-home DIR]
  ./install.sh --with-daemon [--with-alias]

Options:
  --dry-run        Show intended actions without changing files.
  --prefix DIR     Installation directory for scripts. Default: $HOME/bin.
  --codex-home DIR Codex home for config/launchd. Default: $CODEX_HOME or ~/.codex.
  --with-daemon    Install and start a macOS LaunchAgent for the daemon.
  --with-alias     Add alias codex='codex-safe' to ~/.zshrc if missing.
  -h, --help       Show this help.

Default behavior:
  - installs scripts into the prefix;
  - creates a conservative config if missing;
  - does not copy credentials;
  - does not edit shell startup files;
  - does not start launchd unless --with-daemon is passed.
USAGE
}

log() {
  printf '%s\n' "$*"
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
  umask 077
  printf '%s\n' "$content" > "$path"
  chmod "$mode" "$path"
}

dry_run=0
prefix="${PREFIX:-$HOME/bin}"
codex_home="${CODEX_HOME:-$HOME/.codex}"
with_daemon=0
with_alias=0

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
    --with-alias|--with-aliases)
      with_alias=1
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

echo "Codex Hot Swap installer"
echo "prefix: $prefix"
echo "CODEX_HOME: $codex_home"

run mkdir -p "$prefix"
run mkdir -p "$codex_home"

for script in \
  codex-safe \
  codex-status \
  codex-predictive-daemon \
  codex-smooth-mode \
  codex_hot_swap_lib.py
do
  run install -m 0755 "$script_dir/bin/$script" "$prefix/$script"
done

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

if [ "$with_daemon" -eq 1 ]; then
  if [ "$(uname -s)" != "Darwin" ]; then
    echo "--with-daemon currently supports macOS launchd only" >&2
    exit 2
  fi
  if [ ! -f "$plist_template" ]; then
    echo "missing launchd template: $plist_template" >&2
    exit 1
  fi
  if [ "$dry_run" -eq 1 ]; then
    log "dry-run: would write $plist_path"
    log "dry-run: would launchctl bootstrap gui/$(id -u) $plist_path"
  else
    mkdir -p "$(dirname "$plist_path")"
    sed \
      -e "s#__LABEL__#$launchd_label#g" \
      -e "s#__PREFIX__#$prefix#g" \
      -e "s#__CODEX_HOME__#$codex_home#g" \
      -e "s#__PATH__#$prefix:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin#g" \
      "$plist_template" > "$plist_path"
    chmod 644 "$plist_path"
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
  remove installed scripts from the prefix;
  remove the optional alias from ~/.zshrc;
  if launchd was enabled, run:
    launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/dev.codex-hot-swap.predictive.plist
SUMMARY

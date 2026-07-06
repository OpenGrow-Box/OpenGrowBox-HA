#!/bin/sh
# OpenGrowBox installer.
#
# Detects the Home Assistant installation type and version, checks
# compatibility against this integration's minimum required HA version
# (hacs.json -> "homeassistant"), and copies the integration into the
# right custom_components directory.
#
# Works without HACS: run it inside a git checkout of this repo, or as a
# one-liner against a released version:
#
#   curl -fsSL https://raw.githubusercontent.com/OpenGrow-Box/OpenGrowBox-HA/main/scripts/install.sh | sh
#
# POSIX sh only (no bashisms) so it runs on HAOS/BusyBox, Supervised,
# Container and Core installs alike.

set -eu

REPO="OpenGrow-Box/OpenGrowBox-HA"
HACS_JSON_URL="https://raw.githubusercontent.com/${REPO}/main/hacs.json"
RELEASES_LATEST_API="https://api.github.com/repos/${REPO}/releases/latest"
ASSET_NAME="opengrowbox.zip"

CONFIG_DIR_OVERRIDE=""
DO_RESTART=0
SKIP_VERSION_CHECK=0
CLEANUP_DIR=""

usage() {
  cat <<'EOF'
Usage: install.sh [--config-dir DIR] [--restart] [--skip-version-check]

  --config-dir DIR       Home Assistant config directory (default: auto-detect)
  --restart              Restart Home Assistant after install (HAOS/Supervised only)
  --skip-version-check   Install even if the HA version can't be determined
  -h, --help             Show this help
EOF
}

log() { printf '%s\n' "$*"; }
err() { printf 'Error: %s\n' "$*" >&2; }

cleanup() {
  if [ -n "$CLEANUP_DIR" ] && [ -d "$CLEANUP_DIR" ]; then
    rm -rf "$CLEANUP_DIR"
  fi
}
trap cleanup EXIT

while [ $# -gt 0 ]; do
  case "$1" in
    --config-dir)
      [ $# -ge 2 ] || { err "--config-dir requires a value"; exit 1; }
      CONFIG_DIR_OVERRIDE="$2"
      shift 2
      ;;
    --restart)
      DO_RESTART=1
      shift
      ;;
    --skip-version-check)
      SKIP_VERSION_CHECK=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      err "Unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

# --- locate this script's repo checkout, if any ---------------------------
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
if [ -d "$REPO_ROOT/custom_components/opengrowbox" ]; then
  LOCAL_CHECKOUT=1
else
  LOCAL_CHECKOUT=0
fi

# --- config directory -------------------------------------------------------
detect_config_dir() {
  if [ -n "$CONFIG_DIR_OVERRIDE" ]; then
    printf '%s' "$CONFIG_DIR_OVERRIDE"
    return
  fi
  if [ -n "${HA_CONFIG:-}" ] && [ -d "${HA_CONFIG:-}" ]; then
    printf '%s' "$HA_CONFIG"
    return
  fi
  if [ -d /config ]; then
    printf '%s' /config
    return
  fi
  if [ -d "$HOME/.homeassistant" ]; then
    printf '%s' "$HOME/.homeassistant"
    return
  fi
  printf ''
}

# --- installation type (informational + gates --restart) --------------------
detect_install_type() {
  if command -v ha >/dev/null 2>&1; then
    printf 'haos_or_supervised'
  elif [ -f /.dockerenv ]; then
    printf 'container'
  else
    printf 'core'
  fi
}

# --- currently running HA version -------------------------------------------
detect_ha_version() {
  cfg="$1"
  if [ -f "$cfg/.HA_VERSION" ]; then
    tr -d '[:space:]' < "$cfg/.HA_VERSION"
    return
  fi
  if command -v ha >/dev/null 2>&1; then
    ha core info -f json 2>/dev/null \
      | grep -o '"version"[[:space:]]*:[[:space:]]*"[^"]*"' \
      | head -n1 \
      | sed -E 's/.*"([^"]*)"$/\1/'
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import homeassistant.const as c; print(c.__version__)' 2>/dev/null || true
    return
  fi
  printf ''
}

# --- minimum HA version required by this integration ------------------------
extract_json_field() {
  # extract_json_field <field-name> <json-text>
  printf '%s' "$2" | grep -o "\"$1\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" | head -n1 | sed -E 's/.*"([^"]*)"$/\1/'
}

get_min_ha_version() {
  if [ "$LOCAL_CHECKOUT" -eq 1 ] && [ -f "$REPO_ROOT/hacs.json" ]; then
    content=$(cat "$REPO_ROOT/hacs.json")
  else
    content=$(curl -fsSL "$HACS_JSON_URL") || {
      err "Could not fetch hacs.json from GitHub to check compatibility."
      exit 1
    }
  fi
  extract_json_field "homeassistant" "$content"
}

# --- version comparison (dotted, numeric, e.g. 2025.12.5) -------------------
version_ge() {
  # returns success if $1 >= $2
  i=1
  while [ "$i" -le 6 ]; do
    p1=$(printf '%s' "$1" | cut -d. -f"$i")
    p2=$(printf '%s' "$2" | cut -d. -f"$i")
    if [ -z "$p1" ] && [ -z "$p2" ]; then
      return 0
    fi
    p1n=$(printf '%s' "${p1:-0}" | sed -E 's/[^0-9].*$//')
    p2n=$(printf '%s' "${p2:-0}" | sed -E 's/[^0-9].*$//')
    p1n=${p1n:-0}
    p2n=${p2n:-0}
    if [ "$p1n" -gt "$p2n" ]; then return 0; fi
    if [ "$p1n" -lt "$p2n" ]; then return 1; fi
    i=$((i + 1))
  done
  return 0
}

# --- extract a zip without assuming `unzip` is installed ---------------------
extract_zip() {
  zip_path="$1"
  dest="$2"
  if command -v unzip >/dev/null 2>&1; then
    unzip -q "$zip_path" -d "$dest"
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$zip_path" "$dest"
  else
    err "Neither 'unzip' nor 'python3' is available to extract the release archive."
    exit 1
  fi
}

# --- source of the integration files ----------------------------------------
prepare_source() {
  if [ "$LOCAL_CHECKOUT" -eq 1 ]; then
    SOURCE_DIR="$REPO_ROOT/custom_components/opengrowbox"
    return
  fi

  log "No local checkout found, downloading the latest release asset..."
  CLEANUP_DIR=$(mktemp -d)

  release_json=$(curl -fsSL "$RELEASES_LATEST_API") || {
    err "Could not reach GitHub releases API."
    exit 1
  }
  asset_url=$(printf '%s' "$release_json" \
    | grep -o "\"browser_download_url\"[[:space:]]*:[[:space:]]*\"[^\"]*${ASSET_NAME}\"" \
    | head -n1 \
    | sed -E 's/.*"(https:[^"]*)"$/\1/')

  if [ -z "$asset_url" ]; then
    err "Could not find a '${ASSET_NAME}' asset on the latest release."
    exit 1
  fi

  curl -fsSL "$asset_url" -o "$CLEANUP_DIR/${ASSET_NAME}"
  extract_zip "$CLEANUP_DIR/${ASSET_NAME}" "$CLEANUP_DIR/extracted"
  SOURCE_DIR="$CLEANUP_DIR/extracted/opengrowbox"

  if [ ! -d "$SOURCE_DIR" ]; then
    err "Release archive did not contain an 'opengrowbox' folder."
    exit 1
  fi
}

# --- copy into place ----------------------------------------------------------
install_integration() {
  target_parent="$CONFIG_DIR/custom_components"
  target="$target_parent/opengrowbox"

  mkdir -p "$target_parent"

  if [ -d "$target" ]; then
    log "Note: an existing installation was found at $target."
    log "If it is currently managed by HACS, remove it there first to avoid conflicts."
  fi

  staging="$target_parent/opengrowbox.new.$$"
  rm -rf "$staging"
  cp -r "$SOURCE_DIR" "$staging"

  if [ -d "$target" ]; then
    backup="$target_parent/opengrowbox.old.$$"
    rm -rf "$backup"
    mv "$target" "$backup"
    mv "$staging" "$target"
    rm -rf "$backup"
  else
    mv "$staging" "$target"
  fi

  log "Installed OpenGrowBox into $target"
}

# --- main --------------------------------------------------------------------
CONFIG_DIR=$(detect_config_dir)
if [ -z "$CONFIG_DIR" ]; then
  err "Could not determine the Home Assistant config directory. Pass --config-dir DIR."
  exit 1
fi
log "Home Assistant config directory: $CONFIG_DIR"

INSTALL_TYPE=$(detect_install_type)
log "Detected installation type: $INSTALL_TYPE"

HA_VERSION=$(detect_ha_version "$CONFIG_DIR")
MIN_VERSION=$(get_min_ha_version)
log "Required Home Assistant version: >= $MIN_VERSION"

if [ -z "$HA_VERSION" ]; then
  if [ "$SKIP_VERSION_CHECK" -eq 1 ]; then
    log "Warning: could not detect the installed Home Assistant version, continuing anyway (--skip-version-check)."
  else
    err "Could not detect the installed Home Assistant version."
    err "Re-run with --skip-version-check to install anyway, or --config-dir to point at the right folder."
    exit 1
  fi
else
  log "Detected Home Assistant version: $HA_VERSION"
  if ! version_ge "$HA_VERSION" "$MIN_VERSION"; then
    err "Home Assistant $HA_VERSION is older than the required $MIN_VERSION. Aborting."
    exit 1
  fi
fi

prepare_source
install_integration

log ""
log "Done. Restart Home Assistant to finish the installation."

if [ "$DO_RESTART" -eq 1 ]; then
  if command -v ha >/dev/null 2>&1; then
    log "Restarting Home Assistant (ha core restart)..."
    ha core restart
  else
    log "--restart was given, but the 'ha' CLI is not available on this install type (Core/Container)."
    log "Please restart Home Assistant manually."
  fi
fi

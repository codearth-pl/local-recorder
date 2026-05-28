#!/usr/bin/env bash
# One-command startup for local-recorder.
#
# Brings up the whole local stack: Python venv + deps (incl. WhisperX), the
# native-messaging host, your browser (where the unpacked extension lives), and
# the daemon in the foreground.
#
# Config can come from a .env file (copy .env.example -> .env), the cached
# .local-recorder.conf, or command-line flags. Precedence, highest first:
#   CLI flags  >  .env  >  .local-recorder.conf cache  >  defaults
#
# Usage:
#   ./start.sh                                  # use .env / cached config
#   ./start.sh --extension-id <ID> [--browser chrome|chromium|brave|edge]
#   ./start.sh --languages pl,en                # override languages for this run
#   ./start.sh --skip-setup                     # fast restart: skip env/deps/host install
#
# The extension ID can't be discovered automatically: load extension/ once via
# chrome://extensions (Developer mode -> Load unpacked) and copy the ID shown on
# its card. EXTENSION_ID + BROWSER are cached in .local-recorder.conf after the
# first successful run, so subsequent starts need no flags.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"
CONF_FILE="$REPO_DIR/.local-recorder.conf"
ENV_FILE="$REPO_DIR/.env"

usage() {
  cat <<'EOF'
Usage:
  ./start.sh                                  # use .env / cached config
  ./start.sh --extension-id <ID> [--browser chrome|chromium|brave|edge]
  ./start.sh --languages pl,en                # override languages for this run
  ./start.sh --skip-setup                     # fast restart: skip env/deps/host install

Config precedence (highest first): CLI flags > .env > .local-recorder.conf > defaults
EOF
}

# --- parse named flags -----------------------------------------------------
SKIP_SETUP=0
EXTENSION_ID_CLI=""
BROWSER_CLI=""
LANGUAGES_CLI=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --extension-id) EXTENSION_ID_CLI="${2:-}"; shift 2 ;;
    --extension-id=*) EXTENSION_ID_CLI="${1#*=}"; shift ;;
    --browser) BROWSER_CLI="${2:-}"; shift 2 ;;
    --browser=*) BROWSER_CLI="${1#*=}"; shift ;;
    --languages) LANGUAGES_CLI="${2:-}"; shift 2 ;;
    --languages=*) LANGUAGES_CLI="${1#*=}"; shift ;;
    --skip-setup) SKIP_SETUP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown argument '$1'" >&2; usage >&2; exit 1 ;;
  esac
done

# --- resolve config (CLI > .env > cache > defaults) ------------------------
EXTENSION_ID=""
BROWSER=""
LANGUAGES=""
if [[ -f "$CONF_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$CONF_FILE"
fi
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$ENV_FILE"
fi
[[ -n "$EXTENSION_ID_CLI" ]] && EXTENSION_ID="$EXTENSION_ID_CLI"
[[ -n "$BROWSER_CLI" ]] && BROWSER="$BROWSER_CLI"
[[ -n "$LANGUAGES_CLI" ]] && LANGUAGES="$LANGUAGES_CLI"
BROWSER="${BROWSER:-chrome}"

if [[ -z "$EXTENSION_ID" ]]; then
  cat >&2 <<EOF
error: no extension ID.

First, load the unpacked extension once and copy its ID:
  1. open chrome://extensions, enable Developer mode
  2. Load unpacked -> select $REPO_DIR/extension
  3. copy the ID shown on the extension's card

Then set it in .env (EXTENSION_ID=...) or pass it:
  ./start.sh --extension-id <EXTENSION_ID> [--browser chrome|chromium|brave|edge]
EOF
  exit 1
fi

case "$BROWSER" in
  chrome|chromium|brave|edge) ;;
  *) echo "error: unknown browser '$BROWSER' (use chrome|chromium|brave|edge)" >&2; exit 1 ;;
esac

# Validate languages here (before launching the browser) so a typo fails fast
# rather than letting the daemon die on its own argparse check at the very end.
if [[ -n "$LANGUAGES" ]] && ! [[ "$LANGUAGES" =~ ^[A-Za-z]{2}(,[A-Za-z]{2})*$ ]]; then
  echo "error: invalid LANGUAGES '$LANGUAGES' (expected comma-separated two-letter codes, e.g. pl,en)" >&2
  exit 1
fi

# Cache extension ID + browser for next time (languages stay in .env / --languages).
cat > "$CONF_FILE" <<EOF
EXTENSION_ID=$EXTENSION_ID
BROWSER=$BROWSER
EOF

# --- preflight -------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  echo "error: 'uv' not found on PATH (see https://docs.astral.sh/uv/)" >&2
  exit 1
fi
command -v ffmpeg >/dev/null 2>&1 || \
  echo "warning: ffmpeg not found - daemon will fall back to captions-only (no audio transcription)" >&2
command -v wpctl >/dev/null 2>&1 || \
  echo "warning: pipewire's wpctl not found - audio capture may be unavailable" >&2

# --- setup (skippable) -----------------------------------------------------
if [[ "$SKIP_SETUP" -eq 0 ]]; then
  if [[ ! -d "$REPO_DIR/.venv" ]]; then
    echo "==> creating venv (python 3.12)"
    uv venv --python 3.12
  fi
  echo "==> installing deps (incl. WhisperX)"
  uv sync --extra whisper

  echo "==> installing native-messaging host for extension $EXTENSION_ID ($BROWSER)"
  ./native-host/install.sh "$EXTENSION_ID" "$BROWSER"
fi

# --- launch browser (detached) ---------------------------------------------
case "$BROWSER" in
  chrome)   CANDIDATES=(google-chrome google-chrome-stable) ;;
  chromium) CANDIDATES=(chromium chromium-browser) ;;
  brave)    CANDIDATES=(brave-browser brave) ;;
  edge)     CANDIDATES=(microsoft-edge microsoft-edge-stable) ;;
esac
BROWSER_BIN=""
for c in "${CANDIDATES[@]}"; do
  if command -v "$c" >/dev/null 2>&1; then BROWSER_BIN="$c"; break; fi
done
if [[ -n "$BROWSER_BIN" ]]; then
  echo "==> launching $BROWSER_BIN"
  nohup "$BROWSER_BIN" >/dev/null 2>&1 &
  disown || true
else
  echo "warning: no $BROWSER binary found on PATH - open your browser manually" >&2
fi

# --- daemon (foreground) ---------------------------------------------------
echo "==> starting daemon (Ctrl-C to stop)"
if [[ -n "$LANGUAGES" ]]; then
  exec .venv/bin/python -m daemon.daemon --languages "$LANGUAGES"
else
  exec .venv/bin/python -m daemon.daemon
fi

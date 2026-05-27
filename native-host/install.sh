#!/usr/bin/env bash
# Install the native-messaging host manifest for local-recorder.
#
# Usage: ./install.sh <EXTENSION_ID> [browser]
#   EXTENSION_ID  the unpacked extension's ID (chrome://extensions, dev mode on)
#   browser       one of: chrome (default), chromium, brave, edge
set -euo pipefail

EXTENSION_ID="${1:-}"
BROWSER="${2:-chrome}"

if [[ -z "$EXTENSION_ID" ]]; then
  echo "usage: $0 <EXTENSION_ID> [chrome|chromium|brave|edge]" >&2
  echo "find the ID at chrome://extensions (Developer mode -> Load unpacked)" >&2
  exit 1
fi

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST_PATH="$HOST_DIR/host.py"
chmod +x "$HOST_PATH"

case "$BROWSER" in
  chrome)   TARGET="$HOME/.config/google-chrome/NativeMessagingHosts" ;;
  chromium) TARGET="$HOME/.config/chromium/NativeMessagingHosts" ;;
  brave)    TARGET="$HOME/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts" ;;
  edge)     TARGET="$HOME/.config/microsoft-edge/NativeMessagingHosts" ;;
  *) echo "unknown browser: $BROWSER" >&2; exit 1 ;;
esac

mkdir -p "$TARGET"
MANIFEST="$TARGET/com.localrecorder.host.json"

sed -e "s#__HOST_PATH__#$HOST_PATH#" \
    -e "s#__EXTENSION_ID__#$EXTENSION_ID#" \
    "$HOST_DIR/com.localrecorder.host.json.template" > "$MANIFEST"

echo "installed native messaging host:"
echo "  manifest: $MANIFEST"
echo "  host:     $HOST_PATH"
echo "  extension allowed: chrome-extension://$EXTENSION_ID/"

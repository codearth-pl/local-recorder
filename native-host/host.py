#!/usr/bin/env python3
"""Native-messaging host: a thin relay between the browser extension and the
local daemon.

Chrome launches this process and speaks its native-messaging protocol over
stdio: each message is a little-endian uint32 length prefix followed by UTF-8
JSON. We forward every message to the daemon's Unix socket as one JSON line.
Keeping this relay dumb means the daemon (which owns audio + transcription)
survives service-worker restarts independently of the browser.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import sys
import time
from pathlib import Path

SOCKET_PATH = Path(
    os.environ.get("LOCAL_RECORDER_SOCKET", "~/.local/share/local-recorder/daemon.sock")
).expanduser()


def read_message() -> dict | None:
    """Read one native message from stdin, or None at EOF."""
    raw_len = sys.stdin.buffer.read(4)
    if len(raw_len) < 4:
        return None
    (length,) = struct.unpack("=I", raw_len)
    data = sys.stdin.buffer.read(length)
    if len(data) < length:
        return None
    return json.loads(data.decode("utf-8"))


def send_native(obj: dict) -> None:
    """Send a native message back to the extension (used for acks/errors)."""
    encoded = json.dumps(obj).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("=I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def connect_daemon(retries: int = 3) -> socket.socket | None:
    for attempt in range(retries):
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(str(SOCKET_PATH))
            return sock
        except OSError:
            time.sleep(0.3 * (attempt + 1))
    return None


def main() -> None:
    daemon = connect_daemon()
    if daemon is None:
        send_native({"ok": False, "error": f"daemon not reachable at {SOCKET_PATH}"})
        return
    try:
        while True:
            msg = read_message()
            if msg is None:
                break
            line = (json.dumps(msg) + "\n").encode("utf-8")
            try:
                daemon.sendall(line)
            except (BrokenPipeError, OSError):
                daemon = connect_daemon()
                if daemon is None:
                    send_native({"ok": False, "error": "lost daemon connection"})
                    break
                daemon.sendall(line)
    finally:
        if daemon is not None:
            daemon.close()


if __name__ == "__main__":
    main()

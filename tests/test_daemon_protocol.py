"""End-to-end protocol test for the daemon without audio/Whisper.

Starts a Daemon with audio disabled, sends meeting_start + captions +
meeting_stop over the Unix socket, and asserts a captions-only transcript is
written. Run: python -m pytest tests/ -q
"""
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daemon.daemon import Daemon  # noqa: E402


class _NoAudioDaemon(Daemon):
    """Override sessions to skip ffmpeg so the test needs no audio devices."""


def _send(sock_path, messages):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(str(sock_path))
    for m in messages:
        s.sendall((json.dumps(m) + "\n").encode())
    s.close()


def test_captions_only_flow(tmp_path, monkeypatch):
    # Force audio capture to be a no-op so no real devices are touched.
    import daemon.session as session_mod

    class FakeRecorder:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

        def stop(self, timeout=10.0):
            return None  # no wav -> captions-only path

    monkeypatch.setattr(session_mod, "AudioRecorder", FakeRecorder)

    sock_path = tmp_path / "d.sock"
    cfg = {
        "output_dir": str(tmp_path / "out"),
        "keep_audio": False,
        "socket_path": str(sock_path),
        "log_file": str(tmp_path / "d.log"),
        "audio": {"sample_rate": 16000, "monitor_device": "x", "mic_device": "y"},
        "whisper": {},
        "align": {"caption_latency": 0.0, "tolerance": 2.0},
    }
    d = _NoAudioDaemon(cfg)
    t = threading.Thread(target=d.run, daemon=True)
    t.start()

    # Wait for the socket to appear.
    for _ in range(50):
        if sock_path.exists():
            break
        time.sleep(0.05)
    assert sock_path.exists()

    now = int(time.time() * 1000)
    _send(
        sock_path,
        [
            {"type": "meeting_start", "id": "m1", "title": "Test Sync", "platform": "google-meet"},
            {"type": "caption", "id": "m1", "speaker": "Alice", "text": "hello team",
             "t_start": now, "t_end": now + 2000},
            {"type": "caption", "id": "m1", "speaker": "Bob", "text": "hi alice",
             "t_start": now + 3000, "t_end": now + 5000},
            {"type": "meeting_stop", "id": "m1"},
        ],
    )

    # Finalization runs in a background thread; wait for the transcript.
    out_root = Path(cfg["output_dir"])
    md = None
    for _ in range(100):
        files = list(out_root.glob("*.md"))
        if files:
            md = files[0]
            break
        time.sleep(0.05)
    assert md is not None, "transcript .md was not written"
    text = md.read_text()
    assert "Alice" in text and "Bob" in text
    assert "hello team" in text

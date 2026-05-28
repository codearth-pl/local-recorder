"""Long-running local daemon.

Listens on a Unix socket for newline-delimited JSON messages relayed from the
browser extension (via native-host/host.py) and drives meeting sessions.

Message protocol (one JSON object per line):
    {"type": "meeting_start", "id": "<tab/meeting id>", "title": ..., "platform": ...,
     "url": ..., "participants": [...]}
    {"type": "caption", "id": ..., "speaker": ..., "text": ..., "t_start": <ms>, "t_end": <ms>}
    {"type": "meeting_stop", "id": ...}
    {"type": "ping"}

Finalization (transcription) runs in a background thread so the socket loop stays
responsive.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import socket
import threading
from pathlib import Path

from .config import load_config
from .session import Session

_LANG_CODE = re.compile(r"^[a-z]{2}$")


def _setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
    )


log = logging.getLogger("local-recorder.daemon")


class Daemon:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()
        self.socket_path = Path(cfg["socket_path"]).expanduser()

    # --- message handling -------------------------------------------------
    def handle(self, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype == "meeting_start":
            self._start(msg)
        elif mtype == "caption":
            self._caption(msg)
        elif mtype == "meeting_stop":
            self._stop(msg)
        elif mtype == "ping":
            pass
        else:
            log.warning("unknown message type: %r", mtype)

    def _start(self, msg: dict) -> None:
        mid = str(msg.get("id", "default"))
        with self.lock:
            if mid in self.sessions:
                log.info("session %s already active; ignoring duplicate start", mid)
                return
            log.info("meeting_start %s: %s", mid, msg.get("title"))
            self.sessions[mid] = Session(mid, msg, self.cfg)

    def _caption(self, msg: dict) -> None:
        mid = str(msg.get("id", "default"))
        with self.lock:
            session = self.sessions.get(mid)
        if session is None:
            # Caption arrived before start (or after stop); create a session so
            # we don't lose data.
            log.info("caption for unknown session %s; auto-starting", mid)
            self._start({**msg, "type": "meeting_start"})
            with self.lock:
                session = self.sessions.get(mid)
        if session is not None:
            session.add_caption(msg)

    def _stop(self, msg: dict) -> None:
        mid = str(msg.get("id", "default"))
        with self.lock:
            session = self.sessions.pop(mid, None)
        if session is None:
            log.info("meeting_stop for unknown session %s", mid)
            return
        log.info("meeting_stop %s", mid)
        # Finalize off the socket thread; transcription can take a while.
        threading.Thread(target=self._finalize, args=(session,), daemon=True).start()

    @staticmethod
    def _finalize(session: Session) -> None:
        try:
            session.finalize()
        except Exception:  # noqa: BLE001
            log.exception("finalize failed for session %s", session.meeting_id)

    # --- socket server ----------------------------------------------------
    def _serve_conn(self, conn: socket.socket) -> None:
        buf = b""
        with conn:
            while True:
                try:
                    chunk = conn.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        log.warning("bad JSON line dropped: %r", line[:200])
                        continue
                    try:
                        self.handle(msg)
                    except Exception:  # noqa: BLE001
                        log.exception("error handling message")

    def run(self) -> None:
        if self.socket_path.exists():
            self.socket_path.unlink()
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        srv.listen(8)
        log.info("daemon listening on %s", self.socket_path)
        try:
            while True:
                conn, _ = srv.accept()
                threading.Thread(target=self._serve_conn, args=(conn,), daemon=True).start()
        except KeyboardInterrupt:
            log.info("shutting down")
        finally:
            srv.close()
            if self.socket_path.exists():
                self.socket_path.unlink()


def _parse_languages(value: str) -> list[str]:
    """Parse a comma-separated list of two-letter language codes (e.g. "pl,en")."""
    codes = [c.strip().lower() for c in value.split(",") if c.strip()]
    if not codes or not all(_LANG_CODE.match(c) for c in codes):
        raise argparse.ArgumentTypeError(
            f"expected comma-separated two-letter codes (e.g. pl,en), got {value!r}"
        )
    return codes


def main() -> None:
    parser = argparse.ArgumentParser(prog="local-recorder")
    parser.add_argument(
        "--languages",
        type=_parse_languages,
        metavar="pl,en",
        help="candidate languages for per-window detection (comma-separated "
        "two-letter codes); overrides whisper.languages",
    )
    args = parser.parse_args()

    cfg = load_config()
    if args.languages:
        cfg["whisper"]["languages"] = args.languages
    _setup_logging(Path(cfg["log_file"]).expanduser())
    log.info("whisper languages: %s", cfg["whisper"].get("languages") or "auto")
    Daemon(cfg).run()


if __name__ == "__main__":
    main()

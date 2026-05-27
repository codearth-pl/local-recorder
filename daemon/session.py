"""Per-meeting session: collects captions, owns the audio capture, and runs the
transcription -> alignment -> output pipeline when the meeting ends."""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path

from . import align, output
from .audio import AudioRecorder

log = logging.getLogger("local-recorder.session")


def _slug(text: str, max_len: int = 60) -> str:
    text = re.sub(r"[^\w\s-]", "", text or "").strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:max_len] or "meeting"


class Session:
    def __init__(self, meeting_id: str, meta: dict, cfg: dict):
        self.meeting_id = meeting_id
        self.cfg = cfg
        self.captions: list[dict] = []
        self.start_epoch = time.time()
        started = datetime.now().astimezone()
        self.meta = {
            "title": meta.get("title") or "Meeting",
            "platform": meta.get("platform"),
            "url": meta.get("url"),
            "participants": meta.get("participants") or [],
            "started_at": started.isoformat(),
        }

        out_root = Path(cfg["output_dir"]).expanduser()
        stamp = started.strftime("%Y-%m-%dT%H-%M-%S")
        self.out_dir = out_root / f"{stamp}-{_slug(self.meta['title'])}"
        self.out_dir.mkdir(parents=True, exist_ok=True)

        self.audio_path = self.out_dir / "audio.wav"
        self.recorder = AudioRecorder(self.audio_path, cfg["audio"])
        try:
            self.recorder.start()
            # Anchor caption->audio time conversion to the moment capture began.
            self.start_epoch = time.time()
        except Exception:  # noqa: BLE001
            log.exception("audio capture failed to start; continuing captions-only")
            self.recorder = None  # type: ignore[assignment]

    def add_caption(self, cap: dict) -> None:
        if not cap.get("text"):
            return
        self.captions.append(
            {
                "speaker": cap.get("speaker") or align.UNKNOWN_SPEAKER,
                "text": cap["text"],
                "t_start": int(cap["t_start"]),
                "t_end": int(cap.get("t_end", cap["t_start"])),
            }
        )

    def finalize(self) -> dict:
        """Stop recording, transcribe, align, and write transcripts.

        Returns the map of written format -> path.
        """
        log.info("finalizing session %s (%d captions)", self.meeting_id, len(self.captions))
        wav = self.recorder.stop() if self.recorder else None

        blocks = None
        if wav is not None:
            try:
                from . import transcribe  # lazy: avoids importing torch unless needed

                segments = transcribe.transcribe(str(wav), self.cfg["whisper"])
                blocks = align.build_transcript(
                    segments,
                    self.captions,
                    self.start_epoch,
                    self.cfg["align"]["caption_latency"],
                    self.cfg["align"]["tolerance"],
                )
                self.meta["source"] = "whisper+captions"
            except Exception:  # noqa: BLE001
                log.exception("transcription failed; falling back to captions-only")

        if blocks is None:
            blocks = align.captions_only_transcript(self.captions, self.start_epoch)
            self.meta.setdefault("source", "captions-only")

        paths = output.write_all(self.out_dir, blocks, self.meta)

        if wav is not None and not self.cfg.get("keep_audio", False):
            try:
                wav.unlink()
            except OSError:
                log.warning("could not delete audio file %s", wav)

        log.info("transcript written: %s", paths["md"])
        return paths

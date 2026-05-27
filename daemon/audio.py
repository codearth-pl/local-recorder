"""Audio capture via ffmpeg over PipeWire's PulseAudio compatibility layer.

Records two streams and mixes them into one mono WAV suitable for Whisper:
  * the default sink monitor  -> remote participants (what you hear)
  * the default source (mic)  -> your own voice

Uses the pulse special device names @DEFAULT_MONITOR@ / @DEFAULT_SOURCE@ so no
`pactl` lookup is required. Per-application sink-input targeting can be layered
on later; the default monitor is the robust baseline.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger("local-recorder.audio")


class AudioRecorder:
    """Wraps a single ffmpeg capture process for one meeting."""

    def __init__(self, out_path: Path, audio_cfg: dict):
        self.out_path = out_path
        self.sample_rate = int(audio_cfg.get("sample_rate", 16000))
        self.monitor_device = audio_cfg.get("monitor_device", "@DEFAULT_MONITOR@")
        self.mic_device = audio_cfg.get("mic_device", "@DEFAULT_SOURCE@")
        self._proc: subprocess.Popen | None = None

    def _build_command(self) -> list[str]:
        # Mix monitor + mic into one mono stream. normalize=0 keeps levels raw so
        # a quiet participant isn't amplified into noise; duration=longest so the
        # recording spans the whole call even if one input is briefly silent.
        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-f", "pulse", "-i", self.monitor_device,
            "-f", "pulse", "-i", self.mic_device,
            "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0",
            "-ac", "1",
            "-ar", str(self.sample_rate),
            "-y", str(self.out_path),
        ]

    def start(self) -> None:
        if self._proc is not None:
            raise RuntimeError("recorder already started")
        cmd = self._build_command()
        log.info("starting audio capture: %s", " ".join(cmd))
        # stdin=PIPE so we can send 'q' for a clean shutdown that finalizes the WAV.
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def stop(self, timeout: float = 10.0) -> Path | None:
        """Stop ffmpeg cleanly and return the WAV path (or None on failure)."""
        if self._proc is None:
            return None
        proc, self._proc = self._proc, None
        try:
            if proc.poll() is None:
                # 'q' tells ffmpeg to stop and flush the output container.
                try:
                    assert proc.stdin is not None
                    proc.stdin.write(b"q")
                    proc.stdin.flush()
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    proc.terminate()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    log.warning("ffmpeg did not exit on 'q'; terminating")
                    proc.terminate()
                    proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - we always want to surface the path
            log.exception("error stopping ffmpeg")
        finally:
            if proc.returncode not in (0, None):
                stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
                log.warning("ffmpeg exited %s: %s", proc.returncode, stderr.strip()[-500:])

        if self.out_path.exists() and self.out_path.stat().st_size > 0:
            log.info("audio captured: %s (%d bytes)", self.out_path, self.out_path.stat().st_size)
            return self.out_path
        log.error("audio capture produced no usable file at %s", self.out_path)
        return None

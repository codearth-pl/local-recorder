"""WhisperX transcription with word-level timestamps.

GPU-first (CUDA -> large-v3/float16), CPU fallback (-> distil-large-v3/int8).
We deliberately do NOT run WhisperX's pyannote diarization: speaker names come
from the caption stream (see align.py), which is more reliable than acoustic
clustering and gives real names instead of SPEAKER_00.

whisperx/torch are imported lazily so the daemon and the caption-only path can
run without the heavy ML stack installed.
"""
from __future__ import annotations

import logging

log = logging.getLogger("local-recorder.transcribe")


def detect_device() -> str:
    """Return 'cuda' if a usable GPU is present, else 'cpu'."""
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception as exc:  # noqa: BLE001
        log.warning("torch/CUDA probe failed, falling back to CPU: %s", exc)
    return "cpu"


def transcribe(audio_path: str, whisper_cfg: dict) -> list[dict]:
    """Transcribe `audio_path` and return word-aligned segments.

    Each segment: {"start": float, "end": float, "text": str}. Raises
    RuntimeError if whisperx is unavailable so the caller can fall back to a
    captions-only transcript.
    """
    try:
        import whisperx
    except ImportError as exc:
        raise RuntimeError(
            "whisperx not installed; run `uv sync --extra whisper`"
        ) from exc

    device = detect_device()
    if device == "cuda":
        model_name = whisper_cfg.get("gpu_model", "large-v3")
        compute_type = whisper_cfg.get("compute_type_gpu", "float16")
    else:
        model_name = whisper_cfg.get("cpu_model", "distil-large-v3")
        compute_type = whisper_cfg.get("compute_type_cpu", "int8")
    language = whisper_cfg.get("language")
    batch_size = int(whisper_cfg.get("batch_size", 16))

    log.info("transcribing on %s with %s (%s)", device, model_name, compute_type)

    audio = whisperx.load_audio(audio_path)
    model = whisperx.load_model(
        model_name, device, compute_type=compute_type, language=language
    )
    result = model.transcribe(audio, batch_size=batch_size)
    lang = result.get("language", language or "en")

    # Forced alignment -> accurate word/segment timestamps.
    try:
        align_model, metadata = whisperx.load_align_model(language_code=lang, device=device)
        result = whisperx.align(
            result["segments"], align_model, metadata, audio, device,
            return_char_alignments=False,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("alignment step failed (%s); using coarse segment timestamps", exc)

    segments = [
        {"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()}
        for s in result.get("segments", [])
        if s.get("text", "").strip()
    ]
    log.info("transcription produced %d segments", len(segments))
    return segments

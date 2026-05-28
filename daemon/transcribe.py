"""WhisperX transcription with word-level timestamps and dominant-language detection.

GPU-first (CUDA -> large-v3/float16), CPU fallback (-> distil-large-v3/int8).
We deliberately do NOT run WhisperX's pyannote diarization: speaker names come
from the caption stream (see align.py), which is more reliable than acoustic
clustering and gives real names instead of SPEAKER_00.

WhisperX is a one-language-per-file model: it detects language once (first 30s)
and applies it to the whole recording. Meetings here mix Polish and English, so
instead of trusting that single first-30s guess we sample the whole file in ~30s
windows, detect each window's language constrained to a candidate allowlist
(default pl+en), and transcribe the file once in the majority ("dominant")
language. A forced `whisper.language` overrides detection entirely. We do NOT
split the file per language: per-span passes fragmented utterances and hurt both
word accuracy and the alignment timestamps that drive speaker attribution.

whisperx/torch are imported lazily so the daemon and the caption-only path can
run without the heavy ML stack installed.
"""
from __future__ import annotations

import gc
import logging
import math
from collections import Counter

log = logging.getLogger("local-recorder.transcribe")

# Granularity of language detection. Switches happen at utterance boundaries, so
# 30s windows are plenty to catch a Polish -> English transition (and back).
WINDOW_S = 30


def _is_oom(exc: Exception) -> bool:
    """True for a CUDA/host out-of-memory error.

    Covers ctranslate2's `RuntimeError("CUDA failed with error out of memory")`
    and torch's `OutOfMemoryError` (itself a RuntimeError subclass).
    """
    return "out of memory" in str(exc).lower() or type(exc).__name__ == "OutOfMemoryError"


def _attempt_plan(device: str, gpu_batch: int, cpu_batch: int) -> list[tuple[str, int]]:
    """Ordered (device, batch_size) attempts, shrinking batch then dropping to CPU.

    An OOM on a long-lived daemon's nth meeting is recoverable: free the GPU and
    retry smaller, finally on CPU, before giving up to a captions-only transcript.
    """
    if device != "cuda":
        return [("cpu", cpu_batch)]
    seen: set[int] = set()
    batches = [b for b in (gpu_batch, gpu_batch // 2, max(1, gpu_batch // 4)) if b >= 1]
    cuda = [b for b in batches if not (b in seen or seen.add(b))]
    return [("cuda", b) for b in cuda] + [("cpu", cpu_batch)]


def detect_device() -> str:
    """Return 'cuda' if a usable GPU is present, else 'cpu'."""
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception as exc:  # noqa: BLE001
        log.warning("torch/CUDA probe failed, falling back to CPU: %s", exc)
    return "cpu"


def _resolve_languages(whisper_cfg: dict) -> list[str] | None:
    """Candidate languages for detection.

    A single forced `language` wins (disables multi-language). Otherwise the
    `languages` allowlist is used; empty/None means fully automatic detection.
    """
    forced = whisper_cfg.get("language")
    if forced:
        return [str(forced)]
    langs = whisper_cfg.get("languages")
    if not langs:
        return None
    return [str(lang) for lang in langs]


def _dominant_language(window_langs: list[str], fallback: str) -> str:
    """Majority-vote the per-window detections into one language for the whole file.

    Ties are broken by first appearance (Counter preserves insertion order for
    equal counts), so the result is deterministic. An empty list (no windows)
    yields `fallback`.
    """
    if not window_langs:
        return fallback
    return Counter(window_langs).most_common(1)[0][0]


def _detect_language(model, audio_window, allowed: list[str] | None) -> str:
    """Detect the language of one audio window, constrained to `allowed`.

    Mirrors WhisperX's own `detect_language` but reads the full ranked language
    list and returns the best code that is in `allowed` (the built-in returns
    only top-1, which can't honor an allowlist when the top guess is a third,
    misdetected language).
    """
    from whisperx.audio import N_SAMPLES, log_mel_spectrogram

    wmodel = model.model  # faster_whisper.WhisperModel
    n_mels = wmodel.feat_kwargs.get("feature_size")
    pad = 0 if audio_window.shape[0] >= N_SAMPLES else N_SAMPLES - audio_window.shape[0]
    mel = log_mel_spectrogram(audio_window[:N_SAMPLES], n_mels=n_mels or 80, padding=pad)
    encoder_output = wmodel.encode(mel)
    ranked = wmodel.model.detect_language(encoder_output)[0]  # [(token, prob), ...] desc
    for token, _prob in ranked:
        code = token[2:-2]  # "<|en|>" -> "en"
        if allowed is None or code in allowed:
            return code
    return ranked[0][0][2:-2]


def _free_gpu(device: str) -> None:
    """Reclaim GPU memory after a transcription attempt.

    The daemon is long-lived, so models from a finished meeting must not linger:
    `gc.collect()` breaks the reference cycles that otherwise pin the ctranslate2
    and align models until the next automatic GC, and `empty_cache()` returns
    torch's reserved blocks to the driver. Without this, footprint accumulates
    across meetings and a later finalize OOMs even though one meeting fits.
    """
    gc.collect()
    if device == "cuda":
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass


def _run(whisperx, sample_rate: int, audio, whisper_cfg: dict,
         device: str, batch_size: int) -> list[dict]:
    """One transcription attempt on `device`. Always frees the GPU before returning."""
    if device == "cuda":
        model_name = whisper_cfg.get("gpu_model", "large-v3")
        compute_type = whisper_cfg.get("compute_type_gpu", "float16")
    else:
        model_name = whisper_cfg.get("cpu_model", "distil-large-v3")
        compute_type = whisper_cfg.get("compute_type_cpu", "int8")
    candidates = _resolve_languages(whisper_cfg)
    total_s = len(audio) / sample_rate

    log.info(
        "transcribing on %s with %s (%s), batch_size=%d, languages=%s",
        device, model_name, compute_type, batch_size, candidates or "auto",
    )

    model = None
    try:
        # Load with language=None so the tokenizer is rebuilt once the language is
        # known (forced, auto-detected dominant, or WhisperX's own first-30s guess).
        model = whisperx.load_model(
            model_name, device, compute_type=compute_type, language=None
        )

        if candidates is None:
            # Fully automatic: let WhisperX detect on the first 30s.
            language = None
        elif len(candidates) == 1:
            # Forced / manual override -> skip detection entirely.
            language = candidates[0]
        else:
            # Sample the whole file in windows and transcribe in the majority
            # language. Detection is a cheap encoder pass relative to transcription.
            n_windows = max(1, math.ceil(total_s / WINDOW_S))
            window_langs = [
                _detect_language(
                    model,
                    audio[int(i * WINDOW_S * sample_rate):
                          int(min((i + 1) * WINDOW_S, total_s) * sample_rate)],
                    candidates,
                )
                for i in range(n_windows)
            ]
            language = _dominant_language(window_langs, candidates[0])
            log.info("dominant language: %s (windows: %s)", language, dict(Counter(window_langs)))

        result = model.transcribe(audio, batch_size=batch_size, language=language)
        segs = result.get("segments", [])
        lang = result.get("language", language or "en")

        try:
            align_model, metadata = whisperx.load_align_model(
                language_code=lang, device=device
            )
            if segs:
                aligned = whisperx.align(
                    segs, align_model, metadata, audio, device,
                    return_char_alignments=False,
                )
                segs = aligned.get("segments", segs)
        except Exception as exc:  # noqa: BLE001
            log.warning("alignment failed for %s (%s); coarse timestamps", lang, exc)

        segments = [
            {"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()}
            for s in segs
            if s.get("text", "").strip()
        ]
        segments.sort(key=lambda s: s["start"])
        log.info("transcription produced %d segments in %s", len(segments), lang)
        return segments
    finally:
        model = None
        _free_gpu(device)


def transcribe(audio_path: str, whisper_cfg: dict) -> list[dict]:
    """Transcribe `audio_path` and return word-aligned segments.

    Each segment: {"start": float, "end": float, "text": str}. Raises
    RuntimeError if whisperx is unavailable so the caller can fall back to a
    captions-only transcript. A CUDA out-of-memory error is recoverable: the GPU
    is freed and the attempt retried at a smaller batch, then on CPU, before the
    error finally propagates.
    """
    try:
        import whisperx
        from whisperx.audio import SAMPLE_RATE
    except ImportError as exc:
        raise RuntimeError(
            "whisperx not installed; run `uv sync --extra whisper`"
        ) from exc

    audio = whisperx.load_audio(audio_path)
    if not len(audio):
        log.warning("empty audio; nothing to transcribe")
        return []

    batch_size = int(whisper_cfg.get("batch_size", 16))
    plan = _attempt_plan(detect_device(), gpu_batch=batch_size, cpu_batch=batch_size)

    last_exc: Exception | None = None
    for i, (device, batch) in enumerate(plan):
        try:
            return _run(whisperx, SAMPLE_RATE, audio, whisper_cfg, device, batch)
        except RuntimeError as exc:
            last_exc = exc
            if _is_oom(exc) and i < len(plan) - 1:
                log.warning(
                    "out of memory on %s batch=%d; freeing GPU and retrying as %s",
                    device, batch, plan[i + 1],
                )
                continue
            raise
    raise last_exc  # pragma: no cover - loop returns or raises above

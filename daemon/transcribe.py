"""WhisperX transcription with word-level timestamps and per-window language switching.

GPU-first (CUDA -> large-v3/float16), CPU fallback (-> distil-large-v3/int8).
We deliberately do NOT run WhisperX's pyannote diarization: speaker names come
from the caption stream (see align.py), which is more reliable than acoustic
clustering and gives real names instead of SPEAKER_00.

WhisperX detects language only once (first 30s) and applies it to the whole file.
Meetings here open in Polish and switch to English, so instead we detect language
on ~30s windows (constrained to a candidate allowlist, default pl+en), group
consecutive same-language windows into spans, transcribe each span in its own
language, align it with that language's model, and concatenate. Downstream
(align.py) only uses segment start/end/text, so the result is the same shape.

whisperx/torch are imported lazily so the daemon and the caption-only path can
run without the heavy ML stack installed.
"""
from __future__ import annotations

import logging
import math

log = logging.getLogger("local-recorder.transcribe")

# Granularity of language detection. Switches happen at utterance boundaries, so
# 30s windows are plenty to catch a Polish -> English transition (and back).
WINDOW_S = 30


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


def _group_language_spans(
    window_langs: list[str], window_s: float, total_s: float
) -> list[tuple[float, float, str]]:
    """Collapse a per-window language list into contiguous (start, end, lang) spans."""
    spans: list[tuple[float, float, str]] = []
    for i, lang in enumerate(window_langs):
        start = i * window_s
        end = min((i + 1) * window_s, total_s)
        if end <= start:
            continue
        if spans and spans[-1][2] == lang:
            spans[-1] = (spans[-1][0], end, lang)
        else:
            spans.append((start, end, lang))
    return spans


def _offset_segments(segments: list[dict], dt: float) -> list[dict]:
    """Shift span-local segment times back to recording-relative seconds."""
    return [
        {**s, "start": float(s["start"]) + dt, "end": float(s["end"]) + dt}
        for s in segments
    ]


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


def transcribe(audio_path: str, whisper_cfg: dict) -> list[dict]:
    """Transcribe `audio_path` and return word-aligned segments.

    Each segment: {"start": float, "end": float, "text": str}. Raises
    RuntimeError if whisperx is unavailable so the caller can fall back to a
    captions-only transcript.
    """
    try:
        import whisperx
        from whisperx.audio import SAMPLE_RATE
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
    batch_size = int(whisper_cfg.get("batch_size", 16))
    candidates = _resolve_languages(whisper_cfg)

    log.info(
        "transcribing on %s with %s (%s), languages=%s",
        device, model_name, compute_type, candidates or "auto",
    )

    audio = whisperx.load_audio(audio_path)
    total_s = len(audio) / SAMPLE_RATE if len(audio) else 0.0
    if total_s <= 0:
        log.warning("empty audio; nothing to transcribe")
        return []

    # Load with language=None so the tokenizer is rebuilt per span and the
    # detected language for each span actually takes effect.
    model = whisperx.load_model(
        model_name, device, compute_type=compute_type, language=None
    )

    # One language forced -> single span, no detection. Otherwise detect per
    # window and group consecutive same-language windows.
    if candidates is not None and len(candidates) == 1:
        spans = [(0.0, total_s, candidates[0])]
    else:
        n_windows = max(1, math.ceil(total_s / WINDOW_S))
        window_langs = [
            _detect_language(
                model,
                audio[int(i * WINDOW_S * SAMPLE_RATE):
                      int(min((i + 1) * WINDOW_S, total_s) * SAMPLE_RATE)],
                candidates,
            )
            for i in range(n_windows)
        ]
        spans = _group_language_spans(window_langs, float(WINDOW_S), total_s)

    log.info("language spans: %s", [(round(s, 1), round(e, 1), lang) for s, e, lang in spans])

    align_cache: dict[str, tuple | None] = {}

    def _aligner(lang: str):
        if lang not in align_cache:
            try:
                align_cache[lang] = whisperx.load_align_model(
                    language_code=lang, device=device
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("no alignment model for %s (%s); coarse timestamps", lang, exc)
                align_cache[lang] = None
        return align_cache[lang]

    all_segments: list[dict] = []
    for start, end, lang in spans:
        chunk = audio[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)]
        if len(chunk) == 0:
            continue
        result = model.transcribe(chunk, batch_size=batch_size, language=lang)
        segs = result.get("segments", [])

        am = _aligner(lang)
        if am is not None and segs:
            try:
                aligned = whisperx.align(
                    segs, am[0], am[1], chunk, device, return_char_alignments=False
                )
                segs = aligned.get("segments", segs)
            except Exception as exc:  # noqa: BLE001
                log.warning("alignment failed for %s span (%s); coarse timestamps", lang, exc)

        all_segments.extend(_offset_segments(segs, start))

    segments = [
        {"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()}
        for s in all_segments
        if s.get("text", "").strip()
    ]
    segments.sort(key=lambda s: s["start"])
    log.info("transcription produced %d segments across %d span(s)", len(segments), len(spans))
    return segments

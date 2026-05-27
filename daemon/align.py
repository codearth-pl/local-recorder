"""Align Whisper segments with caption-derived speaker turns.

The browser extension gives us *who* spoke and *roughly when* (caption turns
carry real speaker names but lower-quality text). Whisper gives us accurate
*words* with precise timestamps but no names. We use the caption turns as the
speaker timeline and stamp each Whisper segment with the name of the turn it
overlaps most -- this yields accurate text with real names, without pyannote.

All functions operate on plain dicts/lists so they can be unit-tested without
audio or ML dependencies.

Caption turn (from the extension), times in epoch milliseconds:
    {"speaker": str, "text": str, "t_start": int, "t_end": int}

Whisper segment, times in seconds relative to the start of the recording:
    {"start": float, "end": float, "text": str}
"""
from __future__ import annotations

UNKNOWN_SPEAKER = "Unknown"


def captions_to_relative(
    captions: list[dict],
    audio_start_epoch: float,
    caption_latency: float,
) -> list[dict]:
    """Convert caption turns from epoch-ms to recording-relative seconds.

    `caption_latency` shifts caption times earlier to compensate for the lag
    between speech and the caption appearing in the DOM.
    """
    turns: list[dict] = []
    for cap in captions:
        start = cap["t_start"] / 1000.0 - audio_start_epoch - caption_latency
        end = cap["t_end"] / 1000.0 - audio_start_epoch - caption_latency
        turns.append(
            {
                "speaker": cap.get("speaker") or UNKNOWN_SPEAKER,
                "text": cap.get("text", ""),
                "start": start,
                "end": max(end, start),
            }
        )
    turns.sort(key=lambda t: t["start"])
    return turns


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _gap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Distance between two intervals; 0 if they touch or overlap."""
    if a_end < b_start:
        return b_start - a_end
    if b_end < a_start:
        return a_start - b_end
    return 0.0


def assign_speaker(segment: dict, turns: list[dict], tolerance: float) -> str:
    """Pick the speaker whose turn best matches a single Whisper segment."""
    s_start, s_end = segment["start"], segment["end"]
    best_turn = None
    best_overlap = 0.0
    for turn in turns:
        ov = _overlap(s_start, s_end, turn["start"], turn["end"])
        if ov > best_overlap:
            best_overlap, best_turn = ov, turn
    if best_turn is not None:
        return best_turn["speaker"]

    # No overlap: fall back to the nearest turn within tolerance.
    nearest = None
    nearest_gap = tolerance
    for turn in turns:
        g = _gap(s_start, s_end, turn["start"], turn["end"])
        if g <= nearest_gap:
            nearest_gap, nearest = g, turn
    return nearest["speaker"] if nearest is not None else UNKNOWN_SPEAKER


def assign_speakers(
    segments: list[dict],
    turns: list[dict],
    tolerance: float,
) -> list[dict]:
    """Return Whisper segments annotated with a `speaker` field."""
    out = []
    for seg in segments:
        speaker = assign_speaker(seg, turns, tolerance) if turns else UNKNOWN_SPEAKER
        out.append({**seg, "speaker": speaker})
    return out


def group_by_speaker(segments: list[dict]) -> list[dict]:
    """Merge consecutive segments from the same speaker into blocks."""
    blocks: list[dict] = []
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        if blocks and blocks[-1]["speaker"] == seg["speaker"]:
            blocks[-1]["text"] = (blocks[-1]["text"] + " " + text).strip()
            blocks[-1]["end"] = seg["end"]
        else:
            blocks.append(
                {
                    "speaker": seg["speaker"],
                    "text": text,
                    "start": seg["start"],
                    "end": seg["end"],
                }
            )
    return blocks


def build_transcript(
    whisper_segments: list[dict],
    captions: list[dict],
    audio_start_epoch: float,
    caption_latency: float,
    tolerance: float,
) -> list[dict]:
    """Full pipeline: caption turns + Whisper segments -> grouped, named blocks."""
    turns = captions_to_relative(captions, audio_start_epoch, caption_latency)
    annotated = assign_speakers(whisper_segments, turns, tolerance)
    return group_by_speaker(annotated)


def captions_only_transcript(
    captions: list[dict],
    audio_start_epoch: float,
) -> list[dict]:
    """Fallback transcript built purely from captions (no audio/Whisper).

    Used for the caption-only MVP and when transcription is unavailable.
    """
    blocks: list[dict] = []
    for cap in sorted(captions, key=lambda c: c["t_start"]):
        text = (cap.get("text") or "").strip()
        if not text:
            continue
        speaker = cap.get("speaker") or UNKNOWN_SPEAKER
        start = cap["t_start"] / 1000.0 - audio_start_epoch
        end = cap["t_end"] / 1000.0 - audio_start_epoch
        if blocks and blocks[-1]["speaker"] == speaker:
            blocks[-1]["text"] = (blocks[-1]["text"] + " " + text).strip()
            blocks[-1]["end"] = end
        else:
            blocks.append({"speaker": speaker, "text": text, "start": start, "end": end})
    return blocks

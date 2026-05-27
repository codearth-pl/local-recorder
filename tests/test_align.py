"""Unit tests for caption<->Whisper alignment. Run: python -m pytest tests/ -q"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daemon import align  # noqa: E402


def _caps(audio_start=1000.0):
    """Two caption turns: Alice 0-3s, Bob 4-7s (in audio-relative time).

    Built in epoch-ms with a 0 latency for predictable conversion.
    """
    base = int(audio_start * 1000)
    return [
        {"speaker": "Alice", "text": "hello there", "t_start": base + 0, "t_end": base + 3000},
        {"speaker": "Bob", "text": "hi alice", "t_start": base + 4000, "t_end": base + 7000},
    ]


def test_captions_to_relative_applies_latency():
    caps = _caps(audio_start=1000.0)
    turns = align.captions_to_relative(caps, audio_start_epoch=1000.0, caption_latency=1.5)
    # First turn started at +0s epoch-relative, shifted earlier by 1.5s.
    assert turns[0]["speaker"] == "Alice"
    assert turns[0]["start"] == -1.5
    assert turns[0]["end"] == 1.5
    assert turns[1]["start"] == 2.5


def test_assign_speakers_by_overlap():
    turns = align.captions_to_relative(_caps(), audio_start_epoch=1000.0, caption_latency=0.0)
    segments = [
        {"start": 0.5, "end": 2.0, "text": "Hello there friends"},
        {"start": 4.5, "end": 6.0, "text": "Hi Alice how are you"},
    ]
    out = align.assign_speakers(segments, turns, tolerance=2.0)
    assert out[0]["speaker"] == "Alice"
    assert out[1]["speaker"] == "Bob"


def test_assign_nearest_within_tolerance():
    turns = align.captions_to_relative(_caps(), audio_start_epoch=1000.0, caption_latency=0.0)
    # Segment at 3.5s overlaps neither (Alice ends 3.0, Bob starts 4.0) but is
    # within tolerance of both; nearest is Bob's start (0.5s) vs Alice's end (0.5s).
    seg = {"start": 3.4, "end": 3.6, "text": "um"}
    out = align.assign_speakers([seg], turns, tolerance=2.0)
    assert out[0]["speaker"] in {"Alice", "Bob"}


def test_unknown_when_far_outside_tolerance():
    turns = align.captions_to_relative(_caps(), audio_start_epoch=1000.0, caption_latency=0.0)
    seg = {"start": 100.0, "end": 101.0, "text": "way later"}
    out = align.assign_speakers([seg], turns, tolerance=2.0)
    assert out[0]["speaker"] == align.UNKNOWN_SPEAKER


def test_group_by_speaker_merges_consecutive():
    segs = [
        {"start": 0, "end": 1, "text": "Hello", "speaker": "Alice"},
        {"start": 1, "end": 2, "text": "there", "speaker": "Alice"},
        {"start": 4, "end": 5, "text": "Hi", "speaker": "Bob"},
    ]
    blocks = align.group_by_speaker(segs)
    assert len(blocks) == 2
    assert blocks[0] == {"speaker": "Alice", "text": "Hello there", "start": 0, "end": 2}
    assert blocks[1]["speaker"] == "Bob"


def test_build_transcript_end_to_end():
    caps = _caps(audio_start=1000.0)
    segments = [
        {"start": 0.5, "end": 2.5, "text": "Hello there"},
        {"start": 4.5, "end": 6.5, "text": "Hi Alice"},
    ]
    blocks = align.build_transcript(
        segments, caps, audio_start_epoch=1000.0, caption_latency=0.0, tolerance=2.0
    )
    assert [b["speaker"] for b in blocks] == ["Alice", "Bob"]
    assert blocks[0]["text"] == "Hello there"


def test_captions_only_transcript():
    caps = _caps(audio_start=1000.0)
    blocks = align.captions_only_transcript(caps, audio_start_epoch=1000.0)
    assert [b["speaker"] for b in blocks] == ["Alice", "Bob"]
    assert blocks[0]["start"] == 0.0

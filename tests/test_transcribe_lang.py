"""Unit tests for the multi-language helpers. Run: python -m pytest tests/ -q

These cover only the pure, ML-free helpers (language parsing, span grouping,
time offsetting); the live WhisperX path is never imported, matching the rest of
the suite.
"""
import argparse
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daemon import transcribe  # noqa: E402
from daemon.daemon import _parse_languages  # noqa: E402


def test_parse_languages_basic():
    assert _parse_languages("pl,en") == ["pl", "en"]


def test_parse_languages_tolerates_whitespace_and_case():
    assert _parse_languages(" PL , En ") == ["pl", "en"]


@pytest.mark.parametrize("bad", ["polish", "", "e", "pl,english", ",,"])
def test_parse_languages_rejects_invalid(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_languages(bad)


def test_resolve_languages_forced_single_wins():
    assert transcribe._resolve_languages({"language": "en", "languages": ["pl", "en"]}) == ["en"]


def test_resolve_languages_uses_allowlist():
    assert transcribe._resolve_languages({"language": None, "languages": ["pl", "en"]}) == ["pl", "en"]


def test_resolve_languages_empty_means_auto():
    assert transcribe._resolve_languages({"languages": []}) is None
    assert transcribe._resolve_languages({}) is None


def test_group_spans_single_language():
    spans = transcribe._group_language_spans(["pl", "pl", "pl"], window_s=30.0, total_s=75.0)
    assert spans == [(0.0, 75.0, "pl")]


def test_group_spans_one_switch():
    spans = transcribe._group_language_spans(["pl", "pl", "en", "en"], window_s=30.0, total_s=110.0)
    assert spans == [(0.0, 60.0, "pl"), (60.0, 110.0, "en")]


def test_group_spans_back_and_forth():
    spans = transcribe._group_language_spans(["pl", "en", "pl"], window_s=30.0, total_s=90.0)
    assert spans == [(0.0, 30.0, "pl"), (30.0, 60.0, "en"), (60.0, 90.0, "pl")]


def test_offset_segments_shifts_times_and_keeps_text():
    segs = [{"start": 0.0, "end": 2.0, "text": "czesc"}, {"start": 5.0, "end": 6.5, "text": "hi"}]
    out = transcribe._offset_segments(segs, 60.0)
    assert out == [
        {"start": 60.0, "end": 62.0, "text": "czesc"},
        {"start": 65.0, "end": 66.5, "text": "hi"},
    ]

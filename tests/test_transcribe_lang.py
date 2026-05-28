"""Unit tests for the language-selection helpers. Run: python -m pytest tests/ -q

These cover only the pure, ML-free helpers (language parsing, candidate
resolution, dominant-language voting); the live WhisperX path is never imported,
matching the rest of the suite.
"""
import argparse
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daemon import transcribe  # noqa: E402
from daemon.daemon import _parse_language, _parse_languages  # noqa: E402


def test_parse_languages_basic():
    assert _parse_languages("pl,en") == ["pl", "en"]


def test_parse_languages_tolerates_whitespace_and_case():
    assert _parse_languages(" PL , En ") == ["pl", "en"]


@pytest.mark.parametrize("bad", ["polish", "", "e", "pl,english", ",,"])
def test_parse_languages_rejects_invalid(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_languages(bad)


def test_parse_language_basic():
    assert _parse_language(" PL ") == "pl"


@pytest.mark.parametrize("bad", ["polish", "", "e", "pl,en"])
def test_parse_language_rejects_invalid(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_language(bad)


def test_resolve_languages_forced_single_wins():
    assert transcribe._resolve_languages({"language": "en", "languages": ["pl", "en"]}) == ["en"]


def test_resolve_languages_uses_allowlist():
    assert transcribe._resolve_languages({"language": None, "languages": ["pl", "en"]}) == ["pl", "en"]


def test_resolve_languages_empty_means_auto():
    assert transcribe._resolve_languages({"languages": []}) is None
    assert transcribe._resolve_languages({}) is None


def test_dominant_language_clear_majority():
    assert transcribe._dominant_language(["pl", "pl", "en"], "en") == "pl"


def test_dominant_language_single_window():
    assert transcribe._dominant_language(["en"], "pl") == "en"


def test_dominant_language_tie_breaks_on_first_seen():
    # Equal counts: Counter preserves insertion order, so the first-seen wins.
    assert transcribe._dominant_language(["en", "pl", "en", "pl"], "xx") == "en"
    assert transcribe._dominant_language(["pl", "en", "pl", "en"], "xx") == "pl"


def test_dominant_language_empty_uses_fallback():
    assert transcribe._dominant_language([], "pl") == "pl"

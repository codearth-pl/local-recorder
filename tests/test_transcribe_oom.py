"""Unit tests for the OOM-recovery helpers. Run: python -m pytest tests/ -q

These cover only the pure, ML-free helpers (`_is_oom`, `_attempt_plan`); the live
WhisperX path is never imported, matching the rest of the suite.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daemon import transcribe  # noqa: E402


def test_is_oom_ctranslate2_message():
    # The literal error ctranslate2/faster-whisper raises.
    assert transcribe._is_oom(RuntimeError("CUDA failed with error out of memory"))


def test_is_oom_torch_named_error():
    # torch raises OutOfMemoryError (a RuntimeError subclass); match by type name
    # even when the message doesn't contain the phrase.
    class OutOfMemoryError(RuntimeError):
        pass

    assert transcribe._is_oom(OutOfMemoryError("CUDA error"))


def test_is_oom_ignores_other_runtime_errors():
    assert not transcribe._is_oom(RuntimeError("model file not found"))


def test_attempt_plan_cuda_shrinks_then_cpu():
    assert transcribe._attempt_plan("cuda", gpu_batch=16, cpu_batch=16) == [
        ("cuda", 16),
        ("cuda", 8),
        ("cuda", 4),
        ("cpu", 16),
    ]


def test_attempt_plan_cpu_device_is_single_attempt():
    assert transcribe._attempt_plan("cpu", gpu_batch=16, cpu_batch=8) == [("cpu", 8)]


def test_attempt_plan_dedups_small_batch():
    # batch_size 1 collapses the //2 and //4 rungs (all 1) to a single cuda try.
    assert transcribe._attempt_plan("cuda", gpu_batch=1, cpu_batch=4) == [
        ("cuda", 1),
        ("cpu", 4),
    ]

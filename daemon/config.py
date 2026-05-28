"""Configuration loading and path helpers."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

_DEFAULTS: dict = {
    "output_dir": "~/recordings",
    "keep_audio": False,
    "socket_path": "~/.local/share/local-recorder/daemon.sock",
    "log_file": "~/.local/share/local-recorder/daemon.log",
    "audio": {
        "sample_rate": 16000,
        "monitor_device": "@DEFAULT_MONITOR@",
        "mic_device": "@DEFAULT_SOURCE@",
    },
    "whisper": {
        "gpu_model": "large-v3",
        "compute_type_gpu": "float16",
        "cpu_model": "distil-large-v3",
        "compute_type_cpu": "int8",
        "batch_size": 16,
        # Single forced language (e.g. "en"); when set it wins and skips
        # detection, transcribing the whole file in that language. Overridable at
        # daemon startup via `--language pl`. Leave None to use `languages` below.
        "language": None,
        # Allowlist constraining auto dominant-language detection: the file is
        # sampled and transcribed once in the majority language among these
        # (meetings here mix Polish and English). Overridable at daemon startup
        # via `--languages pl,en`. Empty/None => fully automatic (WhisperX's
        # first-30s guess).
        "languages": ["pl", "en"],
    },
    "align": {
        "caption_latency": 1.5,
        "tolerance": 2.0,
    },
}

_CONFIG_DIR = Path(__file__).resolve().parent


def expand(path: str) -> Path:
    """Expand ~ and environment variables to an absolute Path."""
    return Path(os.path.expandvars(os.path.expanduser(path))).resolve()


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config() -> dict:
    """Load config.yaml, then overlay config.local.yaml if present."""
    cfg = dict(_DEFAULTS)
    for name in ("config.yaml", "config.local.yaml"):
        path = _CONFIG_DIR / name
        if path.exists():
            with path.open() as fh:
                loaded = yaml.safe_load(fh) or {}
            cfg = _deep_merge(cfg, loaded)
    return cfg

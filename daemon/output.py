"""Write speaker-attributed transcript blocks to disk as Markdown, JSON, SRT."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def _ts(seconds: float) -> str:
    """Seconds -> mm:ss (or h:mm:ss for long meetings)."""
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _srt_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    ms = int(round((seconds - int(seconds)) * 1000))
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_markdown(path: Path, blocks: list[dict], meta: dict) -> None:
    lines = [f"# {meta.get('title') or 'Meeting transcript'}", ""]
    if meta.get("platform"):
        lines.append(f"- **Platform:** {meta['platform']}")
    if meta.get("started_at"):
        lines.append(f"- **Started:** {meta['started_at']}")
    if meta.get("participants"):
        lines.append(f"- **Participants:** {', '.join(meta['participants'])}")
    if meta.get("source"):
        lines.append(f"- **Source:** {meta['source']}")
    lines.append("")
    for b in blocks:
        lines.append(f"**{b['speaker']}** [{_ts(b['start'])}]: {b['text']}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_json(path: Path, blocks: list[dict], meta: dict) -> None:
    path.write_text(
        json.dumps({"meta": meta, "segments": blocks}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def write_srt(path: Path, blocks: list[dict]) -> None:
    lines = []
    for i, b in enumerate(blocks, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_ts(b['start'])} --> {_srt_ts(b['end'])}")
        lines.append(f"{b['speaker']}: {b['text']}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_all(out_root: Path, base: str, blocks: list[dict], meta: dict) -> dict:
    """Write all three formats as <base>.<ext> in out_root; return format -> path."""
    out_root.mkdir(parents=True, exist_ok=True)
    meta = {**meta, "generated_at": datetime.now().astimezone().isoformat()}
    paths = {
        "md": out_root / f"{base}.md",
        "json": out_root / f"{base}.json",
        "srt": out_root / f"{base}.srt",
    }
    write_markdown(paths["md"], blocks, meta)
    write_json(paths["json"], blocks, meta)
    write_srt(paths["srt"], blocks)
    return paths

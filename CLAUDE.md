# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A fully local, "invisible" recorder for Google Meet / Microsoft Teams that produces
speaker-attributed transcripts without a bot joining the call. No cloud. The core trick:
real speaker *names* come from the platform's own live captions (the browser scrapes them),
while accurate *words* come from local WhisperX transcription of the captured audio. The two
are merged by timestamp — there is deliberately **no acoustic diarization** (no pyannote, no
`SPEAKER_00`).

## Commands

```bash
# Python env (Python 3.12 venv already present at .venv/)
uv venv --python 3.12
uv pip install PyYAML                          # daemon + caption-only path
uv sync --extra whisper                        # add torch/whisperx for real transcription

# Run the daemon
.venv/bin/python -m daemon.daemon

# Tests (no audio/GPU needed — they stub the recorder and skip Whisper)
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m pytest tests/test_align.py -q                          # one file
.venv/bin/python -m pytest tests/test_daemon_protocol.py::test_captions_only_flow   # one test

# Install the native-messaging host (after loading extension/ unpacked in the browser)
./native-host/install.sh <EXTENSION_ID> chrome    # or: chromium | brave | edge
```

There is no JS build step or linter configured — the extension loads as raw unpacked source.

## Architecture: three processes, one direction of data flow

```
extension (content scripts)  ──chrome.runtime──▶  background.js (service worker)
        │                                                  │ connectNative
        │ scrapes captions + meeting lifecycle             ▼
        │                                          native-host/host.py  (dumb stdio↔socket relay)
        │                                                  │ Unix socket, newline-JSON
        ▼                                                  ▼
   ~/recordings/<ts>-<title>.{md,json,srt}  ◀──── daemon/  (audio + Whisper + align + output)
```

The pieces are intentionally decoupled by a **newline-delimited JSON protocol over a Unix
socket**. The daemon owns the long-lived state (audio capture, transcription) so it survives
MV3 service-worker eviction and browser restarts. The native host (`native-host/host.py`) is
a deliberately dumb relay: it only translates Chrome's length-prefixed native-messaging frames
into JSON lines and reconnects to the socket; it holds no state.

### Message protocol (the contract between all layers)
One JSON object per line on the socket:
- `{"type":"meeting_start","id",...,"title","platform","url","participants"}`
- `{"type":"caption","id","speaker","text","t_start","t_end"}` — times in **epoch milliseconds**
- `{"type":"meeting_stop","id"}`
- `{"type":"ping"}`

`id` is a per-meeting UUID minted by the content script. The daemon keys sessions by it and
will **auto-create a session** if a caption arrives before `meeting_start` (so data is never
dropped on races).

### Extension layer (`extension/`)
- `content/captions-core.js` — platform-agnostic core. `CaptionTracker` polls the caption
  container (the live-caption DOM mutates a node's text *in place* as someone speaks, then
  adds a new node for the next turn), and **emits a turn only once its text has been stable
  for `STABLE_MS` or its node leaves the DOM**. `runLifecycle` polls for join/leave and drives
  `meeting_start`/`meeting_stop`.
- `content/meet.js`, `content/teams.js` — thin per-platform adapters supplying CSS selectors,
  `isInMeeting()`, `getTitle()`, and `enableCaptions()`. **These selectors are the most fragile
  part of the whole system** — Meet/Teams use obfuscated, churning class names. If captions
  stop being captured, fix the comma-separated selector fallbacks in these two files by
  inspecting a live caption element. Captions must be ON for real names; otherwise speaker
  falls back to `Unknown`.

### Daemon layer (`daemon/`) — the finalize pipeline
On `meeting_stop`, `Session.finalize()` runs **on a background thread** (transcription is slow)
and does: stop ffmpeg → `transcribe.transcribe()` → `align.build_transcript()` →
`output.write_all()`. Every stage degrades gracefully:
- `session.py` — owns one meeting. If audio capture fails to start, it logs and continues
  **captions-only**. `start_epoch` is re-anchored to the moment ffmpeg actually starts; this
  anchor is what converts caption epoch-ms into recording-relative seconds.
- `audio.py` — single ffmpeg process mixing `@DEFAULT_MONITOR@` (what you hear) + `@DEFAULT_SOURCE@`
  (your mic) into one mono 16 kHz WAV via PipeWire's pulse layer (no `pactl`). Stops cleanly by
  writing `q` to ffmpeg's stdin to flush the container. **Known limitation:** captures the whole
  default sink, not just the browser's sink-input.
- `transcribe.py` — WhisperX, GPU-first (`large-v3`/float16) with CPU fallback
  (`distil-large-v3`/int8). `import whisperx`/`torch` are **lazy** so the daemon and tests run
  without the multi-GB ML stack; missing whisperx raises `RuntimeError` → caller falls back to
  captions-only. **Per-window multi-language:** WhisperX natively detects language once (first
  30 s) and locks the whole file; instead we detect language on ~30 s windows constrained to an
  allowlist (`whisper.languages`, default `[pl, en]`, or `--languages pl,en`), group consecutive
  same-language windows into spans, transcribe + align each span in its own language, and merge.
  This handles meetings that start in Polish and switch to English. The pure helpers
  (`_group_language_spans`, `_offset_segments`, `_resolve_languages`) are ML-free and unit-tested.
- `align.py` — pure functions on plain dicts (hence unit-testable with no audio/ML). Converts
  caption turns to recording-relative seconds (shifting earlier by `caption_latency` to undo
  caption lag), stamps each Whisper segment with the speaker of the turn it overlaps most
  (nearest within `tolerance` if no overlap), then merges consecutive same-speaker segments.
- `output.py` — writes Markdown, JSON, and SRT, flat in the output dir as `<datetime>-<slug>.{md,json,srt}` (no per-meeting subfolder).
- `config.py` — `_DEFAULTS` overlaid by `config.yaml` then `config.local.yaml` (gitignored) via
  deep-merge. Tune `align.caption_latency` if names look time-shifted relative to text.

## Conventions / gotchas
- **Caption times are epoch-ms; Whisper times are recording-relative seconds.** The conversion
  hinges on `Session.start_epoch`. Keep this invariant in mind when touching `align.py` or
  `session.py`.
- Keep `align.py` free of audio/ML imports — the alignment unit tests depend on that.
- Don't add pyannote/diarization; speaker identity is a captions concern by design.
- The audio file is deleted after transcription unless `keep_audio: true`.
- Tests stub `AudioRecorder` and never install/import Whisper, so they exercise only the
  captions-only path end-to-end.

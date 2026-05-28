# local-recorder

A **fully local** alternative to fathom.video for **Google Meet** and **Microsoft Teams**.
It records meetings and produces **speaker-attributed transcripts** — without any bot
joining the call, so nobody on the meeting can see it.

## How it works

Three local pieces, no cloud:

1. **Browser extension** scrapes the meeting's **native live captions** (which carry real
   speaker names, because Meet/Teams know who's talking) and the meeting start/stop events.
2. **Local daemon** captures the browser's audio output + your mic and transcribes it with
   **WhisperX** (GPU `large-v3`, CPU fallback) for high-quality words.
3. **Alignment** uses the caption timeline for *who* (real names) and Whisper for *what*
   (accurate text), matching them by timestamp. No acoustic diarization, no `SPEAKER_00`.

```
extension (Meet/Teams captions) ──▶ native-host/host.py ──▶ daemon (audio + Whisper + align)
                                                                      └─▶ ~/recordings/<meeting>.md
```

If audio/Whisper is unavailable, it falls back to a **captions-only** transcript that still
has real speaker names.

## Requirements

- Linux with PipeWire (`pw-record`/`wpctl`) and `ffmpeg` — the audio path uses ffmpeg's
  pulse `@DEFAULT_MONITOR@` / `@DEFAULT_SOURCE@` devices (no `pactl` needed).
- A Chromium-based browser (Chrome/Chromium/Brave/Edge). On Linux, Teams runs in the browser.
- Python 3.10+ and [`uv`](https://docs.astral.sh/uv/).
- Optional but recommended: an NVIDIA GPU for fast `large-v3` transcription.

## Setup

### Quick start (one command)

Once you have the extension's ID (see step 2 below), copy the env template and start everything:

```bash
cp .env.example .env        # then set EXTENSION_ID=... (and BROWSER / LANGUAGES if needed)
./start.sh                  # venv + deps + native host + browser + daemon
```

`start.sh` reads config from `.env`, falling back to the cached `.local-recorder.conf`. Flags
override `.env` for a single run:

```bash
./start.sh --extension-id <ID> --browser chrome --languages pl,en
```

Precedence (highest first): **CLI flags > `.env` > `.local-recorder.conf` > defaults**. The
manual steps below are equivalent if you'd rather wire things up yourself.

### 1. Python environment

```bash
uv venv --python 3.12
uv pip install PyYAML            # daemon + caption-only MVP
# For local Whisper transcription (multi-GB: torch + whisperx):
uv pip install whisperx torch torchaudio ctranslate2
```

### 2. Load the extension

1. `chrome://extensions` → enable **Developer mode** → **Load unpacked** → select `extension/`.
2. Copy the extension **ID** shown on its card.

### 3. Install the native-messaging host

```bash
./native-host/install.sh <EXTENSION_ID> chrome   # or: chromium | brave | edge
```

This writes the host manifest into the browser's `NativeMessagingHosts/` dir, pointing at
`native-host/host.py`, allowed only for your extension ID.

### 4. Run the daemon

```bash
.venv/bin/python -m daemon.daemon

# Override the candidate languages for a meeting (default: pl,en):
.venv/bin/python -m daemon.daemon --languages pl,en
```

Or install it as a user service (edit paths in the unit first):

```bash
cp daemon/systemd/local-recorder.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now local-recorder
journalctl --user -u local-recorder -f
```

## Usage

1. Start the daemon (or have the systemd service running).
2. Join a Google Meet or Teams call in the browser. **Turn on live captions** (the extension
   tries to auto-enable Meet captions; Teams captions you may need to enable manually under
   More (…) → Language and speech → Turn on live captions).
3. Talk. When you leave the call, the daemon transcribes and writes:
   `~/recordings/<timestamp>-<title>.md` (also `.json` and `.srt`).

Audio (the temporary `.wav` beside those files) is deleted after transcription unless you set
`keep_audio: true` in `daemon/config.yaml`.

## Configuration

Edit `daemon/config.yaml` (or copy to `config.local.yaml`, which is gitignored):

- `output_dir`, `keep_audio`
- `whisper.*` — models and compute types for GPU/CPU
- `whisper.languages` — candidate languages, detected **per ~30 s window** (default `[pl, en]`).
  Meetings that open in Polish and switch to English are transcribed correctly instead of
  being locked to whichever language was spoken first. Override at startup with
  `--languages pl,en`. Set `whisper.language` to a single code to force one language and skip
  detection.
- `align.caption_latency` — how far captions lag speech (default 1.5 s); tune if names look
  shifted relative to text
- `align.tolerance` — slack when matching a segment to a speaker turn

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

Covers the alignment logic and a full daemon round-trip (captions-only path, no audio/GPU
needed).

## Caveats

- **Caption selectors are fragile.** Meet/Teams use obfuscated, changing class names. If
  captions stop being captured, update the selectors in `extension/content/meet.js` /
  `teams.js` (inspect a live caption element). Reference OSS:
  `recallai/chrome-recording-transcription-extension`, `yunho0130/google-meet-cc-to-srt`.
- Captions must be **on** for real speaker names. Without them you still get Whisper text,
  but speakers fall back to `Unknown`.
- Per-application audio capture currently uses the **default sink monitor** (captures all
  system playback). Mute other audio during meetings, or extend `daemon/audio.py` to target
  the browser's specific sink-input.

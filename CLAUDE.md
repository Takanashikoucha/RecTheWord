# CLAUDE.md

This file guides Claude Code (claude.ai/code) working in this repository.

## Project Overview

**RecTheWord** is a Windows desktop app for **meeting scenarios**: dual-lane
real-time ASR + translation (microphone = self, system loopback = others),
full-session WAV recording, structured transcript logging, session management,
and **manually-triggered** offline diarization + speaker labeling + AI meeting
minutes. Built on the [LiveTranslate](https://github.com/TheDeathDragon/LiveTranslate)
pipeline (MIT, verified Windows chain), rebranded and extended for meetings.

Hardware target: office PC, **CPU-only, 16GB RAM**. Models are NOT committed to
git (GitHub 100MB/file limit); the repo stays small and distributes via plain
git. `install.ps1` pre-fetches them so the app is out-of-the-box after install:
- **SenseVoice-Small** (default ASR, ~894MB) ← ModelScope (step 8)
- **Whisper base + small** (common alternatives, 148/488MB) ← HF mirror
  `https://hf-mirror.com` preferred, GitHub Release asset as backup (step 9)

Larger models (Fun-ASR-Nano, Whisper medium/large) download on demand from
ModelScope / the HF mirror.

## Layout

```
main.py            # thin top-level entry (imports the app package)
app/               # all application modules (package)
  ├── main logic modules (audio_capture, vad_processor, asr_*, translator, ...)
  ├── UI modules (subtitle_overlay, control_panel, dialogs, log_window, ...)
  ├── meeting modules (_recorder, _sessions, _minutes, _labels, _offline_diarize, ...)
  ├── i18n.py + i18n/   (UI strings)
  └── funasr_nano/      (vendored nano model code)
config.yaml        # base defaults (anchored to repo root, next to main.py)
requirements.txt
install.ps1 / start.bat
```

Runtime resources anchor to their owning file's directory: `config.yaml` and
`logs/`/`transcripts/` to the repo root (next to `main.py`); `i18n/`,
`user_settings.json`, `models/` to `app/` (via `model_manager.APP_DIR`, which
resolves to the repo root for `models/`).

## Running

```bash
# Must use the project venv (system Python lacks dependencies)
.venv/Scripts/python.exe main.py
```

Lint: `python -m ruff check --select F,E,W --ignore E501,E402 main.py app/*.py`
(E402 ignored because `main.py` imports torch before PyQt6).

## Architecture

Pipeline (per lane): **Audio Capture (32ms) → VAD → ASR worker → Translation (async) → Overlay**

```
main.py (LiveTranslateApp)
  |-- model_manager.py     Model detection / download (ModelScope + HF), cache utils
  |-- audio_capture.py     WASAPI loopback + microphone via pyaudiowpatch (two queues)
  |-- vad_processor.py     Silero VAD (32ms, progressive/adaptive silence, backtrack split)
  |-- LanePipeline (in main.py)  Per-lane VAD + incremental-ASR state machine + segment queue
  |-- asr_client.py        Main-process ASR worker manager (spawn, Pipe IPC, timeouts)
  |-- asr_worker.py        ASR subprocess; owns one backend/model (shared by both lanes)
  |-- asr_engine.py        faster-whisper backend
  |-- asr_funasr.py / asr_sensevoice.py / asr_funasr_nano.py / asr_anime_whisper.py
  |-- asr_remote.py / asr_server.py   Remote ASR client / server (GPU offload)
  |-- translator.py        OpenAI-compatible client (streaming / JSON schema / context)
  |-- subtitle_overlay.py  Transparent overlay, DUAL-PANE (top: others 🔊, bottom: self 🎤)
  |-- subtitle_window.py / subtitle_settings.py   OBS subtitle window
  |-- control_panel.py     Settings UI (tabs incl. mic device + mic target language)
  |-- dialogs.py           Setup wizard, model download/load dialogs
  |-- _recorder.py         Meeting WAV recorder (mix/mic/sys)
  |-- _transcript_log.py   Structured transcript JSONL (interim/final + translation patches)
  |-- _sessions.py         Session store (index + per-session dir, CRUD + integrity)
  |-- _session_ui.py       Session management window
  |-- _offline_diarize.py  Offline refine (lazy-loaded FunASR family)
  |-- _labels.py           Speaker name labeling (persist + apply)
  |-- _minutes.py          Minutes generation (dual input source)
  |-- _refine_view.py      Refined-view dialog (speaker renaming + minutes trigger)
```

### Threading / Process Model

- **Main thread**: Qt event loop (all UI)
- **Capture thread**: `_capture_loop` alternately reads both lanes' queues, feeds each lane's VAD, and records into `_recorder`
- **ASR thread**: `_asr_loop` single consumer draining both lanes' segment queues into the shared ASR worker
- **ASR worker process**: `asr_worker.py` owns one concrete backend/model; both lanes share it (serial inference)
- Cross-thread UI updates via **Qt signals**; ASR readiness tracked by `_asr_ready`

### Meeting scenario (differentiators over LiveTranslate)

- **Two lanes**: `AudioCapture` exposes `audio_queue` (sys loopback) and `mic_queue`
  (microphone) separately; mic is NOT mixed into loopback. Each lane has its own
  `LanePipeline` (VAD + incremental ASR state + queue).
- **Two-level display**: periodic interim ASR while VAD accumulates → `yasbd`
  sentence split → commit complete sentences (gray, low-latency); VAD flush →
  final ASR (colored, in-place replace). Translation follows **final only**.
- **Bidirectional translation**: sys lane → primary target (Chinese, always on);
  mic lane → `mic_target_language` (optional, e.g. zh→en). Same-language skipped.
- **Zero background compute after stop**: stopping archives the session
  (3 WAVs + transcript log + meta) and does NOT auto-run refine/minutes. Those
  are manual button actions on the current or a historical session.
- **Manual refine / labels / minutes**: offline diarization (lazy-loaded),
  speaker-name editing (persisted to `labels.json`), minutes from either the
  refined transcript or the live text stream.

### Key Patterns

- **`torch` must be imported before PyQt6** (Windows DLL conflict, PyTorch 2.9+).
  `apply_cache_env()` runs before `import torch` to redirect caches to `./models/`.
- **Device guard**: if `device` starts with `cuda` but `torch.cuda.is_available()`
  is false, the app falls back to `cpu` (office PCs without a GPU).
- **Model cache**: `is_asr_cached` / `get_local_model_path` check both ModelScope
  and HuggingFace layouts. The bundled SenseVoice-Small sits at
  `models/modelscope/models/iic--SenseVoiceSmall/snapshots/master/` (pruned to
  the 5 inference-required files).
- **Config**: `config.yaml` (base defaults; ASR engine defaults to `funasr` /
  `sensevoice-small` / `cpu`) + `user_settings.json` (runtime, atomic write via
  `.tmp` + `os.replace`, takes priority).
- **FunASR** `disable_pbar=True` required in all `generate()` calls (tqdm crashes
  in GUI process). whisper `device="cuda"` (not `cuda:0`); index via `device_index`.
- **Incremental ASR**: `yasbd-lib` sentence splitting (rule-based, 40 langs) with
  comma fallback; proportional audio trim (+0.3s margin, keep ≥0.5s); echo dedup;
  short-utterance buffering (≤8 alnum chars).
- **Atomic settings write**: write `.tmp` then `os.replace`.
- **Deferred init**: ASR model load + settings apply via `QTimer.singleShot(100)`
  after UI shown (avoid startup freeze).

### Session storage layout

```
~/.rectheword/sessions/
  index.json
  <YYYYMMDD-HHMMSS>/
    meta.json / mix.wav / mic.wav / sys.wav
    transcript.jsonl / refined.json / labels.json / minutes.md
```

## Language & Style

- Respond in Chinese.
- Code comments in English only where critical.
- Commit messages without Co-Authored-By.

# Theia — System Architecture

Voice assistant (Windows). Always-on microphone listening with a non-blocking
two-path design: a capture thread keeps the mic live at all times while a
separate worker thread executes commands, AI calls, and speech synthesis.

## Module map

| Module | Role |
|---|---|
| `server.py` | Web UI + WebSocket hub. Owns capture/worker loops, the full text pipeline, state broadcasting. Main entrypoint. |
| `stt.py` | faster-whisper model manager: auto-selection ladder (`large-v3-turbo` → `medium` → `small`) with hardware detection (CUDA/VRAM/RAM/CPU), compute-type selection, tunable decode config, and per-utterance confidence info (avg_logprob, no_speech_prob). Loaded async at boot. |
| `rnnoise_denoiser.py` | Native Xiph RNNoise integration via ctypes: 16 kHz ↔ 48 kHz resampling, 480-sample frame buffering, graceful fallback when the DLL is absent. |
| `llm.py` | Inception Mercury API client (`chat`, `chat_with_tools`, `correct_transcript`). |
| `normalize.py` | Phonetic/semantic correction layer: alias map, difflib sound-alike fixing, Indian song/artist lexicon, media-query garbage guard. |
| `session.py` | Multi-turn media context state machine (`media_context`, clarifiers, TTL). |
| `intent.py` | Coarse classifier (`classify`) + strict routing gate (`route`): tab / system / media / query / convo. |
| `actions.py` | Command table: media search+play, tab/window control, apps, folders, volume, negative search gate. Power verbs (restart/shutdown/lock/sleep) are removed and never dispatch. |
| `benchmark_stt.py` | Repeatable STT benchmark: fixture generation (edge-tts → wav), WER, latency, raw/noisy modes, cross-model comparison. |
| `computer_use_mcp.py` | Client for zavora-ai/computer-use-mcp (Rust, via `npx`). Primary fast PC-control engine. |
| `computer_fast.py` | FastComputer: screenshot cache, OCR label-clicking (via separate OCR client), hotkey/media/nav verbs. |
| `mcptools.py`, `mcp_server.py` | Legacy local MCP server (pyautogui + RapidOCR). Used as OCR provider + fallback. |
| `audio_bus.py` | AudioMixer (single output stream, mixes sources, `playback_rms` for echo suppression) + Source buffer queue with fade and drain tail. |
| `audio_io.py` | Listener (VAD + noise floor + noisereduce + echo ratio), SpeechMonitor. |
| `tts.py` | edge-tts → ffmpeg → AudioMixer source; sentence chunking; stop/barge-in handling; pygame fallback. |
| `music.py` | Local music engine (`~/Music`) via ffmpeg loop into the mixer. |
| `sfx.py` | Sound effects into the mixer. |
| `config.py` | Defaults + `waifu_config.json` load/save. |
| `web/` | Static UI: `index.html`, `style.css`, `app.js` (WebSocket client, activity feed, live states). |

## Threading model

- **aiohttp event-loop thread** — web server, WebSocket endpoints, out-queue drainer broadcasting to clients.
- **capture_loop (daemon thread)** — always listening (feedVAD + optional RNNoise denoising per block). It never blocks on Whisper: finished audio is pushed to `_audioq` and the mic resumes immediately. Live partial blocks are also fed to the live-transcription path.
- **decode_loop (daemon thread)** — consumes `_audioq`, transcribes (serialized via `_stt_lock`), applies wake/stop/ignored gating, then pushes `(text, utterance_id, t0)` to `_textq`.
- **worker_loop (daemon thread)** — consumes `_textq`: confidence-gated AI correction → phonetic normalization → pipeline → actions/LLM → speech. Never blocks capture.
- **Live partial threads** — lightweight overlapping-window transcriptions (`transcript_partial` events); never trigger commands.
- **TTS speech thread(s)** — one per spoken reply (serialized by `_speaking` + `_pending_text` drain).
- **Background action jobs** — media open (YouTube scrape + `webbrowser.open`), fire-and-forget computer commands (`_run_fast_command`).
- **Subprocesses** — computer-use-mcp (`npx`, Node), legacy MCP server (Python), ffmpeg (TTS, music, benchmark fixtures).
- **STT load thread** — model download/load at boot, server binds immediately.

## Key state

| Variable | Meaning |
|---|---|
| `_textq` | Raw transcript queue (capture → worker). |
| `_speaking` / `_pending_text` | Speech serialization; one speaker at a time. |
| `stop_flag` | Abort signal for current TTS playback. |
| `_confirm_action` | Removed — no power/session actions exist to confirm. |
| `session.media_context` | Last media title + accumulated clarifiers (TTL 120s). |
| `last_media_query` | Last resolved media query (for ordinal follow-ups). |
| `_utt_id` / `_live_buf` | Live-transcription window per utterance (overlapping partials before final). |
| `_latency` | Per-utterance stage timings (decode_done → intent → tts_start) logged as `[latency]`. |
| `_awoken` / `wake_enabled` | Wake-word latch; disabled by default (always listening). |
| `mic_enabled` / `agent_connected` | Mic and agent toggles from the UI. |

## Currently active pipeline (end-to-end)

```
Mic (always open)
  → RNNoise denoise per block (optional, native DLL) / noisereduce fallback
  → VAD + noise gate + echo suppression (audio_io.Listener)
  → capture_loop: live partial windows (transcript_partial → web UI) and _audioq
  → decode_loop: transcribe (STT model manager, auto-language, confidence info)
  → _textq (text + utterance_id + t0)
  → worker: confidence-gated AI correction (LLM.correct_transcript)
  → phonetic normalization (normalize.Normalizer.clean)
  → intent.route / classify
  → gates: registry allowlist, compound, tab, negative-search, audit dispatch
  → execute: media / music / tab / apps / computer / LLM chat
  → speak_reply → TTS chunks → AudioMixer sources → hardware drain
  → always return to "listening"
```

## Design invariants (important for future work)

1. The mic path must never block on AI, commands, or speech — execution is queued to the worker.
2. The displayed state is always `listening`; `idle` is intentionally no longer emitted.
3. While speaking, only the literal stop word interrupts; all other audio is discarded.
4. When wake mode is enabled, the wake word must appear before any command (latch, no timeout).
5. Every dispatched action passes an audit alignment check; mismatched domains are refused.
6. Power/session actions (restart/shutdown/lock/sleep) don't exist anywhere — they are stripped from registry, intent, handlers, relay allowlist, and docs.
7. YouTube playback is direct: query built with anti-Shorts/official-audio tags → parse full-length `videoRenderer` → open `/watch?v=...` (never a search landing page).
8. Computer control prefers computer-use-mcp (fast native); OCR-based clicking uses the legacy client as a separate provider, and falls back to it entirely if npx/Node is unavailable.
9. Battery of edge cases is handled in `normalize.py` and `actions.py`; unknown/gibberish media queries are refused with a "say it again" response.

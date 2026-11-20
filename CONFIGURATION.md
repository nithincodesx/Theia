# Theia — Configuration

File: `waifu_config.json` (created from `config.DEFAULTS`, merged section-by-section).
`config.load()` also reads `.env` for `INCEPTION_API_KEY`.

## Keys

| Section | Key | Default | Meaning |
|---|---|---|---|
| `stt` | `model` | `auto` | `auto` chooses the best model for detected hardware (`large-v3-turbo` on CUDA → `medium` on ≥6.5 GB RAM CPU → `small` emergency). Any explicit faster-whisper name overrides it. |
| | `device` | `auto` | `auto` → `cuda` when a usable CUDA device exists, else `cpu`. |
| | `compute_type` | `auto` | `auto` → `float16` (CUDA ≥16 GB RAM), `int8_float16` (low-VRAM CUDA), `int8` (CPU). |
| | `language` | `null` | `null` = auto-detect; `"en"` forces English and enables the prompt bias. |
| | `backend` | `whisper` | `whisper` = direct engine decode; `livekit` = LiveKit streaming STT (Silero VAD + `StreamAdapter`) for live partials, with the same local model committing finals. Falls back to `whisper` if LiveKit deps are missing. |
| | `ai_correct` | `{"enabled": true, "min_logprob": -0.55, "max_no_speech": 0.55}` | Confidence-aware AI correction: only transcripts below the confidence bar are sent to `LLM.correct_transcript`. A bare bool (legacy) means enabled with defaults. |
| | `decode` | `{"beam_size": 5, "best_of": 5, "temperature": 0.0, "vad_filter": true, "condition_on_previous_text": false, "no_speech_threshold": 0.6}` | faster-whisper decoding settings. |
| | `initial_prompt` | vocabulary seed | STT prompt bias (only applied when language == "en"). |
| `noise` | `enabled` | `true` | master noise-suppression switch |
| | `suppressor` | `rnnoise` | `rnnoise` (native DLL) or `noisereduce` (existing spectral gate). Falls back to `noisereduce` when the DLL is missing. |
| | `rnnoise_dll` | `""` | optional path to `rnnoise.dll`; also searched as `THEIA_RNNOISE_DLL`, `native/rnnoise.dll`, `./rnnoise.dll`. |
| | `noisereduce` | `true` | allow the noisereduce fallback/stage |
| `live_transcription` | `enabled` | `true` | streaming partial transcripts to the web UI |
| | `partial_interval_ms` | `700` | minimum time between partial transcription passes |
| | `window_max_s` | `10.0` | rolling window cap fed to partial transcription |
| | `min_partial_s` | `0.8` | audio length before partials begin |
| `llm` | `base_url` | `https://api.inceptionlabs.ai/v1` | Inception API |
| | `model` | `mercury-3` | brain model |
| | `temperature` | `0.8` | sampling temperature |
| | `max_history` | `24` | conversation history window |
| `tts` | `voice` | `en-US-JennyNeural` | edge-tts voice |
| | `rate` | `+8%` | speech rate |
| | `volume` | `+0%` | TTS gain |
| `sfx` | `enabled` | `false` | GIF/sfx playback |
| | `chance` | `0.25` | random giggle probability |
| `vad` | `aggressiveness` | `3` | WebRTC VAD level (1–3) |
| | `silence_after` | `0.6` | seconds of silence to end a complete turn |
| | `continuation_max` | `1.5` | max silence allowed while an utterance looks incomplete |
| | `max_duration` | `20.0` | max single utterance length |
| | `wake_timeout` | `30.0` | idle listening cap before re-arming |
| `wake` | `enabled` | `false` | wake-word gating (off = always listening) |
| | `word` | `now` | wake word (when enabled) |
| `persona` | `name` / `spice` / `user_name` | Theia / 1.0 / daddy | persona prompts |

## Runtime toggles (web console / WS)

- Mic on/off — `mic_enabled`
- Agent on/off — `agent_connected` (voice loop pauses while false)
- Wake on/off — `wake_enabled`
- Voice select — persisted to config
- Test voice — `test-voice` event

## Env

- `INCEPTION_API_KEY` — required for LLM (and AI STT correction).
- `THEIA_RNNOISE_DLL` — optional path to a native `rnnoise.dll`.

## Bootstrap timing

- Server binds immediately; STT model loads in a background thread (`[stt] model loaded` + hardware report in `server.log`).
- computer-use-mcp connects via `npx` (Node >=20 required); on failure, legacy MCP takes over.
- RNNoise is probed at boot; `[noise] rnnoise active/…` logged, fallback to noisereduce when unavailable.

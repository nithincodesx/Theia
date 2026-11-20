# Theia — open-source waifu voice assistant

A fully open-source voice assistant that talks to you as a flirty waifu
girlfriend. You talk, she hears you, thinks, and answers back out loud.

Pipeline: mic + VAD + optional RNNoise -> faster-whisper (STT, auto-selected model) -> Inception Mercury API (brain) -> edge-tts (voice)

## Highlights

- **STT model manager** — auto-selects `large-v3-turbo` (CUDA), `medium` (CPU ≥6.5 GB RAM) or `small` (emergency); compute type auto (`float16` / `int8_float16` / `int8`); manual override in `waifu_config.json`.
- **Live transcription** — your words appear in the web UI while you speak (`transcript_partial` events), then commit to a final transcript with the same utterance id. Partials never trigger actions.
- **LiveKit streaming STT** — optional `stt.backend: "livekit"` routes live partials through LiveKit's `StreamAdapter` + Silero VAD (local whisper engine; no cloud keys needed).
- **RNNoise** — optional native noise suppression via `rnnoise.dll` (16 kHz↔48 kHz resampled); falls back to the existing noisereduce when absent.
- **Adaptive endpointing** — 0.6 s silence for complete turns, up to 1.5 s continuation for incomplete phrasing.
- **Confidence-aware AI correction** — the LLM only rewrites uncertain transcripts.
- **Audio-first capture** — the mic never blocks on Whisper; decoding runs on a separate loop.
- **Benchmark harness** — `python benchmark_stt.py --models small medium` compares WER/latency per model and noise mode.

## Setup

1. Install Python 3.11+.
2. Create a venv and install dependencies:

   ```
   cd waifu-assistant
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. Add your Inception API key to `.env` (already done if you set it up here):

   ```
   INCEPTION_API_KEY=sk-...
   ```

4. Run:

   ```
   python main.py --selftest
   python main.py
   ```

Talk when you see "listening..." — it auto-detects speech, transcribes, replies
out loud, and keeps the conversation going.

## Tuning

The persona, spice level, voice, and model are configurable in `waifu_config.json`
(created on first run). All options:

| key | default | meaning |
|---|---|---|
| `persona.name` | `Theia` | her name |
| `persona.user_name` | `senpai` | what she calls you |
| `persona.spice` | `0.8` | 0.0 = sweet, 0.35+ = flirty, 0.7+ = full slutty waifu |
| `stt.model` | `auto` | `auto` = hardware-based selection; or explicit: tiny/base/small/medium/large-v3-turbo/large-v3 |
| `llm.model` | `mercury-3` | Inception model: `mercury-2`, `mercury-2.5`, `mercury-3` |
| `llm.temperature` | `0.8` | higher = more chaotic/flirty |
| `llm.base_url` | `https://api.inceptionlabs.ai/v1` | API base |
| `tts.voice` | `en-US-JennyNeural` | see below |
| `tts.rate` | `+12%` | speech speed |
| `vad.silence_after` | `0.6` | seconds of silence that ends a complete turn |
| `vad.continuation_max` | `1.5` | max wait while your phrase looks incomplete |
| `noise.suppressor` | `rnnoise` | `rnnoise` or `noisereduce` (fallback if DLL missing) |
| `live_transcription.enabled` | `true` | streaming partial transcripts in the web UI |

Quick spice override: `python main.py --spice 1.0`

## Voice options (edge-tts, free)

- `en-US-JennyNeural` — warm, energetic female (default)
- `en-US-AriaNeural` — confident female
- `en-GB-SoniaNeural` — British female
- `en-JP-NanamiNeural` — Japanese-accented English
- `en-AU-NatashaNeural` — Australian female

For truly anime voices, swap `tts.py` for Piper (local, MIT) with a downloadable
anime voicepack, or GPT-SoVITS for voice cloning.

## Notes

- The brain is the Inception Mercury API (paid usage per token, no local GPU needed).
- Speech recognition runs locally; first run downloads the Whisper STT model (`medium` ~1.5 GB, `small` ~460 MB).
- RNNoise is optional: drop a `rnnoise.dll` (GPLv3, from werman/noise-suppression-for-voice) into `native/` or set `THEIA_RNNOISE_DLL`. Without it Theia uses `noisereduce`.
- `webrtcvad-wheels` is used for voice activity detection.
- Keep `.env` private — it holds your API key.

# Theia — Workflow

## 1. Audio capture (always on)

- `capture_loop` runs continuously while `active()` is true (agent connected + WS clients + mic enabled).
- Per-block denoising: RNNoise (native DLL, 16 kHz↔48 kHz resampled) when available, otherwise noisereduce stationary removal (or both off).
- `Listener.listen()` uses WebRTC VAD (configurable aggressiveness), a running noise-floor RMS gate, speaker-echo ratio (`playback_rms`).
- **Adaptive endpointing**: `silence_after` (default 0.6 s) ends an utterance; if the live text still looks incomplete (trailing connectors, bare command words), the continuation window extends to `continuation_max` (default 1.5 s). A small tail is kept so final words are never cut.
- **Live transcription**: while speaking, overlapping windows (~700 ms cadence, capped at 10 s) are transcribed on a separate thread and streamed as `transcript_partial` events — the mic never waits for them, and they never trigger commands.
- Finished audio → `_audioq` → mic instantly resumes (never blocks on Whisper).

## 2. Decode → worker execution path

`decode_loop` consumes `_audioq` and transcribes with the resolved model (auto-selected per hardware), producing `(text, utterance_id, t0)` → `_textq`. `worker_loop` then:

1. **AI correction** (if `stt.ai_correct.enabled`): only when the utterance is not confident (`avg_logprob < min_logprob` or `no_speech_prob > max_no_speech`) is `LLM.correct_transcript` called — fixes homophones/artifacts, preserves transliterated Indian song/artist names; clear transcripts proceed directly. Failures fall back to raw text; raw and corrected text are both logged.
2. **Phonetic normalization**: `Normalizer.clean` — curated alias map (`articub`→`art deco`, `veezha veeran`→`vezham veeran`), then difflib token fixing against a lexicon (songs, artists, apps, sites, verbs, common words).
3. **Follow-up resolution**: `session.resolve_followup` — if the user answers a media clarification ("original", "slowed", "remix", …), merge into the locked media context: `"<title> <clarifiers> official audio"` and play immediately.
4. **Media query garbage guard**: if the text has ≥2 unknown tokens and no known entity → "Hmm, I couldn't make that out. Say it again, master." (no search).
5. **Compound commands** (before normal dispatch):
   - `open/launch/start (youtube|yt) and play <X>` → play X directly (alias-only cleaning preserves unknown song titles);
   - `search <X> and play (it|that|this)` → play X;
   - `play the next/previous song/video/one [on youtube/window/…]` → next/previous in the focused window (Shift+N / Shift+P, never a search).
6. **Listening-state commands**: `mute/unmute mic`, Theia quiet (`go to sleep`), `wake up` — handled pre-classification.
7. **Intent route/classify** (`intent.route` → tab / system / media / query / convo).
8. **Gates**:
   - tab domain → `actions.tab_control` only (window/browser automation; never search/media), gated via the registry allowlist (`_gate`/`_tab_action`);
   - every dispatched action (media/music/sfx/app/web/system/computer) is registry-validated before execution; unlisted pairs are refused ("I can't do that, master.");
   - negative search gate: system verbs (`close`, `stop`, `exit`, `minimize`, …) never reach the search branch;
   - audit dispatch: domain/kind mismatch → refused with corrective message.
9. **Dispatch** (see COMMAND-ROUTING.md) then **speak_reply** confirmation or LLM reply. Latency stages (`decode_done` → `intent` → `tts_start`) are logged per utterance.

## 3. Speech output

- `speak_reply` serializes: one speaker at a time; if busy, the new text becomes `_pending_text` and is drained when speech finishes.
- `TTS.speak_chunked` splits text into ≤160-char sentence chunks; each chunk streams edge-tts → ffmpeg → `AudioMixer` source.
- Source playback drains fully (fade + `DRAIN_TAIL` 0.15s) before teardown; the hardware stream is never killed mid-chunk.
- `stop_flag` aborts immediately on the "stop" word (checked every loop iteration).
- After speech: emit `assistant` event, return state to `listening`.

## 4. State / UI events

All events flow through `Hub.outq` → drainer → WebSocket clients:

| type | payload | meaning |
|---|---|---|
| `state` | `"listening"` (only) | current state (idle intentionally removed) |
| `user` | text + intent | user utterance entered pipeline |
| `assistant` | text | spoken reply |
| `ignored` | text | discarded (non-stop while speaking, wake-miss, etc.) |
| `awake` | — | wake latch engaged |
| `mic` / `agent` / `wake` | bool | toggle states |
| `voice` / `voices` / `config` | — | voice & config sync |

## 5. Forbidden actions

Power/session control is entirely disabled: `restart`, `reboot`, `shutdown`,
`lock`, `sleep` (PC suspend), and any equivalent phrasing are removed from the
registry, intent patterns, action handlers, LLM relay allowlist, and UI. They
never dispatch, and `actions.handle` returns `None` for them.

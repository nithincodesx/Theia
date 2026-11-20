# Theia — Audio bus

## Layout

```
edge-tts (TTS) ─┐
ffmpeg music    ─┼─► Source(buffer) ─► AudioMixer._cb ─► sounddevice OutputStream (44100 Hz, 2ch)
sfx            ─┘                    (mix, clip, fade)    (playback_rms → echo suppression)
```

- One `AudioMixer` owns the single `sd.OutputStream`; its callback mixes every registered `Source` each block (1024 frames), clips to int16, and updates `playback_rms` (EMA).
- Every producer registers a `Source` with the mixer, writes PCM (`s16le`, 44100, 2ch), and on completion calls `finish(fade_ms)` → `wait_empty()` → `close()` + `bus.remove(source)`.

## Source buffer mechanics

- Thread-safe `bytearray` under a lock; writes append, the mixer pulls.
- **Fade**: `finish(fade_ms)` marks the tail; the mixer applies a linear gain ramp over the last `fade_ms` of the source.
- **Drain tail** (`DRAIN_TAIL = 0.15s`): after the buffer is empty, `wait_empty` holds the source alive briefly so the hardware finishes playing before teardown — this is what prevents mid-sentence truncation.
- **Stop**: `should_stop` is checked every ~20ms during drain; abort returns immediately and the caller closes the source.

## Echo / self-interference suppression

- `Listener` and `SpeechMonitor` receive `ref=lambda: bus.playback_rms`.
- Blocks whose RMS < `played_rms * ECHO_RATIO` are treated as echo (self-speech) and excluded from speech detection.
- Noise floor gating: a block must exceed `max(floor*1.5, 0.002)` to count; quiet blocks update the floor (0.98 decay).

## TTS specifics

- `TTS` init pygame mixer as a fallback only; primary path streams via subprocess `ffmpeg` (mp3 pipe → s16le).
- Sentence chunking: `split_sentences` merges short sentences into ≤160-char chunks to reduce audio gaps.
- Interruption: `should_stop` checked each loop; barge-in (`SpeechMonitor`) supported but unused by default (strict stop-word gate instead).
- `speak_test` uses the direct (un-chunked) path for voice previews.

## Music engine

- `MusicEngine` scans `~/Music` (extensions: mp3/wav/m4a/flac/ogg/aac/wma/opus), plays sequentially via ffmpeg into its own `Source`.
- Pause/resume/stop/next/prev are safe under a lock; stop kills the ffmpeg proc after fading the source.
- Silent return when a query is provided — queries are handled by the media path instead.

## Known constraints

- The output stream is opened at module import of `Hub`; if no audio device exists, `AudioMixer` raises — the server fails fast at startup.
- `playback_rms` is a global-ish EMA; it is sufficient for echo gating but not a full AEC.
- All audio is int16 PCM; float paths (noise reduction) convert internally.

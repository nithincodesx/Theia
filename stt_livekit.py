import asyncio
import threading
import time

import numpy as np
from livekit import rtc
from livekit.agents import stt as lk_stt
from livekit.agents.stt import (
    STTCapabilities,
    SpeechData,
    SpeechEvent,
    SpeechEventType,
    StreamAdapter,
)
from livekit.plugins import silero


def frames_to_f32(buffer):
    frames = buffer if isinstance(buffer, list) else [buffer]
    chunks = []
    for f in frames:
        arr = np.frombuffer(
            f.data, dtype=np.int16, count=f.samples_per_channel * f.num_channels
        )
        if f.num_channels > 1:
            arr = arr.reshape(-1, f.num_channels).mean(axis=1)
        chunks.append(arr.astype(np.float32) / 32768.0)
    if not chunks:
        return np.empty(0, dtype=np.float32)
    return np.concatenate(chunks)


class LocalWhisperSTT(lk_stt.STT):
    """LiveKit STT plugin backed by our local faster-whisper engine."""

    def __init__(self, engine, language=None, lock=None):
        super().__init__(capabilities=STTCapabilities(streaming=True, interim_results=False))
        self._engine = engine
        self._language = language
        self._lock = lock

    @property
    def model(self):
        return self._engine.model_name

    @property
    def provider(self):
        return "local/faster-whisper"

    async def _recognize_impl(self, buffer, *, language=None, conn_options=None):
        audio = frames_to_f32(buffer)
        if len(audio) == 0:
            return SpeechEvent(
                type=SpeechEventType.FINAL_TRANSCRIPT,
                request_id="",
                alternatives=[],
                created_at=time.time(),
            )
        if self._lock is not None:
            async with self._lock:
                text, info = await asyncio.to_thread(self._engine.transcribe_final, audio)
        else:
            text, info = await asyncio.to_thread(self._engine.transcribe_final, audio)
        confidence = float(info.get("avg_logprob", -1.0))
        return SpeechEvent(
            type=SpeechEventType.FINAL_TRANSCRIPT,
            request_id="",
            alternatives=[SpeechData(
                text=text,
                language=(self._language or info.get("language") or "unknown"),
                start_time=0.0,
                end_time=info.get("duration", 0.0),
                confidence=confidence,
            )] if text else [],
            created_at=time.time(),
        )


class LiveKitStreamingSTT:
    """Thread-safe facade over LiveKit's StreamAdapter (Silero VAD + local Whisper)."""

    def __init__(self, engine, language=None, sample_rate=16000, lock=None):
        self._engine = engine
        self._sample_rate = sample_rate
        self._local = LocalWhisperSTT(engine, language=language, lock=lock)
        self._vad = silero.VAD.load(min_silence_duration=0.32, prefix_padding_duration=0.3)
        self._adapter = StreamAdapter(stt=self._local, vad=self._vad)
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)
        self._stream = None
        self._opened = False
        self._lock = threading.Lock()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def _on_event(self, ev):
        if ev.type == SpeechEventType.INTERIM_TRANSCRIPT:
            text = ev.alternatives[0].text if ev.alternatives else ""
            self._emit("interim", text)
        elif ev.type == SpeechEventType.FINAL_TRANSCRIPT:
            text = ev.alternatives[0].text if ev.alternatives else ""
            self._emit("final", text)
        elif ev.type == SpeechEventType.START_OF_SPEECH:
            self._emit("start")
        elif ev.type == SpeechEventType.END_OF_SPEECH:
            self._emit("end")

    def _emit(self, kind, text=None):
        _log(f"_emit: {kind} = {text!r}")
        cb = self._callbacks.get(kind)
        if cb:
            try:
                cb(text)
            except Exception as e:
                _log(f"callback error: {e}")

    def start(self, on_interim=None, on_final=None, on_start=None, on_end=None):
        self._callbacks = {
            "interim": on_interim,
            "final": on_final,
            "start": on_start,
            "end": on_end,
        }

        def _open():
            if self._opened:
                return
            self._opened = True
            self._stream = self._adapter.stream()
            self._loop.create_task(self._drain())

        self._loop.call_soon_threadsafe(_open)

    async def _drain(self):
        _log("drain started")
        try:
            async for ev in self._stream:
                _log(f"event: {ev.type} = {ev.alternatives}")
                self._on_event(ev)
        except Exception as e:
            _log(f"drain error: {e}")

    def push(self, pcm16):
        if not self._opened or self._stream is None:
            return
        data = np.asarray(pcm16, dtype=np.int16).tobytes()
        frame = rtc.AudioFrame(
            data=data,
            sample_rate=self._sample_rate,
            num_channels=1,
            samples_per_channel=len(pcm16),
        )
        self._loop.call_soon_threadsafe(lambda: self._stream.push_frame(frame))

    def end(self):
        if self._opened and self._stream is not None:
            self._loop.call_soon_threadsafe(self._stream.end_input)

    def close(self):
        if self._opened and self._stream is not None:
            try:
                self._loop.call_soon_threadsafe(self._stream.aclose)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

import threading
import time

import numpy as np
import sounddevice as sd

SAMPLERATE = 44100
CHANNELS = 2
BYTES_PER_FRAME = SAMPLERATE * CHANNELS * 2
FRAME_BYTES = CHANNELS * 2


class Source:
    DRAIN_TAIL = 0.15

    def __init__(self, bus):
        self.bus = bus
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._closed = False
        self._fading = False
        self._fade_frames = 0
        self._end_frames = 0
        self._consumed = 0

    def write(self, data):
        with self._lock:
            if not self._closed:
                self._buf += data

    def finish(self, fade_ms=500):
        with self._lock:
            self._fading = True
            self._fade_frames = max(1, int(fade_ms * SAMPLERATE / 1000))
            self._end_frames = len(self._buf) // FRAME_BYTES

    def empty(self):
        with self._lock:
            return len(self._buf) == 0

    def _take(self, need):
        with self._lock:
            take = min(int(need), len(self._buf))
            if take == 0:
                return b"", 1.0
            raw = bytes(self._buf[:take])
            del self._buf[:take]
            n = take // FRAME_BYTES
            start = self._consumed
            self._consumed += n
            if not self._fading or n <= 0:
                return raw, 1.0
            fade_start = max(0, self._end_frames - self._fade_frames)
            if start >= fade_start + self._fade_frames or fade_start >= start + n:
                return raw, 1.0
            k = np.arange(start, start + n, dtype=np.float64)
            gains = np.clip(1.0 - (k - fade_start) / self._fade_frames, 0.0, 1.0)
            return raw, gains

    def wait_empty(self, should_stop=None):
        while True:
            with self._lock:
                if len(self._buf) == 0:
                    break
            if should_stop is not None and should_stop():
                return False
            time.sleep(0.02)
        deadline = time.time() + self.DRAIN_TAIL
        while time.time() < deadline:
            if should_stop is not None and should_stop():
                return False
            time.sleep(0.01)
        return True

    def close(self):
        with self._lock:
            self._closed = True
            self._buf.clear()


class AudioMixer:
    def __init__(self, samplerate=SAMPLERATE, channels=CHANNELS, blocksize=1024):
        self.samplerate = samplerate
        self.channels = channels
        self._sources = []
        self._lock = threading.Lock()
        self.playback_rms = 0.0
        self.stream = sd.OutputStream(
            samplerate=samplerate,
            channels=channels,
            dtype="int16",
            blocksize=blocksize,
            callback=self._cb,
        )
        self.stream.start()

    def source(self):
        s = Source(self)
        with self._lock:
            self._sources.append(s)
        return s

    def remove(self, s):
        with self._lock:
            if s in self._sources:
                self._sources.remove(s)

    def _cb(self, outdata, frames, time_info, status):
        need = frames * 2 * self.channels
        mix = np.zeros((frames, self.channels), dtype=np.int64)
        with self._lock:
            for s in list(self._sources):
                raw, fade = s._take(need)
                if not raw:
                    continue
                arr = np.frombuffer(raw, dtype=np.int16).reshape(-1, self.channels)
                if isinstance(fade, np.ndarray):
                    arr = (arr.astype(np.float64) * fade.reshape(-1, 1)).astype(np.int64)
                mix[:arr.shape[0]] += arr
        clipped = np.clip(mix, -32768, 32767).astype(np.int16)
        outdata[:] = clipped
        rms = float(np.sqrt(np.mean((clipped.astype(np.float32) / 32768.0) ** 2)))
        self.playback_rms = 0.8 * self.playback_rms + 0.2 * rms

    def close(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass

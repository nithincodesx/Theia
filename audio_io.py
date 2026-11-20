import collections
import queue
import time

import numpy as np
import sounddevice as sd
import webrtcvad


class SpeechMonitor:
    ABS_SPEECH_FLOOR = 0.005
    ECHO_RATIO = 1.4
    MIN_STREAK = 2

    def __init__(self, rate=16000, chunk_ms=30, aggressiveness=2, ref=None, gain=4.0):
        self.rate = rate
        self.chunk = int(rate * chunk_ms / 1000)
        self.vad = webrtcvad.Vad(aggressiveness)
        self.q = queue.Queue()
        self.stream = None
        self._streak = 0
        self.ref = ref
        self.gain = gain

    def _cb(self, indata, frames, time_info, status):
        block = indata.copy()
        if self.gain != 1.0:
            block = np.clip(block.astype(np.int32) * self.gain, -32768, 32767).astype(np.int16)
        self.q.put(block)

    @staticmethod
    def _rms(f32):
        if len(f32) == 0:
            return 0.0
        return float(np.sqrt(np.mean(f32 ** 2)))

    def start(self):
        self.stream = sd.InputStream(
            samplerate=self.rate,
            channels=1,
            dtype="int16",
            blocksize=self.chunk,
            callback=self._cb,
        )
        self.stream.start()

    def hear_speech(self):
        heard = False
        while not self.q.empty():
            block = self.q.get_nowait().flatten()
            if len(block) < self.chunk:
                continue
            f32 = block.astype(np.float32) / 32768.0
            rms = self._rms(f32)
            played = self.ref() if self.ref is not None else None
            is_echo = played is not None and rms < played * self.ECHO_RATIO
            if not is_echo and rms > self.ABS_SPEECH_FLOOR and \
               self.vad.is_speech(block.tobytes(), self.rate):
                self._streak += 1
                if self._streak >= self.MIN_STREAK:
                    heard = True
            else:
                self._streak = 0
        return heard

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None


class Listener:
    MIN_SPEECH_BLOCKS = 5
    MIN_UTTERANCE_SECONDS = 0.35
    MIN_SPEECH_FRACTION = 0.3
    NOISE_CLIP_SECONDS = 0.5
    RMS_GATE_FACTOR = 1.3
    RMS_GATE_FLOOR = 0.005
    ECHO_RATIO = 1.8

    def __init__(self, rate=16000, chunk_ms=30, aggressiveness=2, ref=None, denoiser=None, gain=3.0):
        self.rate = rate
        self.chunk = int(rate * chunk_ms / 1000)
        self.vad = webrtcvad.Vad(aggressiveness)
        self.q = queue.Queue()
        self.noise_floor = None
        self.noise_clip = []
        self.ref = ref
        self.denoiser = denoiser
        self.gain = gain
        self.stream = None

    def _cb(self, indata, frames, time_info, status):
        block = indata.copy()
        if self.gain != 1.0:
            block = np.clip(block.astype(np.int32) * self.gain, -32768, 32767).astype(np.int16)
        self.q.put(block)

    @staticmethod
    def _rms(f32):
        if len(f32) == 0:
            return 0.0
        return float(np.sqrt(np.mean(f32 ** 2)))

    def _is_loud_enough(self, f32):
        rms = self._rms(f32)
        if self.noise_floor is None:
            self.noise_floor = rms
            return False
        threshold = max(self.noise_floor * self.RMS_GATE_FACTOR, self.RMS_GATE_FLOOR)
        if rms < threshold:
            self.noise_floor = 0.98 * self.noise_floor + 0.02 * rms
        return rms > threshold

    def _record_noise(self, f32):
        limit = int(self.rate * self.NOISE_CLIP_SECONDS)
        if len(self.noise_clip) < limit:
            self.noise_clip.extend(f32.tolist())

    def start(self):
        if self.stream is not None:
            return
        try:
            self.stream = sd.InputStream(
                samplerate=self.rate,
                channels=1,
                dtype="int16",
                blocksize=self.chunk,
                callback=self._cb,
            )
        except OSError as exc:
            raise RuntimeError("Could not open the microphone. Is a mic connected and allowed?") from exc
        self.stream.start()

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def listen(self, silence_after=1.2, max_duration=20.0, wake_timeout=30.0,
               alive=None, ref=None, block_cb=None):
        if ref is not None:
            self.ref = ref
        if self.stream is None:
            self.start()
        frames = []
        speaking = False
        last_speech = None
        start = time.time()
        speech_blocks = 0
        speech_blocks_total = 0
        try:
            while True:
                if alive is not None and not alive():
                    return None
                if not speaking and time.time() - start > wake_timeout:
                    return None
                try:
                    block = self.q.get(timeout=0.3)
                except queue.Empty:
                    if speaking and time.time() - last_speech > silence_after:
                        break
                    continue
                pcm = block.flatten()
                if len(pcm) < self.chunk:
                    continue
                if self.denoiser is not None:
                    cleaned = self.denoiser.process(pcm)
                    if len(cleaned) > 0:
                        pcm = cleaned
                f32 = pcm.astype(np.float32) / 32768.0
                loud_enough = self._is_loud_enough(f32)
                played = self.ref() if self.ref is not None else None
                is_echo = played is not None and self._rms(f32) < played * self.ECHO_RATIO
                is_speech = self.vad.is_speech(pcm.tobytes(), self.rate) and loud_enough and not is_echo
                if is_speech:
                    speech_blocks += 1
                    if not speaking and speech_blocks >= self.MIN_SPEECH_BLOCKS:
                        speaking = True
                    if speaking:
                        speech_blocks_total += 1
                        last_speech = time.time()
                        frames.append(pcm)
                        if block_cb is not None:
                            block_cb(f32)
                elif speaking:
                    frames.append(pcm)
                    if block_cb is not None:
                        block_cb(f32)
                    if time.time() - last_speech > silence_after or time.time() - start > max_duration:
                        break
                else:
                    speech_blocks = 0
                    if not frames:
                        self._record_noise(f32)
        finally:
            self.stop()
        if not frames:
            return None
        duration = len(frames) * self.chunk / self.rate
        fraction = speech_blocks_total / len(frames) if frames else 0.0
        if duration < self.MIN_UTTERANCE_SECONDS or fraction < self.MIN_SPEECH_FRACTION:
            return None
        return np.concatenate(frames).astype(np.float32) / 32768.0

import io
import wave

import numpy as np
import speech_recognition as sr

from stt import STT


def _to_pcm16(audio):
    a = np.asarray(audio)
    if a.dtype in (np.float32, np.float64):
        a = np.clip(a * 32768.0, -32768.0, 32767.0).astype(np.int16)
    elif a.dtype != np.int16:
        a = a.astype(np.int16)
    return a


def _trim(pcm16):
    hot = np.abs(pcm16.astype(np.int32)) > 120
    if not hot.any():
        return pcm16[:0]
    nz = np.flatnonzero(hot)
    lo = max(0, int(nz[0]) - 800)
    hi = min(len(pcm16), int(nz[-1]) + 800)
    return pcm16[lo:hi]


def _pcm16_to_wav(pcm16, sample_rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16.tobytes())
    return buf.getvalue()


class GoogleSTT:
    """Unofficial Google Web Speech API (keyless) with local fallback.

    - partial=True -> local tiny faster-whisper (lives, hot path, speculation)
    - partial=False / transcribe_final -> Google Web Speech (accurate), falls
      back to local small if the endpoint is unreachable or returns nothing.
    """

    DEFAULT_MODEL = "google-web-speech"

    def __init__(self, model=DEFAULT_MODEL, language="en-US", local_final="medium",
                 partial_model="medium", prompt=None, **kwargs):
        self.model_name = model
        self.language = "en-US" if language in (None, "en") else language
        self.prompt = prompt or ""
        self._recognizer = sr.Recognizer()
        self._local_partial = None
        self._local_final = None
        self._local_final_name = local_final
        self._partial_model_name = partial_model or "small"

    @property
    def provider(self):
        return "google/web-speech"

    def _get_local(self, final=False):
        if final:
            if self._local_final is None:
                self._local_final = STT(model=self._local_final_name, language="en")
            return self._local_final
        if self._local_partial is None:
            self._local_partial = STT(model=self._partial_model_name, language="en")
        return self._local_partial

    _FILLER = {"bye", "peace", "can you hear me", "can you", "mike mike mike",
                "in", "you", "what"}

    def _request_google(self, pcm16):
        audio = sr.AudioData(pcm16.tobytes(), 16000, 2)
        try:
            return self._recognizer.recognize_google(audio, language=self.language).strip()
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as exc:
            print(f"[google-stt] request error: {exc}")
            return ""

    @staticmethod
    def _info(text, duration):
        return {
            "language": "en",
            "language_probability": 1.0,
            "avg_logprob": 0.0 if text else -99.0,
            "no_speech_prob": 0.0 if text else 1.0,
            "duration": duration,
            "partial": False,
        }

    def transcribe_final(self, audio):
        pcm16 = _trim(_to_pcm16(audio))
        duration = float(len(pcm16)) / 16000.0
        if len(pcm16) == 0:
            return "", self._info("", duration)
        text = self._request_google(pcm16)
        if text and text.lower().strip(" .,!?") in self._FILLER:
            probe = self._get_local(final=True).transcribe_final(
                pcm16.astype(np.float32) / 32768.0
            )
            probe_text, probe_info = probe
            if not probe_text or probe_info.get("no_speech_prob", 0.0) > 0.7:
                return "", self._info("", duration)
        if text:
            return text, self._info(text, duration)
        text, info = self._get_local(final=True).transcribe_final(
            pcm16.astype(np.float32) / 32768.0
        )
        info["partial"] = False
        return text, info

    def transcribe_with_info(self, audio, partial=False):
        if partial:
            return self._get_local(final=False).transcribe_with_info(audio, partial=True)
        return self.transcribe_final(audio)

    def transcribe(self, audio, partial=False):
        text, _ = self.transcribe_with_info(audio, partial=partial)
        return text

import io
import json
import os
import subprocess
import tempfile
import wave

import numpy as np

from stt import STT


def _to_pcm16(audio):
    a = np.asarray(audio)
    if a.dtype in (np.float32, np.float64):
        a = np.clip(a * 32768.0, -32768.0, 32767.0).astype(np.int16)
    elif a.dtype != np.int16:
        a = a.astype(np.int16)
    return a


def _pcm16_to_wav(pcm16, sample_rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16.tobytes())
    return buf.getvalue()


class GroqSTT:
    """Whisper large-v3-turbo via Groq's free API with local fallback.

    - partial=True -> local tiny faster-whisper (lives, hot path, speculation)
    - partial=False / transcribe_final -> Groq (accurate), falls back to local
      medium if the API is unreachable or no key is configured.
    """

    DEFAULT_MODEL = "whisper-large-v3-turbo"
    API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

    def __init__(self, model=DEFAULT_MODEL, language="en", local_final="medium", prompt=None, **kwargs):
        self.model_name = model
        self.language = language or "en"
        self.prompt = prompt or ""
        self.api_key = os.environ.get("GROQ_API_KEY", "").strip()
        self._local_partial = None
        self._local_final = None
        self._local_final_name = local_final

    @property
    def provider(self):
        return "groq/whisper-large-v3-turbo"

    def _get_local(self, final=False):
        if final:
            if self._local_final is None:
                self._local_final = STT(model=self._local_final_name, language=self.language)
            return self._local_final
        if self._local_partial is None:
            self._local_partial = STT(model="tiny", language=self.language)
        return self._local_partial

    def _request_groq(self, wav_bytes):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tf.write(wav_bytes)
            path = tf.name
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            cmd = [
                "curl.exe", "-s", "-X", "POST", self.API_URL,
                "-H", f"Authorization: Bearer {self.api_key}",
                "-F", f"model={self.model_name}",
                "-F", f"language={self.language}",
                "-F", "response_format=json",
                "-F", f"file=@{path};type=audio/wav",
            ]
            if self.prompt:
                cmd += ["-F", f"prompt={self.prompt}"]
            out = subprocess.run(
                cmd, capture_output=True, timeout=20, creationflags=creationflags
            ).stdout.decode("utf-8", "replace")
            data = json.loads(out)
            return (data.get("text") or "").strip()
        finally:
            try:
                os.unlink(path)
            except Exception:
                pass

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
        pcm16 = _to_pcm16(audio)
        duration = float(len(pcm16)) / 16000.0
        if self.api_key:
            try:
                text = self._request_groq(_pcm16_to_wav(pcm16))
                if text:
                    return text, self._info(text, duration)
            except Exception as exc:
                print(f"[groq] error: {exc}; falling back to local")
        text, info = self._get_local(final=True).transcribe_final(audio)
        info["partial"] = False
        return text, info

    def transcribe_with_info(self, audio, partial=False):
        if partial:
            return self._get_local(final=False).transcribe_with_info(audio, partial=True)
        return self.transcribe_final(audio)

    def transcribe(self, audio, partial=False):
        text, _ = self.transcribe_with_info(audio, partial=partial)
        return text

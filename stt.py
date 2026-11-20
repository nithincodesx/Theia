import ctypes
import functools
import os

from faster_whisper import WhisperModel

MODEL_LADDER = ["large-v3-turbo", "medium", "small"]
_MODEL_ORDER = {name: i for i, name in enumerate(MODEL_LADDER)}


def _virtual_memory_gb():
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ullTotalPhys / (1024.0 ** 3)
    except Exception:
        return 8.0


def detect_hardware():
    cuda = 0
    try:
        import ctranslate2
        try:
            cuda = max(0, int(ctranslate2.get_cuda_device_count()))
        except Exception:
            cuda = 0
    except Exception:
        cuda = 0
    ram = _virtual_memory_gb()
    cpus = os.cpu_count() or 4
    if cuda >= 1 and ram >= 16.0:
        return {"device": "cuda", "compute_type": "float16", "cuda_count": cuda, "ram_gb": ram, "cpu_count": cpus}
    if cuda >= 1:
        return {"device": "cuda", "compute_type": "int8", "cuda_count": cuda, "ram_gb": ram, "cpu_count": cpus}
    return {"device": "cpu", "compute_type": "int8", "cuda_count": 0, "ram_gb": ram, "cpu_count": cpus}


def select_model(requested="auto", hw=None):
    hw = hw or detect_hardware()
    if requested and requested != "auto":
        return requested, hw["device"], hw["compute_type"]
    if hw["device"].startswith("cuda"):
        return "large-v3-turbo", hw["device"], hw["compute_type"]
    if hw["ram_gb"] >= 10.0:
        return "large-v3-turbo", "cpu", "int8"
    if hw["ram_gb"] >= 6.5:
        return "medium", "cpu", "int8"
    return "small", "cpu", "int8"


class STT:
    def __init__(self, model="auto", device="auto", compute_type="auto", language=None,
                 initial_prompt=None, decode=None, model_ladder=None, partial_model=""):
        hw = detect_hardware()
        self.hardware = hw
        resolved, dev, ctype = select_model(model, hw)
        if device and device != "auto":
            dev = device
        if compute_type and compute_type != "auto":
            ctype = compute_type
        self.model_name = resolved
        self.device = dev
        self.compute_type = ctype
        self.model = WhisperModel(resolved, device=dev, compute_type=ctype, cpu_threads=max(1, hw["cpu_count"]))
        self.language = language
        self.initial_prompt = initial_prompt
        self.decode = dict(decode or {})
        self._partial_model_name = (partial_model or "").strip() or ""
        self._partial_engine = None
        self._last_info = None
        self._last_raw = None
        self._last_text = None

    def _get_partial_engine(self):
        if not self._partial_model_name or self._partial_model_name == self.model_name:
            return self.model
        if self._partial_engine is None:
            self._partial_engine = WhisperModel(
                self._partial_model_name,
                device=self.device,
                compute_type=self.compute_type,
                cpu_threads=max(1, self.hardware["cpu_count"]),
            )
        return self._partial_engine

    # -- configuration ----------------------------------------------------

    @property
    def beam_size(self):
        return int(self.decode.get("beam_size", 5))

    @property
    def temperature(self):
        return float(self.decode.get("temperature", 0.0))

    @property
    def best_of(self):
        return int(self.decode.get("best_of", 5))

    @property
    def vad_filter(self):
        return bool(self.decode.get("vad_filter", True))

    @property
    def no_speech_threshold(self):
        v = self.decode.get("no_speech_threshold")
        return float(v) if v is not None else None

    @property
    def condition_on_previous_text(self):
        return bool(self.decode.get("condition_on_previous_text", False))

    # -- transcription ----------------------------------------------------

    def _kwargs(self, partial=False):
        if partial:
            return {
                "language": self.language,
                "beam_size": 1,
                "temperature": 0.0,
                "vad_filter": False,
                "condition_on_previous_text": False,
                "no_speech_threshold": None,
            }
        kw = {
            "language": self.language,
            "beam_size": self.beam_size,
            "temperature": self.temperature,
            "vad_filter": self.vad_filter,
            "condition_on_previous_text": self.condition_on_previous_text,
        }
        if self.best_of > 1 and self.temperature > 0:
            kw["best_of"] = self.best_of
        if self.no_speech_threshold is not None:
            kw["no_speech_threshold"] = self.no_speech_threshold
        if self.initial_prompt and (self.language is None or self.language == "en"):
            kw["initial_prompt"] = self.initial_prompt
        return kw

    def transcribe_with_info(self, audio, partial=False):
        engine = self._get_partial_engine() if partial else self.model
        segments, info = engine.transcribe(audio, **self._kwargs(partial))
        text = " ".join(s.text.strip() for s in segments).strip()
        detail = {
            "language": getattr(info, "language", None),
            "language_probability": float(getattr(info, "language_probability", 0.0) or 0.0),
            "avg_logprob": float(getattr(info, "avg_logprob", -99.0) or -99.0),
            "no_speech_prob": float(getattr(info, "no_speech_prob", 1.0) or 1.0),
            "duration": float(getattr(info, "duration", 0.0) or 0.0),
            "partial": bool(partial),
        }
        self._last_info = detail
        self._last_raw = audio
        self._last_text = text
        return text, detail

    def transcribe_final(self, audio):
        kw = {
            "language": self.language,
            "beam_size": self.beam_size,
            "temperature": self.temperature,
            "vad_filter": self.vad_filter,
            "condition_on_previous_text": self.condition_on_previous_text,
        }
        if self.best_of > 1 and self.temperature > 0:
            kw["best_of"] = self.best_of
        if self.no_speech_threshold is not None:
            kw["no_speech_threshold"] = self.no_speech_threshold
        if self.initial_prompt and (self.language is None or self.language == "en"):
            kw["initial_prompt"] = self.initial_prompt
        segments, info = self.model.transcribe(audio, **kw)
        text = " ".join(s.text.strip() for s in segments).strip()
        detail = {
            "language": getattr(info, "language", None),
            "language_probability": float(getattr(info, "language_probability", 0.0) or 0.0),
            "avg_logprob": float(getattr(info, "avg_logprob", -99.0) or -99.0),
            "no_speech_prob": float(getattr(info, "no_speech_prob", 1.0) or 1.0),
            "duration": float(getattr(info, "duration", 0.0) or 0.0),
            "partial": False,
        }
        self._last_info = detail
        self._last_raw = audio
        self._last_text = text
        return text, detail

    def transcribe(self, audio, partial=False):
        text, _ = self.transcribe_with_info(audio, partial=partial)
        return text

    @property
    def last_info(self):
        return self._last_info

    @property
    def last_raw(self):
        return self._last_raw

    @property
    def last_text(self):
        return self._last_text

    def confidence_ok(self, threshold=-0.55, max_no_speech=0.55):
        info = self._last_info or {}
        if info.get("no_speech_prob", 0.0) > max_no_speech:
            return False
        return info.get("avg_logprob", -99.0) >= threshold

    def __repr__(self):
        return f"STT(model={self.model_name!r}, device={self.device!r}, compute_type={self.compute_type!r})"

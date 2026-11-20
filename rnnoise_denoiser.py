import ctypes
import os
import pathlib

import numpy as np

RNNOISE_RATE = 48000
RNNOISE_FRAME = 480
TARGET_RATE = 16000
_RATE_FACTOR = RNNOISE_RATE // TARGET_RATE


def _candidate_paths(configured=""):
    root = pathlib.Path(__file__).parent
    env = os.environ.get("THEIA_RNNOISE_DLL", "")
    cands = [
        configured,
        env,
        str(root / "native" / "rnnoise.dll"),
        str(root / "rnnoise.dll"),
    ]
    out = []
    for c in cands:
        if c and c not in out:
            out.append(c)
    return out


def find_rnnoise_dll(configured=""):
    for c in _candidate_paths(configured):
        p = pathlib.Path(c)
        if p.exists() and p.is_file():
            return str(p)
    return None


class RNNoiseDenoiser:
    def __init__(self, dll_path=None):
        self._lib = None
        self._state = None
        self._frame_size = 0
        self._buf = np.empty(0, dtype=np.float32)
        self._dll_path = None
        path = find_rnnoise_dll(dll_path or "")
        if path is None:
            self.available = False
            self.error = "rnnoise.dll not found (searched configured path, THEIA_RNNOISE_DLL, native/rnnoise.dll)"
            return
        self.available = False
        self.error = ""
        try:
            lib = ctypes.CDLL(path)
            lib.rnnoise_get_frame_size.restype = ctypes.c_int
            size = int(lib.rnnoise_get_frame_size())
            if size <= 0:
                self.error = "rnnoise_get_frame_size returned invalid value"
                return
            lib.rnnoise_create.restype = ctypes.c_void_p
            lib.rnnoise_create.argtypes = [ctypes.POINTER(ctypes.c_int)]
            lib.rnnoise_process_frame.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_float),
                ctypes.POINTER(ctypes.c_float),
            ]
            lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
            self._frame_size = size
            self._lib = lib
            self._state = lib.rnnoise_create(None)
            if not self._state:
                self.error = "rnnoise_create failed"
                return
            self._dll_path = path
            self.available = True
        except Exception as exc:
            self.error = f"failed to load rnnoise.dll: {exc}"

    # -- resampling helpers ------------------------------------------------

    @staticmethod
    def _upsample3(x):
        n = len(x)
        if n == 0:
            return np.empty(0, dtype=np.float32)
        out_len = n * 3
        out = np.empty(out_len, dtype=np.float32)
        out[0::3] = x
        if n > 1:
            interp = np.empty(n, dtype=np.float32)
            interp[:-1] = (x[:-1] + x[1:]) * 0.5
            interp[-1] = x[-1]
            out[1::3] = interp
            out[2::3] = interp
        else:
            out[1::3] = x[0]
            out[2::3] = x[0]
        return out

    @staticmethod
    def _downsample3(x):
        n = len(x) - (len(x) % 3)
        if n == 0:
            return np.empty(0, dtype=np.float32)
        return x[:n].reshape(-1, 3).mean(axis=1).astype(np.float32)

    def process(self, pcm16):
        if not self.available:
            return np.asarray(pcm16, dtype=np.int16)
        f32 = np.asarray(pcm16, dtype=np.float32) / 32768.0
        if self._buf.size:
            f32 = np.concatenate([self._buf, f32])
            self._buf = np.empty(0, dtype=np.float32)
        up = self._upsample3(f32)
        frame = self._frame_size
        usable = len(up) - (len(up) % frame)
        if usable <= 0:
            self._buf = up
            return np.empty(0, dtype=np.int16)
        n_frames = usable // frame
        out = np.empty(usable, dtype=np.float32)
        in_addr = int(up[:usable].ctypes.data)
        out_addr = int(out.ctypes.data)
        PTR = ctypes.POINTER(ctypes.c_float)
        for i in range(n_frames):
            off = i * frame * 4
            self._lib.rnnoise_process_frame(
                self._state,
                ctypes.cast(out_addr + off, PTR),
                ctypes.cast(in_addr + off, PTR),
            )
        self._buf = up[usable:]
        down = self._downsample3(out)
        return np.clip(down * 32768.0, -32768.0, 32767.0).astype(np.int16)

    def reset(self):
        if self.available:
            self._buf = np.empty(0, dtype=np.float32)
            if self._state:
                try:
                    self._lib.rnnoise_destroy(self._state)
                except Exception:
                    pass
            try:
                self._state = self._lib.rnnoise_create(None)
            except Exception:
                self._state = None

    def close(self):
        if self.available and self._state:
            try:
                self._lib.rnnoise_destroy(self._state)
            except Exception:
                pass
        self._state = None
        self._lib = None
        self.available = False

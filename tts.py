import asyncio
import hashlib
import os
import queue as _queue
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict

import edge_tts
import pygame
import re

from audio_bus import AudioMixer

_CACHE_MAX_ITEMS = 1024
_CACHE_MAX_BYTES = 512 * 1024 * 1024
_WRITE_CHUNK = 32768
_CACHE_DIR_ENV = "WAI_TTS_CACHE_DIR"
_DEFAULT_CACHE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "waifu-assistant",
    "tts-cache",
)


def split_sentences(text, limit=160):
    pieces = re.split(r"(?<=[.!?…])\s+", text.strip())
    parts = []
    current = ""
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        while len(piece) > limit:
            cut = piece.rfind(" ", 0, limit)
            if cut < 0:
                cut = limit
            parts.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if not piece:
            continue
        if current and len(current) + len(piece) + 1 > limit:
            parts.append(current)
            current = piece
        else:
            current = (current + " " + piece).strip() if current else piece
    if current:
        parts.append(current)
    return parts or [text]


class TTS:
    def __init__(self, voice="en-US-JennyNeural", rate="+12%", volume="+0%", bus=None):
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.fade_ms = 900
        self.bus = bus or AudioMixer()
        try:
            pygame.mixer.init()
        except Exception as exc:
            raise RuntimeError("Could not init audio output. Check your speakers/audio device.") from exc
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._cache_lock = threading.Lock()
        self._cache_dir = os.environ.get(_CACHE_DIR_ENV, _DEFAULT_CACHE_DIR)
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
        except Exception:
            self._cache_dir = None
        self._eng = None
        self._eng_lock = threading.Lock()

    def set_voice(self, voice, rate=None, volume=None):
        self.voice = voice
        if rate:
            self.rate = rate
        if volume:
            self.volume = volume

    # -- persistent ffmpeg engine ------------------------------------------

    def _ensure_engine(self):
        with self._eng_lock:
            if self._eng is not None and self._eng["proc"].poll() is None:
                return True
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            proc = subprocess.Popen(
                ["ffmpeg", "-loglevel", "error", "-f", "mp3", "-i", "pipe:0",
                 "-f", "s16le", "-ar", "44100", "-ac", "2", "pipe:1"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            src = self.bus.source()
            eng = {"proc": proc, "src": src, "stop": False}
            self._eng = eng
            threading.Thread(target=self._pump_engine, args=(eng,), daemon=True).start()
            return True

    def _pump_engine(self, eng):
        try:
            while not eng["stop"]:
                data = eng["proc"].stdout.read(8192)
                if not data:
                    break
                eng["src"].write(data)
        except Exception:
            pass

    def _teardown_engine(self):
        with self._eng_lock:
            eng = self._eng
            self._eng = None
            if eng is None:
                return
            eng["stop"] = True
            try:
                eng["proc"].kill()
            except Exception:
                pass
            try:
                eng["src"].close()
            except Exception:
                pass
            self.bus.remove(eng["src"])

    # -- audio cache -------------------------------------------------------

    def _cache_path(self, key):
        if not self._cache_dir:
            return None
        h = hashlib.sha1(repr(key).encode("utf-8", "replace")).hexdigest()
        return os.path.join(self._cache_dir, h + ".mp3")

    def _cache_get(self, key):
        with self._cache_lock:
            data = self._cache.get(key)
            if data is not None:
                return data
        p = self._cache_path(key)
        if p and os.path.exists(p):
            try:
                with open(p, "rb") as f:
                    data = f.read()
                if data:
                    self._cache_put(key, data)
                    return data
            except Exception:
                pass
        return None

    def _cache_put(self, key, data):
        with self._cache_lock:
            if key in self._cache:
                self._cache_bytes -= len(self._cache[key])
                del self._cache[key]
            self._cache[key] = data
            self._cache_bytes += len(data)
            while len(self._cache) > _CACHE_MAX_ITEMS or self._cache_bytes > _CACHE_MAX_BYTES:
                _k, v = self._cache.popitem(last=False)
                self._cache_bytes -= len(v)
        p = self._cache_path(key)
        if p:
            try:
                with open(p, "wb") as f:
                    f.write(data)
            except Exception:
                pass

    def _synth_mp3(self, text):
        chunks = []

        async def gen():
            comm = edge_tts.Communicate(text, self.voice, rate=self.rate, volume=self.volume)
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])

        try:
            asyncio.run(gen())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                loop.run_until_complete(gen())
            finally:
                asyncio.set_event_loop(None)
                loop.close()
        return b"".join(chunks)

    def _play_bytes(self, data, should_stop, barge_in, fade_ms):
        self._ensure_engine()
        eng = self._eng
        try:
            for i in range(0, len(data), _WRITE_CHUNK):
                eng["proc"].stdin.write(data[i:i + _WRITE_CHUNK])
            eng["proc"].stdin.flush()
            eng["proc"].stdin.close()
            eng["proc"].wait(timeout=30)
        except Exception:
            self._teardown_engine()
            raise
        src = eng["src"]
        src.finish(self.fade_ms if fade_ms is None else fade_ms)

        def _stop():
            if should_stop is not None and should_stop():
                return True
            if barge_in is not None and barge_in.hear_speech():
                return True
            return False

        if not src.wait_empty(should_stop=_stop):
            self._teardown_engine()

    def prewarm(self, phrases):
        def job():
            for p in phrases:
                key = (self.voice, self.rate, self.volume, p)
                if self._cache_get(key) is not None:
                    continue
                try:
                    data = self._synth_mp3(p)
                    if data:
                        self._cache_put(key, data)
                except Exception:
                    pass

        threading.Thread(target=job, daemon=True).start()

    def speak(self, text, should_stop=None, barge_in=None, fade_ms=None):
        key = (self.voice, self.rate, self.volume, text)
        cached = self._cache_get(key)
        try:
            if cached is not None:
                self._play_bytes(cached, should_stop, barge_in, fade_ms)
            else:
                data = self._synth_mp3(text)
                if data:
                    self._cache_put(key, data)
                self._play_bytes(data, should_stop, barge_in, fade_ms)
        except Exception as exc:
            print(f"[tts] failed ({exc}); falling back to file playback")
            self._speak_file(text, should_stop, barge_in)

    def speak_chunked(self, text, should_stop=None, barge_in=None):
        chunks = split_sentences(text)
        last = len(chunks) - 1
        for i, chunk in enumerate(chunks):
            if should_stop is not None and should_stop():
                break
            fade = self.fade_ms if i == last else 0
            self.speak(chunk, should_stop, barge_in, fade_ms=fade)
            if should_stop is not None and should_stop():
                break

    def _speak_stream(self, text, should_stop, barge_in, fade_ms=None, cache_key=None):
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        proc = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-f", "mp3", "-i", "pipe:0",
             "-f", "s16le", "-ar", "44100", "-ac", "2", "pipe:1"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        q = _queue.Queue()
        sentinel = object()
        src = self.bus.source()
        collected = bytearray() if cache_key else None

        def feed():
            try:
                async def gen():
                    comm = edge_tts.Communicate(text, self.voice, rate=self.rate, volume=self.volume)
                    async for chunk in comm.stream():
                        if chunk["type"] == "audio":
                            proc.stdin.write(chunk["data"])
                            if collected is not None:
                                collected.extend(chunk["data"])
                asyncio.run(gen())
            except Exception:
                pass
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        def read_pipe():
            try:
                while True:
                    data = proc.stdout.read(8192)
                    if not data:
                        break
                    q.put(data)
            except Exception:
                pass
            finally:
                q.put(sentinel)

        threading.Thread(target=feed, daemon=True).start()
        threading.Thread(target=read_pipe, daemon=True).start()

        interrupted = False
        while True:
            if should_stop is not None and should_stop():
                interrupted = True
                break
            try:
                data = q.get(timeout=0.05)
            except _queue.Empty:
                if barge_in is not None and barge_in.hear_speech():
                    interrupted = True
                    break
                continue
            if data is sentinel:
                break
            src.write(data)
        if interrupted:
            while True:
                try:
                    q.get_nowait()
                except _queue.Empty:
                    break

        if not interrupted:
            src.finish(self.fade_ms if fade_ms is None else fade_ms)
            drained = src.wait_empty(should_stop=should_stop)
            if not drained:
                interrupted = True
            elif collected is not None and len(collected) > 0:
                self._cache_put(cache_key, bytes(collected))

        try:
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        src.close()
        self.bus.remove(src)

    def _speak_file(self, text, should_stop, barge_in):
        tmp = os.path.join(tempfile.gettempdir(), "yui_reply.mp3")
        asyncio.run(edge_tts.Communicate(text, self.voice, rate=self.rate, volume=self.volume).save(tmp))
        pygame.mixer.music.load(tmp)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            if should_stop is not None and should_stop():
                pygame.mixer.music.stop()
                break
            if barge_in is not None and barge_in.hear_speech():
                pygame.mixer.music.stop()
                break
            pygame.time.wait(50)
        pygame.mixer.music.unload()

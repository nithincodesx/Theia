import os
import subprocess
import threading
import time

EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma", ".opus"}


class MusicEngine:
    def __init__(self, bus, folder=None):
        self.bus = bus
        self.folder = folder or os.path.expanduser("~/Music")
        self._lock = threading.Lock()
        self._files = []
        self._idx = 0
        self._playing = False
        self._paused = False
        self._proc = None
        self._src = None
        self._thread = None

    def _scan(self):
        if not os.path.isdir(self.folder):
            return []
        found = []
        for root, _dirs, names in os.walk(self.folder):
            for name in names:
                if os.path.splitext(name)[1].lower() in EXTS:
                    found.append(os.path.join(root, name))
        return sorted(found)

    def _stop_current(self):
        proc = None
        src = None
        with self._lock:
            proc = self._proc
            src = self._src
            self._proc = None
            self._src = None
        if src is not None:
            src.finish(350)
            time.sleep(0.4)
            src.close()
            self.bus.remove(src)
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

    def play(self, query=None):
        if query:
            return None
        files = self._scan()
        if not files:
            return "No music found in your Music folder, sweetheart."
        self._stop_current()
        with self._lock:
            self._files = files
            self._idx = 0
            self._playing = True
            self._paused = False
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return "Playing your music, daddy."

    def _run(self):
        try:
            while True:
                with self._lock:
                    if not self._playing:
                        return
                    if self._idx >= len(self._files):
                        self._playing = False
                        return
                    path = self._files[self._idx]
                    paused = self._paused
                if paused:
                    time.sleep(0.05)
                    continue
                self._play_file(path)
                with self._lock:
                    self._idx += 1
        finally:
            self._stop_current()

    def _play_file(self, path):
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            proc = subprocess.Popen(
                ["ffmpeg", "-loglevel", "error", "-i", path,
                 "-f", "s16le", "-ar", "44100", "-ac", "2", "pipe:1"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except Exception:
            return
        src = self.bus.source()
        with self._lock:
            self._proc = proc
            self._src = src
        try:
            while True:
                with self._lock:
                    if not self._playing:
                        return
                    paused = self._paused
                if paused:
                    time.sleep(0.05)
                    continue
                data = proc.stdout.read(8192)
                if not data:
                    break
                src.write(data)
            while True:
                with self._lock:
                    if not self._playing:
                        return
                if src.empty():
                    break
                time.sleep(0.03)
        except Exception:
            pass
        finally:
            with self._lock:
                self._proc = None
                self._src = None
            try:
                proc.kill()
            except Exception:
                pass
            src.close()
            self.bus.remove(src)

    def pause(self):
        with self._lock:
            self._paused = True
        return "Paused, sweetheart."

    def resume(self):
        with self._lock:
            self._paused = False
        return "Resuming, babe."

    def next_track(self):
        self._stop_current()
        with self._lock:
            if self._playing and self._files:
                self._idx = min(self._idx + 1, len(self._files) - 1)
        return "Skipping to the next track, honey."

    def prev_track(self):
        self._stop_current()
        with self._lock:
            if self._playing and self._files:
                self._idx = max(self._idx - 1, 0)
        return "Going back a track, babe."

    def stop(self):
        with self._lock:
            self._playing = False
        self._stop_current()
        return "Stopped, master."

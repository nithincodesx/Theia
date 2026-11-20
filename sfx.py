import os
import random

import pygame


class Sfx:
    def __init__(self, folder="sfx", enabled=True, chance=0.25):
        self.enabled = enabled
        self.chance = chance
        self.clips = {}
        for name in ("giggle", "moan_soft", "moan_warm", "gasp"):
            path = os.path.join(folder, name + ".mp3")
            if os.path.exists(path):
                self.clips[name] = path

    def maybe(self, reply, should_stop=None):
        if not self.enabled or not self.clips or random.random() > self.chance:
            return
        low = reply.lower()
        if any(w in low for w in ("mmm", "moan", "daddy", "master", "kiss", "touch", "bed", "want", "close")):
            kind = random.choice(["moan_soft", "moan_warm"])
        elif any(w in low for w in ("laugh", "giggle", "funny", "hehe", "cute", "tease")):
            kind = "giggle"
        else:
            kind = random.choice(list(self.clips))
        self.play(kind, should_stop)

    def play(self, kind, should_stop=None):
        path = self.clips.get(kind)
        if not path:
            return
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            if should_stop is not None and should_stop():
                pygame.mixer.music.stop()
                break
            pygame.time.wait(40)

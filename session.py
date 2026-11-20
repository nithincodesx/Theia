import re
import time

_FUNCTION_WORDS = frozenset({
    "the", "a", "an", "please", "can", "could", "i", "you", "me", "my",
    "give", "play", "want", "would", "just", "let's", "lets", "maybe",
    "yes", "yeah", "ok", "okay", "umm", "um", "uh", "like", "that",
})

_CLARIFIERS = {
    "original": "original",
    "official": "official",
    "official audio": "official audio",
    "album": "album version",
    "album version": "album version",
    "slowed": "slowed",
    "slowed reverb": "slowed + reverb",
    "slowed and reverb": "slowed + reverb",
    "slowed down": "slowed",
    "slow": "slowed",
    "reverb": "slowed + reverb",
    "sped up": "sped up",
    "nightcore": "nightcore",
    "remix": "remix",
    "remastered": "remastered",
    "remaster": "remastered",
    "live": "live version",
    "live version": "live version",
    "acoustic": "acoustic",
    "cover": "cover",
    "full": "full length",
    "full length": "full length",
    "full version": "full length",
    "extended": "extended",
    "extended version": "extended",
    "radio edit": "radio edit",
    "instrumental": "instrumental",
    "8d": "8d",
    "audio": "official audio",
    "music video": "music video",
    "lyric video": "lyric video",
    "lyrics": "lyric video",
    "unplugged": "unplugged",
    "demo": "demo",
    "clean": "clean",
    "explicit": "explicit",
}

_MAX_CLARIFIERS = 4
_SUFFIX = "official audio"


class SessionState:
    def __init__(self, ttl=120.0):
        self.ttl = float(ttl)
        self.media_context = None
        self.state = "idle"
        self.pending_parameters = {}

    def register_media(self, title):
        title = (title or "").strip()
        if not title:
            self.clear_media()
            return
        self.media_context = {"title": title, "clarifiers": [], "at": time.time()}
        self.state = "awaiting_clarification"
        self.pending_parameters = {"media_title": title}

    def clear_media(self):
        self.media_context = None
        self.state = "idle"
        self.pending_parameters = {}

    @staticmethod
    def _normalize(text):
        low = text.lower().strip()
        low = re.sub(r"[^a-z0-9\+ ]", " ", low)
        return re.sub(r"\s+", " ", low).strip()

    def _parse_clarifier(self, text):
        tokens = self._normalize(text).split(" ")
        if not tokens or tokens == [""]:
            return None
        while tokens and tokens[0] in _FUNCTION_WORDS:
            tokens.pop(0)
        while tokens and tokens[-1] in _FUNCTION_WORDS:
            tokens.pop()
        if not tokens:
            return None
        return _CLARIFIERS.get(" ".join(tokens))

    def resolve_followup(self, text):
        if self.media_context is None:
            return None
        if time.time() - self.media_context.get("at", 0) > self.ttl:
            self.clear_media()
            return None
        clar = self._parse_clarifier(text)
        if clar is None:
            return None
        base = self.media_context["title"]
        existing = list(self.media_context.get("clarifiers", []))
        if clar not in existing:
            existing.append(clar)
            existing = existing[:_MAX_CLARIFIERS]
        self.media_context["clarifiers"] = existing
        self.media_context["at"] = time.time()
        parts = [base] + existing
        if _SUFFIX not in existing:
            parts.append(_SUFFIX)
        return " ".join(parts)

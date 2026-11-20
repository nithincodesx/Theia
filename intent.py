import re

_MUSIC_PATTERNS = [
    r"\b(?:play|start|shuffle)\b",
    r"\bpause\b",
    r"\bresume\b",
    r"\b(?:next|previous|prev|skip)\b",
    r"\bstop\s+(?:the\s+)?(?:music|song|audio|playback)\b",
    r"\b(?:music|song|album|playlist|track|tune|melody)\b",
    r"\b(?:volume|mute|unmute|un\s+mute|louder|quieter)\b",
]

_COMPUTER_PATTERNS = [
    r"\b(?:click|double\s*click|dbl\s*click|type|write|dictate|press|scroll)\b",
    r"\b(?:open|close|launch|minimize|maximize|screenshot)\b",
    r"\b(?:app|application|program|folder|window|tab|pc|computer|screen|system|browser)\b",
    r"\b(?:youtube|github|reddit|gmail|maps|drive|docs|spotify|twitch|google|wikipedia|netflix|amazon)\b",
    r"\bsearch\b",
    r"\blook\s+up\b",
    r"\bask\s+(?:google|the\s+internet|the\s+web)\b",
    r"\b(?:switch|change)\s+(?:to|into)\b",
    r"\b(?:mute|unmute|un\s+mute)\s+(?:the\s+)?(?:mic|microphone)\b",
    r"\b(?:go\s+to\s+sleep|sleep\s+now|be\s+quiet|wake\s+up|wake)\b",
    r"\b(?:downloads|documents|desktop|pictures|videos)\b",
]

_MUSIC_RE = [re.compile(p) for p in _MUSIC_PATTERNS]
_COMPUTER_RE = [re.compile(p) for p in _COMPUTER_PATTERNS]

STOP_RE = re.compile(r"\b(?:please\s+)?(?:theia|lana\s+)?stop\b(?!\s+(?:the\s+)?(?:music|song|audio|playback))")


def is_stop(text):
    return bool(STOP_RE.search(text))


def classify(text):
    t = text.lower().strip()
    if is_stop(t):
        return "stop"
    music = sum(1 for p in _MUSIC_RE if p.search(t))
    computer = sum(1 for p in _COMPUTER_RE if p.search(t))
    if music >= computer and music > 0:
        return "music"
    if computer > 0:
        return "computer"
    return "conversation"


_TAB_RE = re.compile(
    r"\b(?:close|minimize|maximize|restore|switch)\s+(?:the\s+)?(?:tab|window|browser)\b"
    r"|\b(?:close|minimize|maximize|switch)\s+(?:the\s+)?(?:youtube|yt|chrome|edge|"
    r"browser|github|reddit|spotify|twitch|discord|netflix|gmail)(?:\s+(?:tab|window))\b"
    r"|\b(?:next|previous|prev|new|reopen|last|open)\s+tab\b"
    r"|\b(?:new|another|other)\s+window\b"
    r"|\b(?:move|go)\s+(?:to\s+)?(?:next|other|another)\s+tab\b",
    re.IGNORECASE,
)

_SYSTEM_RE = re.compile(
    r"\b(?:exit|quit)\b",
    re.IGNORECASE,
)

_QUERY_RE = re.compile(
    r"\b(?:search|look\s+up|ask\s+(?:google|the\s+internet|the\s+web))\b",
    re.IGNORECASE,
)

_MEDIA_ROUTE_RE = re.compile(
    r"\b(?:play|listen\s+to|stream|watch|put\s+on|start|pause|resume|skip|next|previous|prev|volume|mute|unmute|louder|quieter)\b"
    r"|\b(?:music|song|album|playlist|track|tune|melody|video|podcast)\b",
    re.IGNORECASE,
)


def route(text):
    t = text.lower().strip()
    if _TAB_RE.search(t):
        return "tab"
    if _SYSTEM_RE.search(t):
        return "system"
    if _MEDIA_ROUTE_RE.search(t):
        return "media"
    if _QUERY_RE.search(t):
        return "query"
    return "convo"


_POLITE_REQUEST_RE = re.compile(r"\b(?:can|could|would|will|may)\s+you\b", re.IGNORECASE)

_DECISION_QUESTION_RE = re.compile(
    r"\b(?:should\s+i|should\s+we|do\s+you\s+want|do\s+you\s+think|do\s+we\s+need|"
    r"is\s+it\s+ok(?:ay)?\s+to|would\s+you\s+like\s+me|are\s+you\s+going|"
    r"should\s+the)\b",
    re.IGNORECASE,
)

_ACTION_WORD_RE = re.compile(
    r"\b(?:play|pause|resume|stop|open|close|launch|start|search|look|find|type|"
    r"write|press|scroll|click|switch|change|turn|"
    r"minimize|maximize|show|hide|set|ask|watch|listen)\b",
    re.IGNORECASE,
)


_WH_QUESTION_RE = re.compile(r"^(?:what|who|whose|whom|why|how|where|when|which)\b", re.IGNORECASE)


def is_user_question(text):
    t = text.strip()
    if not t:
        return False
    if _DECISION_QUESTION_RE.search(t) and not _POLITE_REQUEST_RE.search(t):
        return True
    if _WH_QUESTION_RE.match(t):
        return True
    if t.endswith("?") and not _POLITE_REQUEST_RE.search(t) and not _ACTION_WORD_RE.search(t):
        return True
    return False


def looks_like_request(text):
    t = text.strip()
    if not t or is_user_question(t):
        return False
    if _ACTION_WORD_RE.search(t):
        return True
    if _POLITE_REQUEST_RE.search(t):
        return True
    return False

import difflib
import re

SONGS = [
    "art deco", "summertime sadness", "video games", "young and beautiful", "west coast",
    "born to die", "ride", "national anthem", "off to the races", "blue jeans",
    "dark paradise", "radio", "without you", "lolita", "gods & monsters",
    "million dollar man", "lucky ones", "american", "cola", "body electric",
    "bel air", "cruel world", "ultraviolence", "shades of cool", "brooklyn baby",
    "pretty when you cry", "money power glory", "fucked my way up to the top",
    "old money", "the blackest day", "honeymoon", "salvatore", "freak",
    "terrence loves you", "god knows i tried", "high by the beach", "swan song",
    "love", "lust for life", "13 beaches", "cherry", "white mustang",
    "summer bummer", "groupie love", "in my feelings", "coachella", "change",
    "tomorrow never came", "heroin", "mariners apartment complex", "venice bitch",
    "fuck it i love you", "the greatest", "hope is a dangerous thing", "doin time",
    "norman fucking rockwell", "cinnamon girl", "how to disappear", "california",
    "the next best american record", "bartender", "happiness is a butterfly",
    "let me love you like a woman", "chemtrails over the country club", "white dress",
    "tulsa jesus freak", "dance till we die", "wildflower wildfire",
    "not all who wander are lost", "yosemite", "breaking up slowly", "dealer",
    "arcadia", "thunder", "if you lie down with me", "black bathing suit",
    "living legend", "cherry blossom", "beautiful", "violet for rose",
    "fingertips", "the grants", "did you know that there's a tunnel under ocean blvd",
    "a&w", "judah smith interlude", "candy necklace", "jon batiste interlude",
    "kintsugi", "paris texas", "grandfather please stand on the shoulders of my father while he's still tall",
    "let the light in", "margaret", "fishtail", "peppers", "taco truck x vb",
    "say yes to heaven", "once upon a dream", "blue banisters", "text book",
    "black beauty", "florida kilos", "guns and roses", "is this happiness",
    "the other woman", "your girl", "mermaid motel", "queen of disaster",
    "this is what makes us girls", "carmen", "yayo", "kind of out of luck",
    "played out", "jfk", "pale blue eyes", "fine china", "waiting for you",
    "you must love me", "africa", "cocomelon", "baby shark",
]

INDIAN_SONGS = [
    "vezham veeran", "aavesham", "naatu naatu", "rowdy baby", "why this kolaveri di",
    "why this kolaveri", "kolaveri", "vaathi coming", "joe", "kadharalu",
    "pulikuthi", "illuminati", "poove unakkaga", "enjoy enjaami", "jai ho",
    "tu jaane na", "gerua", "chammak challo", "sheila ki jawani", "munni badnam hui",
    "desi girl", "ladki beautiful", "gandi baat", "kala chashma", "channa mereya",
    "ae dil hai mushkil", "balam pichkari", "sunn raha hai", "humma humma",
    "vaathi", "master the blaster", "hukum", "jawan", "pathaan", "pushpa",
    "kgf", "salaar", "devara", "kalki", "rrr", "animal", "jailer", "leo",
    "vikram", "ponniyin selvan", "kuruthi", "manjummel boys", "rdx", "kadhal",
]

INDIAN_ARTISTS = [
    "anirudh", "a.r. rahman", "ar rahman", "ilaiyaraaja", "ilayaraja",
    "sid sriram", "shreya ghoshal", "arijit singh", "neha kakkar",
    "badshah", "diljit dosanjh", "m.s. subbulakshmi", "k.j. yesudas",
    "sp balasubrahmanyam", "s. p. balasubrahmanyam", "chinmayi",
    "santhosh narayanan", "gv prakash", "yuvan shankar raja", "harris jayaraj",
    "devi sri prasad", "thaman", "raghu dixit", "nawazuddin", "kabir singh",
]

ARTISTS = [
    "lana del rey", "lana del ray", "lana", "billie eilish", "the weeknd",
    "ariana grande", "taylor swift", "drake", "kanye west", "bad bunny",
    "daft punk", "radiohead", "coldplay", "the beatles", "the beetles",
    "queen", "nirvana", "adele", "eminem", "rihanna", "ed sheeran",
    "post malone", "bruno mars", "weeknd", "chase atlantic", "the neighbourhood",
    "joji", "cigarettes after sex", "tv girl", "mitski", "phoebe bridgers",
    "clairo", "beach house", "mac demarco", "tame impala", "arctic monkeys",
    "the 1975", "the smiths", "deftones", "aphex twin",
]

APPS = [
    "notepad", "calculator", "task manager", "taskmgr", "spotify", "discord",
    "chrome", "google chrome", "edge", "microsoft edge", "vscode", "vs code",
    "visual studio code", "file explorer", "explorer", "camera", "settings",
    "mail", "terminal", "cmd", "powerpoint", "word", "excel", "steam",
    "epic games", "roblox", "minecraft", "zoom", "slack", "outlook",
    "photos", "paint", "snipping tool", "control panel", "recycle bin",
    "this pc", "downloads", "documents", "desktop", "pictures", "videos",
    "music", "youtube music",
]

SITES = [
    "youtube", "yt", "github", "reddit", "gmail", "mail", "maps", "google maps",
    "drive", "google drive", "docs", "google docs", "spotify", "twitch",
    "twitter", "x", "instagram", "netflix", "amazon", "google", "wikipedia",
]

VERBS = [
    "play", "pause", "resume", "stop", "skip", "next", "previous", "prev",
    "open", "close", "launch", "start", "switch", "change", "search", "look",
    "find", "type", "write", "press", "scroll", "click", "double", "volume",
    "mute", "unmute", "louder", "quieter",
    "turn", "set", "go", "wake", "up", "down", "on",
    "off", "increase", "decrease", "show", "hide", "minimize", "maximize",
    "screenshot", "read", "tell",
]

COMMON = [
    "what", "how", "why", "when", "who", "which", "please", "can", "could",
    "would", "want", "need", "give", "make", "let", "help", "ok", "okay",
    "yes", "no", "yeah", "hmm", "umm", "um", "uh", "the", "a", "an", "and",
    "or", "but", "with", "for", "to", "of", "in", "on", "at", "by", "from",
    "that", "this", "these", "those", "it", "its", "i", "you", "me", "my",
    "mine", "your", "we", "us", "our", "they", "them", "he", "she", "her",
    "him", "his", "their", "there", "here", "then", "than", "so", "if", "as",
    "just", "really", "very", "too", "also", "some", "any", "all", "more",
    "most", "other", "another", "about", "after", "before", "between",
    "during", "without", "within", "through", "across", "around", "behind",
    "beside", "toward", "upon", "under", "over", "above", "below", "leave",
    "like", "was", "were", "is", "are", "am", "be", "been", "being", "do",
    "does", "did", "done", "doing", "have", "has", "had", "having", "will",
    "shall", "should", "may", "might", "must", "not", "never", "always",
    "today", "tomorrow", "yesterday", "now", "later", "soon", "quickly",
    "fast", "slow", "again", "already", "still", "even", "only", "own",
    "same", "such", "via", "per", "de", "la", "el", "del", "feat", "ft",
    "versus", "vs", "official", "audio", "video", "music", "song", "album",
    "track", "artist", "title", "band", "cover", "remix", "remastered",
    "live", "acoustic", "instrumental", "version", "full", "length", "original",
    "slowed", "reverb", "sped", "nightcore", "radio", "edit", "extended",
    "demo", "clean", "explicit", "short", "shorts", "watch", "playlist",
    "stream", "episode", "podcast", "movie", "film", "trailer", "documentary",
    "lecture", "interview", "gameplay", "walkthrough", "name", "call",
    "called", "known", "often", "maybe", "perhaps", "probably", "actually",
    "usually", "normally", "generally", "sometimes", "almost", "together",
    "etc", "example", "thanks", "thank", "hey", "hello", "hi", "yo", "bye",
    "good", "great", "nice", "cool", "awesome", "perfect", "fine", "ok",
]

_ALIASES = {
    "articub": "art deco",
    "articube": "art deco",
    "art decube": "art deco",
    "article": "art deco",
    "summertimes": "summertime",
    "summer time sadness": "summertime sadness",
    "summertime sadnes": "summertime sadness",
    "summertime sad ness": "summertime sadness",
    "lana del ray": "lana del rey",
    "lana delray": "lana del rey",
    "lana del roy": "lana del rey",
    "lana del r": "lana del rey",
    "notpad": "notepad",
    "caculator": "calculator",
    "youtub": "youtube",
    "gitub": "github",
    "discordd": "discord",
    "spotifyy": "spotify",
    "teya": "theia",
    "tiya": "theia",
    "theeya": "theia",
    "theia": "theia",
    "veezha veeran": "vezham veeran",
    "veezha veeran song": "vezham veeran song",
    "veezha veeran": "vezham veeran",
    "vezha veeran": "vezham veeran",
    "veeraan": "vezham veeran",
    "veezham veeran": "vezham veeran",
}

_TOKEN_RE = re.compile(r"[a-z0-9']+")


def _tokenize(text):
    return _TOKEN_RE.findall(text.lower())


class Normalizer:
    def __init__(self):
        self._vocab = set()
        items = SONGS + INDIAN_SONGS + ARTISTS + INDIAN_ARTISTS + APPS + SITES + VERBS + COMMON
        items += list(_ALIASES.keys()) + list(_ALIASES.values())
        for item in items:
            for w in _TOKEN_RE.findall(item.lower()):
                self._vocab.add(w)

    def _fix_word(self, word):
        if word in self._vocab:
            return word
        if len(word) < 4:
            return word
        best = word
        best_score = 0.0
        for cand in self._vocab:
            if len(cand) < 4:
                continue
            score = difflib.SequenceMatcher(None, word, cand).ratio()
            if score > best_score:
                best_score = score
                best = cand
        return best if best_score >= 0.78 else word

    def clean(self, text, fix=True):
        t = (text or "").strip()
        if not t:
            return t
        low = t.lower()
        for old, new in sorted(_ALIASES.items(), key=lambda kv: -len(kv[0])):
            low = re.sub(r"\b" + re.escape(old) + r"\b", new, low)
        if not fix:
            return low
        words = low.split()
        fixed = [self._fix_word(w) for w in words]
        return " ".join(fixed)

    def unknown_tokens(self, text):
        return [w for w in _tokenize(text) if w not in self._vocab and len(w) >= 4]

    def media_query(self, q):
        q = (q or "").strip()
        if not q:
            return None
        unknown = self.unknown_tokens(q)
        known = [w for w in _tokenize(q) if w in self._vocab]
        if len(unknown) >= 2 and not known:
            return None
        return q

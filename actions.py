import os
import re
import subprocess
import threading
import urllib.parse
import urllib.request
import webbrowser

import pyautogui

pyautogui.FAILSAFE = False

APPS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "task manager": "taskmgr.exe",
    "taskmgr": "taskmgr.exe",
    "spotify": "spotify",
    "discord": "discord",
    "chrome": "chrome",
    "google chrome": "chrome",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "vscode": "code",
    "vs code": "code",
    "visual studio code": "code",
    "file explorer": "explorer.exe",
    "explorer": "explorer.exe",
    "camera": "microsoft.windows.camera:",
    "settings": "ms-settings:",
    "mail": "outlook.exe",
    "terminal": "wt",
    "whatsapp": "whatsapp",
    "telegram": "telegram",
    "steam": "steam",
    "photos": "microsoft.windows.photos:",
    "paint": "mspaint",
    "powershell": "powershell.exe",
    "snipping tool": "snippingtool",
    "control panel": "control",
}

SITES = {
    "youtube": "https://www.youtube.com",
    "yt": "https://www.youtube.com",
    "github": "https://github.com",
    "reddit": "https://www.reddit.com",
    "gmail": "https://mail.google.com",
    "mail": "https://mail.google.com",
    "maps": "https://maps.google.com",
    "google maps": "https://maps.google.com",
    "drive": "https://drive.google.com",
    "google drive": "https://drive.google.com",
    "docs": "https://docs.google.com",
    "google docs": "https://docs.google.com",
    "spotify": "https://open.spotify.com",
    "twitch": "https://www.twitch.tv",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "instagram": "https://www.instagram.com",
    "netflix": "https://www.netflix.com",
    "amazon": "https://www.amazon.com",
    "google": "https://www.google.com",
    "wikipedia": "https://www.wikipedia.org",
    "facebook": "https://www.facebook.com",
    "linkedin": "https://www.linkedin.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "whatsapp web": "https://web.whatsapp.com",
    "prime video": "https://www.primevideo.com",
    "hulu": "https://www.hulu.com",
    "pinterest": "https://www.pinterest.com",
    "tumblr": "https://www.tumblr.com",
}

APPS_ORDER = sorted(APPS, key=len, reverse=True)
SITES_ORDER = sorted(SITES, key=len, reverse=True)

CLOSE_APPS = {
    "spotify": "spotify.exe",
    "discord": "discord.exe",
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "vscode": "Code.exe",
    "vs code": "Code.exe",
    "visual studio code": "Code.exe",
    "notepad": "notepad.exe",
    "calculator": "CalculatorApp.exe",
    "terminal": "WindowsTerminal.exe",
    "whatsapp": "WhatsApp.exe",
    "telegram": "Telegram.exe",
    "steam": "steam.exe",
    "photos": "PhotosApp.exe",
    "paint": "mspaint.exe",
    "powershell": "powershell.exe",
    "snipping tool": "SnippingTool.exe",
}
CLOSE_ORDER = sorted(CLOSE_APPS, key=len, reverse=True)

MEDIA_KEYS = {
    "play": "playpause",
    "pause": "playpause",
    "next": "nexttrack",
    "previous": "prevtrack",
    "stop": "stop",
}

_CTRL_PHRASE_RE = re.compile(
    r"^(?:the\s+)?(?:next|previous|prev|first|second|third|this|that|some|another)"
    r"\s+(?:song|track|video|one)$",
    re.IGNORECASE,
)


def _has_any(text, words):
    return any(w in text for w in words)


def _after(text, markers):
    for m in sorted(markers, key=len, reverse=True):
        if m in text:
            after = text.split(m, 1)[1].strip()
            if after:
                return after
    return ""


def _yt_search(q):
    return "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(q)


_YT_ID_RE = re.compile(r'"videoId":"([A-Za-z0-9_-]{11})"')
_YT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

_SHORTS_TAG = "-shorts"
_NO_AUDIO_TAG = ("video", "podcast", "episode", "trailer", "movie", "film",
                 "documentary", "lecture", "radio", "interview", "gameplay",
                 "walkthrough", "live stream", "stream")


def build_media_query(query):
    q = (query or "").strip()
    if not q:
        return q
    low = q.lower()
    tags = []
    if not re.search(r"\bshorts\b", low):
        tags.append(_SHORTS_TAG)
    if not any(h in low for h in ("official audio", "official video", "music video",
                                  "lyric video", "full length")):
        if not any(h in low for h in _NO_AUDIO_TAG):
            tags.insert(0, "official audio")
    if tags:
        q = f"{q} {' '.join(tags)}"
    return q


def _fetch_yt_video_ids(query, limit=3):
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
    req = urllib.request.Request(url, headers={"User-Agent": _YT_UA, "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return []

    def block_for(match):
        start = match.end()
        depth = 1
        i = start
        n = len(html)
        while i < n and depth:
            c = html[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            i += 1
        return html[start:i]

    seen = []
    for m in re.finditer(r'"videoRenderer":\s*\{', html):
        block = block_for(m)
        if '"lengthText"' not in block:
            continue
        vid = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', block)
        if vid and vid.group(1) not in seen:
            seen.append(vid.group(1))
            if len(seen) >= limit:
                break
    if seen:
        return seen
    for m in _YT_ID_RE.finditer(html):
        vid = m.group(1)
        if vid not in seen:
            seen.append(vid)
            if len(seen) >= limit:
                break
    return seen


def media_open(query, confirm, index=0):
    query = (query or "").strip()
    if not query:
        return "media", query, confirm

    def job():
        target = None
        first = build_media_query(query)
        ids = _fetch_yt_video_ids(first)
        if not ids:
            ids = _fetch_yt_video_ids(query)
        if ids and index < len(ids):
            target = f"https://www.youtube.com/watch?v={ids[index]}"
        if target is None:
            target = _yt_search(first)
        try:
            webbrowser.open(target)
        except Exception:
            pass

    threading.Thread(target=job, daemon=True).start()
    return "media", query, confirm


def _focus_or_launch(app, confirm):
    name = str(app).lower().strip()
    try:
        import pygetwindow
        wins = [w for w in pygetwindow.getAllWindows() if name in w.title.lower()]
        if wins:
            try:
                wins[0].activate()
            except Exception:
                try:
                    wins[0].minimize()
                    wins[0].restore()
                except Exception:
                    pass
            return "app", app, f"Switched to {app}, babe."
    except Exception:
        pass
    target = APPS.get(name)
    if target:
        try:
            subprocess.Popen(["cmd", "/c", "start", "", target], creationflags=_NO_WINDOW)
        except Exception:
            return None, None, None
        return "app", app, f"Opening {app} for you, baby."
    return None, None, None


def _tab_key(keys):
    try:
        pyautogui.hotkey(*keys)
        return True
    except Exception:
        return False


def _find_window(label):
    try:
        import pygetwindow
        wins = [w for w in pygetwindow.getAllWindows() if label in w.title.lower()]
        return wins[0] if wins else None
    except Exception:
        return None


def _close_window(win):
    try:
        win.close()
        return True
    except Exception:
        return False


def tab_control(text):
    low = " " + text.lower().strip() + " "

    m = re.search(r"\bclose\s+(?:the\s+)?(.+?)\s+(?:tab|window)\b", low)
    if m:
        label = m.group(1).strip()
        if label in ("youtube", "yt"):
            win = _find_window("youtube")
            if win is not None and _close_window(win):
                return "done", None, "Closed the YouTube tab, master."
            if _tab_key(("ctrl", "w")):
                return "done", None, "Closed the YouTube tab, master."
        win = _find_window(label)
        if win is not None and _close_window(win):
            return "done", None, f"Closed the {label} {m.group(0).split()[-1]}, master."
        if _tab_key(("ctrl", "w")):
            return "done", None, f"Closed the {label} {m.group(0).split()[-1]}, master."
        return None

    if re.search(r"\bclose\s+(?:this\s+)?(?:tab|window)\b", low):
        if _tab_key(("ctrl", "w")):
            return "done", None, "Closed the tab, master."
        return None

    if re.search(r"\b(?:new|open)\s+tab\b", low):
        if _tab_key(("ctrl", "t")):
            return "done", None, "New tab opened, babe."
        return None

    if re.search(r"\b(?:reopen|restore)\s+(?:the\s+)?(?:last|closed)\s+tab\b", low):
        if _tab_key(("ctrl", "shift", "t")):
            return "done", None, "Reopened the last tab, sweetheart."
        return None

    if re.search(r"\b(?:next|other|another)\s+tab\b", low):
        if _tab_key(("ctrl", "tab")):
            return "done", None, "Switched tabs, babe."
        return None

    if re.search(r"\b(?:previous|prev|last)\s+tab\b", low):
        if _tab_key(("ctrl", "shift", "tab")):
            return "done", None, "Switched back a tab, honey."
        return None

    if re.search(r"\b(?:new|another|open)\s+window\b", low):
        if _tab_key(("ctrl", "n")):
            return "done", None, "New window opened, baby."
        return None

    if re.search(r"\bminimize\s+(?:the\s+)?(?:window|app|application)\b", low):
        try:
            import pygetwindow
            win = pygetwindow.getActiveWindow()
            if win is not None:
                win.minimize()
                return "done", None, "Minimized, master."
        except Exception:
            pass
        return None

    m = re.search(r"\bminimize\s+(?:the\s+)?(.+)$", low)
    if m:
        win = _find_window(m.group(1).strip())
        if win is not None:
            try:
                win.minimize()
                return "done", None, f"Minimized {m.group(1).strip()}, master."
            except Exception:
                pass
        return None

    if re.search(r"\bmaximize\s+(?:the\s+)?(?:window|app|application)\b", low):
        try:
            import pygetwindow
            win = pygetwindow.getActiveWindow()
            if win is not None:
                win.maximize()
                return "done", None, "Maximized, master."
        except Exception:
            pass
        return None

    if re.search(r"\b(?:show|view)\s+(?:my\s+|the\s+)?desktop\b", low):
        if _tab_key(("win", "d")):
            return "done", None, "Showing the desktop, master."
        return None

    if re.search(r"\b(?:task\s+view|all\s+windows|view\s+all\s+windows)\b", low):
        if _tab_key(("win", "tab")):
            return "done", None, "Task view, babe."
        return None

    return None


def _open(url, confirm):
    try:
        webbrowser.open(url)
    except Exception:
        return None, None, None
    return "web", {"url": url}, confirm


def _media(verb, confirm):
    key = MEDIA_KEYS.get(verb)
    if not key:
        return None, None, None
    try:
        pyautogui.press(key)
    except Exception:
        return None, None, None
    return "done", None, confirm


def _volume(action, value=None):
    try:
        from pycaw.pycaw import AudioUtilities

        device = AudioUtilities.GetSpeakers()
        volume = device.EndpointVolume
        if action == "mute":
            volume.SetMute(1, None)
            return "volume", ("mute", None), "Muted, daddy."
        if action == "unmute":
            volume.SetMute(0, None)
            return "volume", ("unmute", None), "Unmuted, sweetheart."
        if action == "set":
            volume.SetMasterVolumeLevelScalar(max(0.0, min(1.0, value / 100.0)), None)
            return "volume", ("set", value), f"Volume set to {int(value)} percent, master."
        current = volume.GetMasterVolumeLevelScalar()
        if action == "up":
            volume.SetMasterVolumeLevelScalar(min(1.0, current + 0.05), None)
            return "volume", ("up", None), "Turning the volume up, babe."
        if action == "down":
            volume.SetMasterVolumeLevelScalar(max(0.0, current - 0.05), None)
            return "volume", ("down", None), "Turning the volume down, honey."
    except Exception:
        return None, None, None
    return None, None, None


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _exec_system(verb):
    return


def confirm_system(verb):
    try:
        _exec_system(verb)
        return True
    except Exception:
        return False


def _system(verb, confirm):
    try:
        _exec_system(verb)
    except Exception:
        return None, None, None
    return "system", verb, confirm


def handle(text):
    t = " " + text.lower().strip() + " "

    if re.search(r"\b(?:close|stop|exit|minimize|hide|delete|kill)\b", t) and _has_any(t, ["search", "look up", "find", "google"]):
        return None, None, None

    if _has_any(t, ["open youtube", "launch youtube", "start youtube"]):
        return _open(SITES["youtube"], "Opening YouTube for you, babe.")

    m = re.match(r"^(?:please\s+)?(?:theia\s+)?play\s+(?:the\s+)?(next|previous|prev)\s+(?:song|track|video|one)(?:\s+(?:on|in)\s+(?:the\s+)?(?:youtube|yt|window|windows|screen|tab|browser|page))?\s*$", t.strip())
    if m:
        verb = "previous" if m.group(1).startswith(("p", "P")) else "next"
        confirm = "Previous track, honey." if verb == "previous" else "Next track, babe."
        return _media(verb, confirm)

    if (" on youtube" in t or "youtube " in t) and not re.search(r"\b(?:close|stop|exit|minimize|hide|delete|kill)\b", t):
        q = _after(t, [" on youtube", "youtube "])
        if not q:
            head = re.split(r" on youtube\b| youtube ", t.strip())[0].strip()
            q = re.sub(r"^(?:please\s+)?(?:theia\s+)?(?:play|start|put\s+on|turn\s+on)\s+", "", head).strip()
        if q and not _CTRL_PHRASE_RE.match(q):
            if _has_any(t, ["play", "start", "put on", "turn on"]):
                return media_open(q, f"Playing {q} on YouTube for you, sweetheart.")
            return _open(_yt_search(q), f"Finding {q} on YouTube for you, sweetheart.")

    core = text.strip().lower()
    if re.fullmatch(r"(?:please\s+)?(?:theia\s+)?(?:play|pause|resume|next|skip|previous|prev)(?:\s+it|\s+that)?", core):
        if "next" in core or "skip" in core:
            return _media("next", "Next track, babe.")
        if "previous" in core or "prev" in core:
            return _media("previous", "Previous track, honey.")
        if "pause" in core:
            return _media("pause", "Paused, sweetheart.")
        return _media("play", "Resuming playback, daddy.")

    if "watch?v=" in t:
        vid = text.split("watch?v=")[-1].split()[0].split("&")[0]
        return _open(f"https://www.youtube.com/watch?v={vid}", "Playing that video for you, baby.")

    if _has_any(t, ["play video", "play a video", "play the video"]):
        q = _after(t, ["play the video", "play a video", "play video"])
        if q:
            return media_open(q, f"Playing {q} on YouTube.")
        return _open(_yt_search(""), "What video should I play, baby?")

    if _has_any(t, ["play some music", "play a song", "play a tune", "play music video", "play music", "start music", "resume music", "play my music", "play the song", "play a track", "play the track", "play the music", "play some song"]):
        markers = ["play music video", "play the song", "play a song", "play a track",
                   "play the track", "play some music", "play the music", "play some song",
                   "play music", "play a tune", "start music", "resume music", "play my music"]
        q = _after(t, markers)
        if q and not _CTRL_PHRASE_RE.match(q):
            return media_open(q, f"Playing {q} on YouTube.")
        return "music", {"action": "play"}, "Playing your music, daddy."

    if _has_any(t, ["pause music", "pause the music"]):
        return "music", {"action": "pause"}, "Paused, sweetheart."
    if _has_any(t, ["next track", "next song", "skip track", "skip song"]):
        return "music", {"action": "next"}, "Skipping to the next track, babe."
    if _has_any(t, ["previous track", "previous song", "prev track"]):
        return "music", {"action": "prev"}, "Going back a track, honey."
    if _has_any(t, ["stop music", "stop the music", "stop audio"]):
        return "music", {"action": "stop"}, "Stopped, master."

    if _has_any(t, ["volume up", "turn it up", "louder"]):
        return _volume("up")
    if _has_any(t, ["volume down", "turn it down", "quieter"]):
        return _volume("down")
    if _has_any(t, ["unmute", "un mute"]):
        return _volume("unmute")
    if _has_any(t, ["mute"]):
        return _volume("mute")
    if _has_any(t, ["set volume"]):
        m = __import__("re").search(r"(\d{1,3})", t)
        if m:
            return _volume("set", int(m.group(1)))



    if _has_any(t, ["close youtube", "close yt", "close youtube tab", "close yt tab"]):
        try:
            import pygetwindow
            wins = [w for w in pygetwindow.getAllWindows() if "youtube" in w.title.lower()]
            if wins:
                try:
                    wins[0].activate()
                except Exception:
                    wins[0].minimize()
                    wins[0].restore()
                pyautogui.press("ctrl", "w")
                return "done", None, "Closed the YouTube tab, master."
        except Exception:
            pass
        target = CLOSE_APPS.get("chrome")
        if target:
            try:
                subprocess.Popen(["taskkill", "/IM", target, "/F"], creationflags=_NO_WINDOW)
                return "done", None, "Closed YouTube, sweetheart."
            except Exception:
                pass
        return None, None, None

    for app in CLOSE_ORDER:
        if f"close {app}" in t:
            target = CLOSE_APPS[app]
            try:
                subprocess.Popen(["taskkill", "/IM", target, "/F"], creationflags=_NO_WINDOW)
            except Exception:
                return None, None, None
            return "done", None, f"Closed {app}, baby."

    if _has_any(t, ["giggle", "laugh", "hehe"]):
        return "sfx", "giggle", None

    if _has_any(t, ["open music folder", "open my music", "open music files"]):
        music_dir = os.path.expanduser("~/Music")
        if os.path.isdir(music_dir):
            os.startfile(music_dir)
            return True, None, "Opening your music folder, sweetheart~"
        return _open(SITES["youtube"], "Opening YouTube for you, honey.")

    _FOLDERS = {
        "downloads": "~/Downloads",
        "documents": "~/Documents",
        "desktop": "~/Desktop",
        "pictures": "~/Pictures",
        "videos": "~/Videos",
    }
    for _name, _rel in _FOLDERS.items():
        if f" open {_name} " in t or t.startswith(f"open {_name} "):
            _folder = os.path.expanduser(_rel)
            if os.path.isdir(_folder):
                try:
                    os.startfile(_folder)
                    return True, None, f"Opening your {_name} folder, sweetheart~"
                except Exception:
                    pass

    m = re.match(r"(?:please\s+)?(?:theia\s+)?(?:switch|change)\s+(?:to|into)\s+(?:the\s+)?(.+)$", t.strip())
    if m:
        name = m.group(1).strip().strip("\"'")
        if name:
            res = _focus_or_launch(name, f"Switched to {name}.")
            if res is not None and res[0] is not None:
                return res

    if t.startswith(" play "):
        q = t[len(" play "):].strip()
        if q:
            return media_open(q, f"Playing {q} on YouTube.")

    for app in APPS_ORDER:
        if f" {app} " in t or t.startswith(app + " ") or t.endswith(" " + app):
            target = APPS[app]
            try:
                subprocess.Popen(["cmd", "/c", "start", "", target], creationflags=_NO_WINDOW)
            except Exception:
                return None, None, None
            return "app", app, f"Opening {app} for you, baby."

    for site in SITES_ORDER:
        if f" {site} " in t or t.startswith(site + " ") or t.endswith(" " + site):
            return _open(SITES[site], f"Opening {site} for you, sweetheart.")

    if _has_any(t, ["search for", "google ", "look up ", "search "]):
        if re.search(r"\b(?:close|stop|exit|minimize|hide|delete|kill)\b", t):
            return None, None, None
        q = _after(t, ["search for", "google", "look up", "search"])
        if q:
            return _open(f"https://www.google.com/search?q={urllib.parse.quote_plus(q)}", f"Searching for {q}, honey.")

    if t.startswith(" open "):
        rest = text.strip()[5:].strip().strip("\"'")
        if rest.startswith("http"):
            return _open(rest, f"Opening {rest} for you, baby.")
        if "." in rest and " " not in rest:
            return _open("https://" + rest, f"Opening {rest} for you, baby.")

    return None, None, None

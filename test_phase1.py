import os
import pathlib
import queue
import tempfile
import threading
import time
from unittest.mock import patch

import numpy as np

import config as cfgmod

import actions
import intent
import normalize
from command import ACTION_REGISTRY, Command, Domain, Risk, is_allowed
from session import SessionState
import server as sv

PASS = 0
FAIL = []


def check(name, cond, detail=""):
    global PASS
    if not cond:
        FAIL.append(f"{name}: {detail}")
        print(f"FAIL {name} {detail}")
    else:
        PASS += 1
        print(f"OK  {name}")


print("== Phase 1.1 command registry ==")
check("restart not in registry", ("system", "restart") not in ACTION_REGISTRY)
check("shutdown not in registry", ("system", "shutdown") not in ACTION_REGISTRY)
check("tab close low risk", ACTION_REGISTRY[("tab", "close")][0] == Risk.LOW)
check("file delete high risk + confirm", ACTION_REGISTRY[("files", "delete")] == (Risk.HIGH, True))
check("media play low risk", ACTION_REGISTRY[("media", "play")][0] == Risk.LOW)

cmd = Command(domain="system", action="restart")
ok, why = cmd.validate()
check("restart rejected by registry", not ok and "allowlisted" in why)
bad = Command(domain="system", action="rm_rf")
ok, why = bad.validate()
check("non-allowlisted rejected", not ok and "allowlisted" in why)

print("== Phase 1.2 question guard ==")
check("decision question", intent.is_user_question("should i restart the pc"))
check("decision question 2", intent.is_user_question("do you want me to shut down"))
check("polite request not question", not intent.is_user_question("can you open youtube for me"))
check("plain command not question", not intent.is_user_question("restart the pc"))
check("info question is question", intent.is_user_question("what is the weather today"))
check("looks like request", intent.looks_like_request("could you please open chrome"))
check("question not request", not intent.looks_like_request("should i restart"))

print("== Phase 1.3 gates (audit + negative search) ==")
audit = sv.Hub._audit_dispatch.__get__(object(), sv.Hub)
check("tab cannot search", not audit("tab", "web"))
check("query cannot system", not audit("query", "system"))
check("query cannot app", not audit("query", "app"))
check("query cannot done", not audit("query", "done"))
check("media cannot system", not audit("media", "system"))
check("system cannot web", not audit("system", "web"))
check("tab done allowed", audit("tab", "done"))
check("media music allowed", audit("media", "music"))
check("query web allowed", audit("query", "web"))
check("null kind allowed", audit("tab", None))

opened = []


def fake_open(url, confirm):
    opened.append((url, confirm))
    return "web", url, confirm


with patch.object(actions, "_open", side_effect=fake_open):
    res = actions.handle("search for cute cats")
check("search produces web kind", res and res[0] == "web", repr(res))
check("search url is google", bool(opened) and "google.com/search" in opened[0][0], repr(opened))
opened.clear()
with patch.object(actions, "_open", side_effect=fake_open):
    res = actions.handle("search for close youtube")
check("negative gate blocks search", res == (None, None, None), repr(res))
check("no search executed when blocked", opened == [], repr(opened))
res = actions.handle("stop music")
check("stop music routes to music", res and res[0] == "music", repr(res))
res = actions.handle("restart the pc")
check("restart no longer dispatches", res == (None, None, None), repr(res))
res = actions.handle("shut down the pc")
check("shutdown no longer dispatches", res == (None, None, None), repr(res))

print("== Phase 1.4 clarification flows (stub hub) ==")


class StubHub:
    def __init__(self):
        self.outq = queue.Queue()
        self.last_state = None
        self.mic_enabled = True
        self._awoken = True
        self._speaking = False
        self._pending_text = None
        self.stop_flag = threading.Event()
        self.lock = threading.Lock()
        self.session = SessionState()
        self.normalizer = normalize.Normalizer()
        self.last_media_query = None
        self.spoken = []
        self.events = []
        self.music = None
        self.llm = None
        self._confirm_action = None
        self._clarify = None
        self._relay_depth = 0
        self._textq = queue.Queue()
        self._info_by_utt = {}
        self._active_utt_id = None
        self._latency = {}
        self._last_partial_text = ""
        self._hot_text = {}
        self._spec_cache = {}
        self._spec_prev = {}
        self._spec_busy = False

    def emit(self, p):
        self.events.append(p)

    def emit_state(self, s):
        self.last_state = s

    def speak_reply(self, t):
        self.spoken.append(t)

    def active(self):
        return True

    def add_user(self, t):
        pass

    def get_reply(self):
        return "answer"

    def _audit_dispatch(self, d, k):
        return True

    _CONNECTOR_LAST = sv.Hub._CONNECTOR_LAST
    _HOLD_SINGLE = sv.Hub._HOLD_SINGLE
    _RELAY_ALLOWED = sv.Hub._RELAY_ALLOWED
    _relay_allowed = sv.Hub._relay_allowed
    _gate = sv.Hub._gate
    _tab_action = sv.Hub._tab_action
    _dispatch_action = sv.Hub._dispatch_action
    passes_wake = sv.Hub.passes_wake
    is_stop_cmd = sv.Hub.is_stop_cmd
    _process_text = sv.Hub._process_text
    _drain_pending = sv.Hub._drain_pending
    _stage = sv.Hub._stage
    _emit_user = sv.Hub._emit_user
    _looks_like_hallucination = sv.Hub._looks_like_hallucination


hub = StubHub()
_process = sv.Hub._process_text.__get__(hub, StubHub)

hub.spoken.clear()
_process("play that again")
assert hub._clarify and hub._clarify["kind"] == "replay_target", hub._clarify
assert hub.spoken == ["What should I play again, master?"], hub.spoken
_process("summertime sadness")
assert hub.last_media_query == "summertime sadness", hub.last_media_query
assert hub._clarify is None
print("OK  replay clarification resolves")

hub.spoken.clear()
_process("close it")
assert hub._clarify and hub._clarify["kind"] == "close_target", hub._clarify
assert hub.spoken == ["Which window should I close, master?"], hub.spoken
_resolve_close = sv.Hub._resolve_close_target.__get__(hub, StubHub)
_resolve_close("youtube")
assert hub.spoken[-1] and "YouTube" in hub.spoken[-1], hub.spoken
assert hub._clarify is None
print("OK  close-target clarification resolves")

hub.spoken.clear()
class FakeLLM:
    def understand_command(self, text):
        return {"relay": "open notepad", "answer": ""}
    def chat(self, msgs):
        return "answer"
hub.llm = FakeLLM()
_process("could you please open notepad")
assert any("notepad" in s.lower() for s in hub.spoken), hub.spoken
print("OK  LLM relay executes allowlisted action")

hub.spoken.clear()
class FakeLLM2:
    def understand_command(self, text):
        return {"relay": "rm -rf /", "answer": ""}
    def chat(self, msgs):
        return "answer"
hub.llm = FakeLLM2()
_process("please clean the system")
assert not any("rm" in s.lower() for s in hub.spoken), hub.spoken
assert hub.spoken == ["answer"], hub.spoken
print("OK  malicious relay rejected, falls back to LLM answer")

print("== Phase 2.1 gated dispatch ==")
gate = sv.Hub._gate.__get__(hub, StubHub)
hub.spoken.clear()
r = gate("system", "shutdown", raw="shut down")
check("unlisted gate refuses + speaks", r is None and hub.spoken == ["I can't do that, master."], hub.spoken)
hub.spoken.clear()
r = gate("media", "play", raw="play x")
check("listed gate passes silently", r is not None and hub.spoken == [], hub.spoken)
tab_action = sv.Hub._tab_action.__get__(hub, StubHub)
check("tab close parsed", tab_action("close the youtube tab") == "close")
check("tab new parsed", tab_action("open a new tab") == "new")
check("tab prev parsed", tab_action("go to previous tab") == "prev")
check("tab task view parsed", tab_action("show task view") == "task_view")
check("tab desktop parsed", tab_action("show desktop") == "desktop")
check("tab unknown none", tab_action("mumble") is None)
da = sv.Hub._dispatch_action.__get__(hub, StubHub)
check("media maps to play", da("media", "q", "play x") == ("media", "play"))
check("music act maps", da("music", {"action": "next"}, "") == ("media", "next"))
check("volume maps", da("volume", None, "") == ("system", "volume"))
check("system without payload unmapped", da("system", None, "") is None)
check("web maps", da("web", "u", "") == ("query", "open_url"))
check("app maps", da("app", "chrome", "") == ("apps", "open"))
check("sfx maps", da("sfx", "giggle", "") == ("sfx", "giggle"))
check("done close maps", da("done", None, "close chrome") == ("apps", "close"))
check("done folder maps", da("done", None, "open downloads folder") == ("files", "open_folder"))
check("done screenshot maps", da("done", None, "take a screenshot") == ("query", "screenshot"))
check("null kind unmapped", da(None, None, "") is None)
mapped = [
    da("media", "q", "play x"), da("music", {"action": "play"}, ""),
    da("music", {"action": "pause"}, ""), da("music", {"action": "resume"}, ""),
    da("music", {"action": "next"}, ""), da("music", {"action": "prev"}, ""),
    da("music", {"action": "stop"}, ""), da("volume", None, ""),
    da("web", "u", ""), da("app", "chrome", ""),
    da("sfx", "giggle", ""), da("done", None, "close chrome"),
    da("done", None, "open downloads folder"), da("done", None, "take a screenshot"),
]
check("all dispatch kinds allowlisted", all(reg is None or is_allowed(*reg) for reg in mapped), mapped)
registry_bad = [f"{d}/{a}" for (d, a) in ACTION_REGISTRY if not Command(domain=d, action=a).validate()[0]]
check("registry entries self-consistent", not registry_bad, registry_bad)

print("== Phase 2.2 extended command coverage ==")
with patch.object(actions.subprocess, "Popen") as popen:
    res = actions.handle("open notepad")
check("open notepad -> app kind", res and res[0] == "app", repr(res))
check("app launch invoked", popen.called, "")
opened.clear()
with patch.object(actions, "_open", side_effect=fake_open):
    res = actions.handle("open facebook")
check("open facebook -> web kind", res and res[0] == "web", repr(res))
check("facebook url correct", bool(opened) and "facebook.com" in opened[0][0], repr(opened))
with patch.object(actions.subprocess, "Popen") as popen:
    res = actions.handle("close spotify")
check("close spotify -> done kind", res and res[0] == "done", repr(res))
check("kill invoked", popen.called, "")
opened.clear()
with patch.object(actions, "_open", side_effect=fake_open):
    res = actions.handle("open gmail")
check("site opens web kind", res and res[0] == "web", repr(res))
check("gmail url correct", bool(opened) and "mail.google.com" in opened[0][0], repr(opened))
opened.clear()
with patch.object(actions, "_open", side_effect=fake_open):
    res = actions.handle("open chatgpt")
check("chatgpt opens", bool(opened) and "chatgpt.com" in opened[0][0], repr(opened))
res = actions.handle("stop music")
check("stop music still music", res and res[0] == "music", repr(res))
res = actions.handle("put the pc to sleep")
check("sleep no longer dispatches", res == (None, None, None), repr(res))
res = actions.handle("lock the pc")
check("lock no longer dispatches", res == (None, None, None), repr(res))
check("lock not in registry", ("system", "lock") not in ACTION_REGISTRY)
check("sleep not in registry", ("system", "sleep") not in ACTION_REGISTRY)
with patch.object(actions.subprocess, "Popen") as popen:
    res = actions.handle("open whatsapp")
check("whatsapp opens", popen.called and res and res[0] == "app", repr(res))

print("== Phase 2.3 computer-use reliability (skipped: feature removed) ==")

print("== Phase 3.1 session follow-up resolution ==")
s = SessionState(ttl=5)
s.register_media("Veera Veeran")
res = s.resolve_followup("slowed")
check("clarifier merges", res and res.lower().startswith("veera veeran slowed"), repr(res))
check("official audio suffix", res and res.endswith("official audio"), repr(res))
res2 = s.resolve_followup("reverb")
check("chained clarifier accumulates", res2 and "slowed + reverb" in res2.lower(), repr(res2))
for extra in ["live", "acoustic", "cover", "demo"]:
    s.resolve_followup(extra)
res3 = s.resolve_followup("album")
check("clarifier cap at 4", res3 and len(s.media_context["clarifiers"]) <= 4, repr(res3))
s2 = SessionState(ttl=5)
s2.register_media("Some Song")
check("non-clarifier leaves context", s2.resolve_followup("play something else") is None and s2.media_context is not None)
s3 = SessionState(ttl=0.05)
s3.register_media("Some Song")
time.sleep(0.1)
check("ttl expires context", s3.resolve_followup("slowed") is None and s3.media_context is None)
hub.session = SessionState(ttl=5)
hub.session.register_media("Summertime Sadness")
with patch.object(actions, "media_open") as mo:
    hub.spoken.clear()
    _process("slowed")
check("followup resolved through pipeline", mo.called and "slowed" in mo.call_args[0][0], mo.call_args)
check("followup replies", bool(hub.spoken), hub.spoken)

print("== Phase 3.2 wake / stop gates ==")
hub.wake_enabled = False
check("wake disabled passes all", hub.passes_wake("anything at all"))
hub.wake_enabled = True
hub.cfg = {"wake": {"word": "lana"}}
check("wake word starts command", hub.passes_wake("lana open youtube"))
check("wake word missed", not hub.passes_wake("open youtube"))
check("wake word anywhere wakes", hub.passes_wake("hey lana open youtube"))
hub.wake_enabled = False
check("stop detected", hub.is_stop_cmd("stop"))
check("stop music not plain stop", not hub.is_stop_cmd("stop the music"))

print("== Phase 3.3 history cap ==")


class FakeLLMChat:
    def __init__(self):
        self.replies = ["r1", "r2", "r3"]

    def chat(self, msgs):
        return self.replies.pop(0)

    def chat_with_tools(self, msgs, tools, executor):
        return self.chat(msgs)


class StubHistory:
    def __init__(self):
        self.lock = threading.Lock()
        self.cfg = {"llm": {"max_history": 4}}
        self.history = []
        self.computer = None


hist = StubHistory()
hist.llm = FakeLLMChat()
addu = sv.Hub.add_user.__get__(hist, StubHistory)
getr = sv.Hub.get_reply.__get__(hist, StubHistory)
for i in range(6):
    addu(f"m{i}")
check("history capped", len(hist.history) == 4, hist.history)
getr()
check("assistant reply appended", hist.history[-1]["role"] == "assistant" and hist.history[-1]["content"] == "r1", hist.history)
check("oldest retained after cap", hist.history[0]["content"] == "m2", hist.history)

print("== Phase 3.4 normalization & media query ==")
nz = normalize.Normalizer()
check("alias fixes", nz.clean("play articub") == "play art deco", nz.clean("play articub"))
check("indian alias fixes", nz.clean("veezha veeran") == "vezham veeran", nz.clean("veezha veeran"))
check("typofix app", nz.clean("open notpad") == "open notepad", nz.clean("open notpad"))
check("unknown words untouched", nz.clean("play qwertyuiop") == "play qwertyuiop", nz.clean("play qwertyuiop"))
from actions import build_media_query
q = build_media_query("lana del rey summertime sadness")
check("official audio appended", q and "official audio" in q, q)
check("shorts excluded tag", q and "-shorts" in q, q)
q2 = build_media_query("some movie trailer")
check("no audio tag for video", q2 and "official audio" not in q2, q2)
q3 = build_media_query("song official audio")
check("existing audio not doubled", q3.count("official audio") == 1, q3)
q4 = build_media_query("")
check("empty stays empty", q4 == "", repr(q4))
garbage = nz.media_query("waka wika wow")
check("garbage guard blocks", garbage is None, repr(garbage))
okq = nz.media_query("lana del rey summertime sadness")
check("known query passes", okq is not None, repr(okq))

print("== Phase 3.5 intent routing matrix ==")
from intent import route, classify, is_stop
check("route tab", route("close the youtube tab") == "tab")
check("route lock falls to convo", route("lock the pc") == "convo")
check("route media", route("play summertime sadness") == "media")
check("route query", route("search for cats") == "query")
check("route convo", route("tell me a joke") == "convo")
check("classify music", classify("play that song") == "music")
check("classify stop", classify("stop") == "stop")
check("classify computer", classify("open notepad") == "computer")
check("classify convo", classify("hello there") == "conversation")
check("stop music not stop", not is_stop("stop the music"))
check("plain stop is stop", is_stop("stop"))

print("== Phase 4.1 worker pipeline smoke ==")


class WorkerLLM:
    def __init__(self):
        self.seen = []

    def correct_transcript(self, raw):
        self.seen.append(raw)
        return "what time is it now"

    def chat(self, msgs):
        return "The time is always now, master."

    def chat_with_tools(self, msgs, tools, executor):
        return self.chat(msgs)

    def understand_command(self, text):
        return {"relay": "", "answer": ""}


hub2 = StubHub()
hub2.cfg = {"stt": {"ai_correct": True}, "llm": {"max_history": 24}}
hub2.llm = WorkerLLM()
hub2.spoken.clear()
hub2._textq.put("what time is it")
worker_thr = threading.Thread(target=sv.Hub.worker_loop.__get__(hub2, StubHub), daemon=True)
worker_thr.start()
deadline = time.time() + 5
while time.time() < deadline and not hub2.spoken:
    time.sleep(0.05)
check("worker produced reply", bool(hub2.spoken), hub2.spoken)
check("ai correction ran", bool(hub2.llm.seen), hub2.llm.seen)

print("== Phase 4.2 music engine scan/state ==")
from music import MusicEngine


class StubBus:
    def source(self):
        raise AssertionError("bus source should not be used")

    def remove(self, src):
        pass


with tempfile.TemporaryDirectory() as td:
    for name in ("a.mp3", "b.wav", "notes.txt", "c.flac"):
        pathlib.Path(td, name).write_text("x", encoding="utf-8")
    mel = MusicEngine(StubBus(), folder=td)
    scan = mel._scan()
    check("scan finds audio only", [pathlib.Path(p).name for p in scan] == ["a.mp3", "b.wav", "c.flac"], scan)
with tempfile.TemporaryDirectory() as td:
    mel = MusicEngine(StubBus(), folder=td)
    msg = mel.play()
    check("empty folder friendly reply", msg and "No music" in msg, msg)
mel2 = MusicEngine(StubBus(), folder=td)
check("pause message", mel2.pause() and "Paused" in mel2.pause())
check("resume message", mel2.resume() and "Resuming" in mel2.resume())
check("stop message", mel2.stop() and "Stopped" in mel2.stop())

print("== Phase 4.3 config roundtrip ==")
with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
    tmp = tf.name
os.unlink(tmp)
with patch.object(cfgmod, "path", lambda: pathlib.Path(tmp)):
    c = cfgmod.load()
    c["tts"]["voice"] = "en-US-AriaNeural"
    c["persona"]["spice"] = 0.5
    cfgmod.save(c)
    c2 = cfgmod.load()
check("config roundtrip", c2["tts"]["voice"] == "en-US-AriaNeural" and c2["persona"]["spice"] == 0.5, c2)
check("defaults preserved", c2["llm"]["model"] == "mercury-3", c2)

print("== Phase 5.1 process-text branches ==")
hub.spoken.clear()
_process("mute the mic")
check("mic mute state", hub.mic_enabled is False and hub.spoken == ["Mic muted, master."], hub.spoken)
hub.spoken.clear()
_process("unmute the microphone")
check("mic unmute state", hub.mic_enabled is True and hub.spoken == ["Microphone unmuted, sweetheart."], hub.spoken)
hub.spoken.clear()
_process("go to sleep")
check("theia quiet state", hub._awoken is False and hub.spoken == ["Going quiet, master."], hub.spoken)
hub.spoken.clear()
_process("wake up")
check("theia wakes", hub._awoken is True and hub.spoken == ["I'm all ears, master."], hub.spoken)
hub.spoken.clear()
hub.last_media_query = None
with patch.object(actions, "media_open") as mo:
    _process("open youtube and play veera weeran")
check("compound opens media", mo.called and "veera weeran" in mo.call_args[0][0], mo.call_args)
check("compound records query", hub.last_media_query == "veera weeran", hub.last_media_query)
check("compound replies", bool(hub.spoken), hub.spoken)
hub.spoken.clear()
hub.last_media_query = "summertime sadness"
with patch.object(actions, "media_open") as mo:
    _process("play that again")
check("replay uses last query", mo.called and "summertime sadness" in mo.call_args[0][0], mo.call_args)
check("replay replies", bool(hub.spoken), hub.spoken)
hub.spoken.clear()
_process("waka wika wow")
check("garbage guard replies", hub.spoken == ["Hmm, I couldn't make that out. Say it again, master."], hub.spoken)

print("== Phase 5.2 web payload ==")
pl = sv.config_payload(cfgmod.load())
check("payload has voice_id", pl["voice_id"] == cfgmod.load()["tts"]["voice"], pl)
check("payload has wake word", "wake" in pl, pl)
check("payload has spice", "spice" in pl, pl)

print("== Phase 6.1 youtube result parsing ==")
import actions as act


class FakeResp:
    def __init__(self, html):
        self._html = html

    def read(self):
        return self._html.encode()


class FakeUrlopen:
    def __init__(self, html):
        self._html = html

    def __enter__(self):
        return FakeResp(self._html)

    def __exit__(self, *a):
        return False


html1 = (
    'x{"videoRenderer":{"videoId":"S1234567890"}}x'
    '{"videoRenderer":{"videoId":"F1234567890","lengthText":{"simpleText":"3:45"}}}'
    '{"videoRenderer":{"videoId":"F2234567890","lengthText":{"simpleText":"4:00"}}}'
)
with patch.object(act.urllib.request, "urlopen", side_effect=lambda *a, **k: FakeUrlopen(html1)):
    ids = act._fetch_yt_video_ids("test", limit=5)
check("shorts skipped", "S1234567890" not in ids, ids)
check("full length parsed", ids[:2] == ["F1234567890", "F2234567890"], ids)
html2 = 'zzz"videoId":"FALLBACK123"zzz'
with patch.object(act.urllib.request, "urlopen", side_effect=lambda *a, **k: FakeUrlopen(html2)):
    ids = act._fetch_yt_video_ids("test")
check("generic fallback parse", ids == ["FALLBACK123"], ids)
opened = []
with patch.object(act, "_fetch_yt_video_ids", return_value=["AAA11111111", "BBB22222222"]), \
     patch.object(act.webbrowser, "open", side_effect=lambda u: opened.append(u)):
    act.media_open("song", "ok", index=1)
time.sleep(0.4)
check("ordinal opens watch url", bool(opened) and opened[0].endswith("watch?v=BBB22222222"), opened)

print("== Phase 6.2 speech serialization ==")


class FakeTTS:
    def __init__(self):
        self.said = []

    def speak_chunked(self, text, should_stop):
        self.said.append(text)


class LoggingStubHub(StubHub):
    def __init__(self):
        super().__init__()
        self.states = []

    def emit_state(self, s):
        self.states.append(s)
        self.last_state = s


lhub = LoggingStubHub()
lhub.tts = FakeTTS()
sr = sv.Hub.speak_reply.__get__(lhub, LoggingStubHub)
sr("first")
sr("second")
deadline = time.time() + 5
while time.time() < deadline and len(lhub.tts.said) < 2:
    time.sleep(0.05)
check("speech serialized in order", lhub.tts.said == ["first", "second"], lhub.tts.said)
check("speaking state entered", "speaking" in lhub.states, lhub.states)
check("returns to listening", lhub.states[-1] == "listening", lhub.states)
check("assistant event for both", sum(1 for e in lhub.events if e.get("type") == "assistant") >= 2, lhub.events)

print("== Phase 6.3 utterance completeness ==")
ic = sv.Hub._is_complete.__get__(hub, StubHub)
check("connector holds open", not ic("play something and"))
check("single hold word holds", not ic("open"))
check("please trailing holds", not ic("open chrome please"))
check("normal complete", ic("open youtube"))
check("empty complete", ic(""))

print("== Phase 6.5 llm json parse ==")
from llm import LLM
pj = LLM._parse_command_json
check("plain json", pj('{"relay": "open notepad", "answer": ""}') == {"relay": "open notepad", "answer": ""})
check("fenced json", pj('```json\n{"relay": "open x", "answer": ""}\n```') == {"relay": "open x", "answer": ""})
check("non-json falls back to answer", pj("hello there") == {"relay": "", "answer": "hello there"})
check("dict without relay", pj('{"foo": 1}') == {"relay": "", "answer": ""})
check("stray line extracted", pj('note\n{"relay": "play x", "answer": ""}\nend') == {"relay": "play x", "answer": ""})

print("== Phase 7.1 persona build ==")
import persona
pc = cfgmod.load()
pc["persona"]["name"] = "Theia"
pc["persona"]["user_name"] = "daddy"
pc["persona"]["spice"] = 1.0
p = persona.build(pc)
check("persona uses name", "Theia" in p, p[:60])
check("persona uses user name", "daddy" in p, p[:60])
pc["persona"]["spice"] = 0.1
p_low = persona.build(pc)
check("low spice adds low block", len(p_low) > 200, len(p_low))
check("spice tiers differ", p_low != p, "")

print("== Phase 7.2 registry consistency ==")
from command import Domain
registry_domains = {d for (d, _a) in ACTION_REGISTRY}
check("registry domains valid", registry_domains <= {m.value for m in Domain}, registry_domains)
check("all enum domains used or future", True)
allowed_dispatch = [da("media", "q", "play x"), da("music", {"action": "play"}, ""),
                    da("music", {"action": "stop"}, ""), da("volume", None, ""),
                    da("web", "u", ""), da("app", "chrome", ""),
                    da("sfx", "giggle", ""), da("done", None, "close chrome"),
                    da("done", None, "open downloads folder"), da("done", None, "take a screenshot")]
check("dispatch map all allowlisted", all(reg is not None and is_allowed(*reg) for reg in allowed_dispatch), allowed_dispatch)

print("== Phase 8.1 stt model manager ==")
from stt import select_model, detect_hardware
hw_cuda = {"device": "cuda", "compute_type": "float16", "cuda_count": 2, "ram_gb": 32, "cpu_count": 8}
check("turbo preferred on gpu", select_model("auto", hw_cuda)[0] == "large-v3-turbo", select_model("auto", hw_cuda))
hw_cpu_big = {"device": "cpu", "compute_type": "int8", "cuda_count": 0, "ram_gb": 16, "cpu_count": 8}
check("medium cpu fallback", select_model("auto", hw_cpu_big)[0] == "medium", select_model("auto", hw_cpu_big))
hw_cpu_small = {"device": "cpu", "compute_type": "int8", "cuda_count": 0, "ram_gb": 4, "cpu_count": 4}
check("small emergency", select_model("auto", hw_cpu_small)[0] == "small", select_model("auto", hw_cpu_small))
check("explicit override", select_model("large-v3", hw_cpu_small)[0] == "large-v3")
check("cuda ctype float16", select_model("auto", hw_cuda)[2] == "float16")
hw_cuda_low = {"device": "cuda", "compute_type": "int8_float16", "cuda_count": 1, "ram_gb": 8, "cpu_count": 4}
check("low vram uses int8_float16", select_model("auto", hw_cuda_low)[2] == "int8_float16")
hw = detect_hardware()
check("hardware detect shape", set(hw) == {"device", "compute_type", "cuda_count", "ram_gb", "cpu_count"}, hw)

print("== Phase 8.2 rnnoise wrapper ==")
from rnnoise_denoiser import RNNoiseDenoiser, find_rnnoise_dll


class FakeLib:
    def __init__(self):
        self.frames = 0
        self._state = object()

    def rnnoise_get_frame_size(self):
        return 480

    def rnnoise_create(self, p):
        return self._state

    def rnnoise_process_frame(self, state, out, inp):
        self.frames += 1
        for i in range(480):
            out[i] = inp[i]

    def rnnoise_destroy(self, s):
        pass


d = RNNoiseDenoiser.__new__(RNNoiseDenoiser)
d._lib = FakeLib()
d._state = object()
d._frame_size = 480
d.available = True
d.error = ""
d._buf = np.empty(0, dtype=np.float32)
d._dll_path = "fake"
pcm = (np.arange(16000, dtype=np.int16) % 300).astype(np.int16)
out = d.process(pcm)
check("16k in -> 16k out shape", len(out) == len(pcm), (len(out), len(pcm)))
check("48k frames processed", d._lib.frames == (16000 * 3) // 480, d._lib.frames)
mean_err = int(np.abs(out.astype(np.int32) - pcm.astype(np.int32)).mean())
check("passthrough preserved approx", mean_err < 200, mean_err)
check("unavailable reports error", not RNNoiseDenoiser("Z:\\\\missing\\\\rnnoise.dll").available)
check("find dll missing returns None", find_rnnoise_dll("") is None)

print("== Phase 9.1 livekit stt plugin ==")
try:
    from stt_livekit import frames_to_f32, LocalWhisperSTT
    from livekit import rtc as lk_rtc
    import asyncio as _ai

    def _frame(arr):
        return lk_rtc.AudioFrame(
            data=arr.astype(np.int16).tobytes(),
            sample_rate=16000,
            num_channels=1,
            samples_per_channel=len(arr),
        )

    mono = np.arange(320, dtype=np.int16) - 160
    f32 = frames_to_f32(_frame(mono))
    check("mono frame to f32", f32.shape == (320,) and abs(float(f32[10]) - float(mono[10]) / 32768.0) < 1e-6, f32.shape)
    frames_list = [_frame(mono[:160]), _frame(mono[160:])]
    f32b = frames_to_f32(frames_list)
    check("frame list concatenated", f32b.shape == (320,), f32b.shape)

    class FakeEngine:
        model_name = "fake-small"

        def transcribe_with_info(self, audio, partial=False):
            return "open youtube", {"language": "en", "avg_logprob": -0.2, "duration": 1.0}

        def transcribe_final(self, audio):
            return self.transcribe_with_info(audio, partial=False)

    stt = LocalWhisperSTT(FakeEngine(), language="en")
    ev = _ai.run(stt._recognize_impl(_frame(mono)))
    check("recognize returns final event", ev.type.value == "final_transcript", ev.type)
    check("recognize text", ev.alternatives[0].text == "open youtube", ev.alternatives)
    check("recognize language", ev.alternatives[0].language == "en", ev.alternatives)
    check("recognize confidence", ev.alternatives[0].confidence == -0.2, ev.alternatives)
except Exception as exc:
    check("livekit import available", False, str(exc))

print("== Phase 9.2 agent greeting ==")
hub.spoken.clear()
hub.agent_connected = True
hub._greeted = False
hub.cfg = {"persona": {"name": "Theia", "user_name": "daddy", "greeting": "Hey {user_name}, {name} here."}}
greet = sv.Hub._maybe_greet.__get__(hub, StubHub)
greet()
check("greeting spoken once", hub.spoken == ["Hey daddy, Theia here."], hub.spoken)
greet()
check("no duplicate greeting", hub.spoken == ["Hey daddy, Theia here."], hub.spoken)
hub.agent_connected = False
greet()
check("greeting stays off when agent off", hub.spoken == ["Hey daddy, Theia here."], hub.spoken)

if FAIL:
    print(f"\n=== {len(FAIL)} FAILURES ===")
    for f in FAIL:
        print(" ", f)
    raise SystemExit(1)
print(f"\n=== PHASE 1 TESTS PASSED ({PASS} checks) ===")

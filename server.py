import argparse
import asyncio
import json
import os
import pathlib
import queue
import re
import threading
import time
import uuid

import numpy as np

from aiohttp import web, WSMsgType

LOG_FILE = pathlib.Path(__file__).parent / "server.log"


def _log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass
    print(msg)

import actions
from command import ACTION_REGISTRY, Command, is_allowed

import config
import intent

import persona
from audio_bus import AudioMixer
from audio_io import Listener, SpeechMonitor
from llm import LLM
from music import MusicEngine
import normalize
from rnnoise_denoiser import RNNoiseDenoiser
from session import SessionState
from sfx import Sfx
from stt import STT
from tts import TTS

LiveKitStreamingSTT = None


VOICES = {
    "en-US-JennyNeural": "Jenny - Warm Sweetheart",
    "en-US-AriaNeural": "Aria - Sultry Boss",
    "en-US-EmmaNeural": "Emma - Soft Romantic",
    "en-GB-LibbyNeural": "Libby - Young Tease",
    "en-US-AvaNeural": "Ava - Breathy Temptress",
}


_SPAM_WORDS = {
    "the", "a", "an", "so", "and", "you", "want", "get", "go",
    "game", "baby", "daddy", "master", "miss", "now", "hey",
    "video", "please", "what", "ok", "okay", "yeah",
    "lana", "theia", "like", "just", "can", "i", "me", "my",
    "thank", "thanks", "yes", "no", "hello", "hi", "right", "sure",
    "fine", "good", "great", "cool", "nice", "hmm", "hm", "oh", "uh",
    "um", "do", "did", "it", "is", "are", "was", "this", "that",
    "then", "hear", "again", "really", "very", "much", "more",
}


class Hub:
    def __init__(self, cfg):
        self.cfg = cfg
        self._stt_backend = cfg.get("stt", {}).get("backend", "whisper")
        self.persona = persona.build(cfg)
        self.llm = LLM(
            base_url=cfg["llm"]["base_url"],
            model=cfg["llm"]["model"],
            system=self.persona,
            temperature=cfg["llm"]["temperature"],
        )
        self.bus = AudioMixer()
        self.tts = TTS(voice=cfg["tts"]["voice"], rate=cfg["tts"]["rate"], volume=cfg["tts"]["volume"], bus=self.bus)
        self.tts.prewarm([
            "Mic muted, master.",
            "Microphone unmuted, sweetheart.",
            "Going quiet, master.",
            "I'm all ears, master.",
            "Hmm, I couldn't make that out. Say it again, master.",
            "I can't do that, master.",
            "I couldn't do that window action, master.",
            "Which window should I close, master?",
            "What should I play again, master?",
            "I didn't catch what to play, master.",
            "I couldn't close that, master.",
        ])
        self.music = MusicEngine(self.bus)
        self.sfx = Sfx(enabled=cfg["sfx"]["enabled"], chance=cfg["sfx"]["chance"])
        self.stt = None
        self._stt_ready = threading.Event()
        self._stt_lock = threading.Lock()
        threading.Thread(target=self._load_stt, daemon=True).start()
        self.denoiser = None
        self.noise_mode = "none"
        noise_cfg = cfg.get("noise", {})
        if noise_cfg.get("enabled", True):
            if noise_cfg.get("suppressor") == "rnnoise":
                den = RNNoiseDenoiser(noise_cfg.get("rnnoise_dll", ""))
                if den.available:
                    self.denoiser = den
                    self.noise_mode = "rnnoise"
                    _log(f"[noise] rnnoise active ({den._dll_path})")
                else:
                    _log(f"[noise] rnnoise unavailable: {den.error}")
            if self.denoiser is None and noise_cfg.get("noisereduce", True):
                self.noise_mode = "noisereduce"
        self.listener = Listener(aggressiveness=cfg["vad"]["aggressiveness"], denoiser=self.denoiser)
        self._live_enabled = bool(cfg.get("live_transcription", {}).get("enabled", False))
        self._live_cfg = cfg.get("live_transcription", {})
        self._utt_id = None
        self._live_buf = []
        self._live_start = 0.0
        self._last_partial = 0.0
        self._partial_running = False
        self._last_partial_text = ""
        self._last_level_t = 0.0
        self._audioq = queue.Queue()
        self._info_by_utt = {}
        self._active_utt_id = None
        self._latency = {}
        if self._stt_backend in ("livekit", "groq"):
            try:
                import stt_livekit  # registers livekit plugins on main thread
            except Exception:
                pass
        self.livekit_stt = None
        self._lk_finalized = set()
        self._hot_text = {}
        self._spec_cache = {}
        self._spec_prev = {}
        self._spec_busy = False
        threading.Thread(target=self.decode_loop, daemon=True).start()
        self.clients = set()
        self.history = []
        self.lock = threading.Lock()
        self.outq = queue.Queue()
        self.last_state = None
        self.mic_enabled = False
        self.agent_connected = True
        self._greeted = False
        self.wake_enabled = bool(cfg.get("wake", {}).get("enabled", False))
        self._awoken = not self.wake_enabled
        self.stop_flag = threading.Event()
        self.normalizer = normalize.Normalizer()
        self.session = SessionState(ttl=cfg.get("session", {}).get("clarifier_ttl", 120))
        self._confirm_action = None
        self._clarify = None
        self._relay_depth = 0
        self._textq = queue.Queue()
        self._speaking = False
        self._pending_text = None
        self.last_media_query = None

    def _load_stt(self):
        try:
            stt_cfg = self.cfg["stt"]
            self.stt = None
            if self._stt_backend == "groq":
                try:
                    from stt_groq import GroqSTT
                    self.stt = GroqSTT(
                        model=stt_cfg.get("groq_model", "whisper-large-v3-turbo"),
                        language=stt_cfg.get("language") or "en",
                        local_final=stt_cfg.get("model", "medium"),
                        prompt=stt_cfg.get("initial_prompt"),
                    )
                    _log("[stt] groq backend configured (whisper-large-v3-turbo + local fallback)")
                except Exception as exc:
                    _log(f"[stt] groq init failed: {exc}")
            if self._stt_backend == "google":
                try:
                    from stt_google import GoogleSTT
                    self.stt = GoogleSTT(
                        language=stt_cfg.get("google_language") or "en-US",
                        local_final=stt_cfg.get("model", "medium"),
                        prompt=stt_cfg.get("initial_prompt"),
                    )
                    _log("[stt] google web-speech backend configured (keyless + local fallback)")
                except Exception as exc:
                    _log(f"[stt] google init failed: {exc}")
            if self.stt is None:
                self.stt = STT(
                    model=stt_cfg.get("model", "auto"),
                    device=stt_cfg.get("device", "auto"),
                    compute_type=stt_cfg.get("compute_type", "auto"),
                    language=stt_cfg.get("language"),
                    initial_prompt=stt_cfg.get("initial_prompt"),
                    decode=stt_cfg.get("decode"),
                    partial_model=stt_cfg.get("partial_model", ""),
                )
                _log("[stt] model loaded")
                _log(f"[stt] {self.stt}")
                hw = self.stt.hardware
                _log(f"[stt] hardware: device={hw['device']} cuda={hw['cuda_count']} ram={hw['ram_gb']:.1f}GB cpu={hw['cpu_count']}")
            self.livekit_stt = None
            self._stt_backend = "groq" if self._stt_backend == "groq" else "whisper"
        except Exception as exc:
            _log(f"[stt] model load failed: {exc}")
        finally:
            self._stt_ready.set()

    def emit(self, payload):
        self.outq.put(payload)

    def emit_state(self, state):
        if state != self.last_state:
            self.last_state = state
            self.emit({"type": "state", "state": state})

    async def broadcast(self, payload):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def add_user(self, text):
        with self.lock:
            self.history.append({"role": "user", "content": text})
            if len(self.history) > self.cfg["llm"]["max_history"]:
                self.history = self.history[-self.cfg["llm"]["max_history"]:]

    def get_reply(self):
        with self.lock:
            try:
                _log(f"[brain] calling LLM with {len(self.history)} messages")
                reply = self.llm.chat(self.history)
                _log(f"[brain] LLM reply: {reply[:80]}...")
            except Exception as exc:
                _log(f"[brain] error: {exc}")
                reply = "Hmm â€” my brain hiccuped. Say that again?"
            self.history.append({"role": "assistant", "content": reply})
        return reply

    def clients_alive(self):
        return bool(self.clients)

    def active(self):
        return self.agent_connected and self.clients_alive()

    def is_awake(self):
        return self._awoken

    def is_stop_cmd(self, text):
        return intent.is_stop(text)

    def passes_wake(self, text):
        if not self.wake_enabled:
            return True
        import re
        low = " " + re.sub(r"[^a-z0-9\s]", " ", text.lower()) + " "
        word = self.cfg.get("wake", {}).get("word", "theia").lower()
        return f" {word} " in low or low.startswith(word + " ")

    def speak_safely(self, text):
        self.tts.speak_chunked(text, should_stop=lambda: self.stop_flag.is_set())

    def _audit_dispatch(self, domain, kind):
        if kind is None:
            return True
        if domain == "tab" and kind == "web":
            return False
        if domain == "query" and kind in ("system", "app", "done"):
            return False
        if domain == "media" and kind == "system":
            return False
        if domain == "system" and kind == "web":
            return False
        return True

    def _gate(self, domain, action, target=None, raw=""):
        cmd = Command(domain=domain, action=action, target=target, raw=raw)
        ok, why = cmd.validate()
        if not ok:
            _log(f"[gate] refused {domain}/{action}: {why}")
            self.speak_reply("I can't do that, master.")
            return None
        return cmd

    def _tab_action(self, text):
        low = " " + text.lower().strip() + " "
        if "task view" in low or "all windows" in low:
            return "task_view"
        if "desktop" in low:
            return "desktop"
        if "reopen" in low or "last tab" in low:
            return "reopen"
        if "next" in low:
            return "next"
        if "previous" in low or "prev" in low:
            return "prev"
        if "minimize" in low:
            return "minimize"
        if "maximize" in low:
            return "maximize"
        if "new" in low:
            return "new"
        if "close" in low:
            return "close"
        return None

    def _dispatch_action(self, kind, payload, text=""):
        if kind == "media":
            return "media", "play"
        if kind == "music":
            act = (payload or {}).get("action") if isinstance(payload, dict) else payload
            return "media", act or "play"
        if kind == "volume":
            return "system", "volume"
        if kind == "system":
            return ("system", payload) if payload else None
        if kind == "sfx":
            return "sfx", (payload or "giggle")
        if kind == "web":
            return "query", "open_url"
        if kind == "app":
            return "apps", "open"
        if kind == "screenshot":
            return "query", "screenshot"
        if kind == "done":
            low = " " + str(text).lower() + " "
            if "screenshot" in low:
                return "query", "screenshot"
            if any(w in low for w in ("folder", "downloads", "documents", "pictures", "videos")):
                return "files", "open_folder"
            if "close" in low:
                return "apps", "close"
            return "media", "play"
        return None

    def _process_text(self, text):
        _log(f"[process] _process_text entered: {text!r}")
        followup = self.session.resolve_followup(text)
        if followup is not None:
            self._emit_user(text, "music")
            self.last_media_query = followup
            confirm = f"Playing {text.strip()} version of that for you, baby."
            actions.media_open(followup, confirm)
            self.speak_reply(confirm)
            return
        if self.normalizer.media_query(text) is None and " " in text.strip():
            self._emit_user(text, intent.classify(text))
            self.speak_reply("Hmm, I couldn't make that out. Say it again, master.")
            return
        raw = text.strip()
        if self._clarify is not None:
            clarify = self._clarify
            if time.time() - clarify.get("at", 0) > 60:
                self._clarify = None
            elif clarify.get("kind") == "close_target":
                self._clarify = None
                self._resolve_close_target(raw)
                return
            elif clarify.get("kind") == "replay_target":
                self._clarify = None
                q = raw.strip().strip("\"'")
                if q:
                    self.last_media_query = q
                    self.session.register_media(q)
                    confirm = f"Playing {q}, master."
                    actions.media_open(q, confirm)
                    self.speak_reply(confirm)
                else:
                    self.speak_reply("I didn't catch what to play, master.")
                return
        ctrl_m = re.match(r"^(?:please\s+)?(?:theia\s+)?play\s+(?:the\s+)?(next|previous|prev)\s+(?:song|track|video|one)(?:\s+(?:on|in)\s+(?:the\s+)?(?:window|windows|screen|tab|browser|page))?\s*$", raw, re.IGNORECASE)
        if ctrl_m is not None:
            verb = "prev" if ctrl_m.group(1).lower().startswith("p") else "next"

            def ctrl_job():
                kind, payload, confirm = actions._media(
                    "previous" if verb == "prev" else "next",
                    "Previous track, honey." if verb == "prev" else "Next track, babe.",
                )
                if confirm:
                    self.speak_reply(confirm)

            threading.Thread(target=ctrl_job, daemon=True).start()
            return
        compound = re.match(r"^(?:please\s+)?(?:theia\s+)?(?:open|launch|start)\s+(?:youtube|yt)\s+and\s+play\s+(.+)$", raw, re.IGNORECASE)
        if compound is None:
            compound = re.match(r"^(?:please\s+)?(?:theia\s+)?search\s+(.+?)\s+and\s+play(?:\s+(?:it|that|this))?\s*$", raw, re.IGNORECASE)
        if compound is not None:
            q = compound.group(1).strip().strip("\"'")
            q = self.normalizer.clean(q, fix=False)
            if q:
                self.last_media_query = q
                self.session.register_media(q)
                confirm = f"Playing {q}, master."
                actions.media_open(q, confirm)
                self.speak_reply(confirm)
                return
        low = " " + text.lower().strip() + " "
        mic_m = re.search(r"\b(?:mute|unmute|un\s+mute)\s+(?:the\s+)?(?:mic|microphone)\b", low)
        if mic_m:
            enabled = bool(re.search(r"\b(?:unmute|un\s+mute)\b", low))
            self.mic_enabled = enabled
            self.emit({"type": "mic", "enabled": enabled})
            self.emit_state("listening")
            self.speak_reply("Microphone unmuted, sweetheart." if enabled else "Mic muted, master.")
            return
        if re.search(r"\b(?:go\s+to\s+sleep|sleep\s+now|take\s+a\s+nap|be\s+quiet)\b", low):
            self._awoken = False
            self.emit_state("listening")
            self.speak_reply("Going quiet, master.")
            return
        if re.search(r"\bwake\s+up\b", low) and len(text.strip().split()) <= 3:
            self._awoken = True
            self.emit({"type": "awake"})
            self.speak_reply("I'm all ears, master.")
            return
        if intent.is_user_question(text):
            reply = None
            utt = self._active_utt_id
            if utt is not None:
                hit = self._spec_cache.pop(utt, None)
                if hit is not None and hit[0] == text:
                    reply = hit[1]
            self.add_user(text)
            if reply is None:
                reply = self.get_reply()
            else:
                with self.lock:
                    self.history.append({"role": "assistant", "content": reply})
            if self.active():
                self.speak_reply(reply)
            return
        if re.search(r"\b(?:play|replay|hit)\s+that\s+again\b", low):
            if self.last_media_query:
                confirm = f"Playing {self.last_media_query} again, master."
                actions.media_open(self.last_media_query, confirm)
                self.speak_reply(confirm)
            else:
                self._clarify = {"kind": "replay_target", "at": time.time()}
                self.speak_reply("What should I play again, master?")
            return
        if re.search(r"\bclose\s+(?:it|that)\b", low):
            self._clarify = {"kind": "close_target", "at": time.time()}
            self.speak_reply("Which window should I close, master?")
            return
        domain = intent.route(text)
        self._stage(self._active_utt_id, "intent")
        if domain == "tab":
            tab_act = self._tab_action(text)
            if tab_act is None or self._gate("tab", tab_act, raw=text) is None:
                _log(f"[gate] no allowlisted tab action for {text!r}")
                self.speak_reply("I couldn't do that window action, master.")
                return
            self._emit_user(text, "tab")
            res = actions.tab_control(text)
            if res is not None and res[0] is not None:
                _kind, _payload, confirm = res
                if confirm:
                    self.speak_reply(confirm)
                return
            self.speak_reply("I couldn't do that window action, master.")
            return
        intent_kind = intent.classify(text)
        self._emit_user(text, intent_kind)
        if intent_kind in ("music", "computer"):
            kind, payload, command_reply = actions.handle(text)
            if kind is None:
                return
            if kind is not None and not self._audit_dispatch(domain, kind):
                self.speak_reply("That didn't line up â€” say it again, master.")
                return
            if kind is not None:
                reg = self._dispatch_action(kind, payload, text)
                if reg is None or not is_allowed(*reg):
                    _log(f"[gate] refused dispatch kind={kind} payload={payload!r} text={text!r}")
                    self.speak_reply("I can't do that, master.")
                    return
                if kind == "media":
                    payload = self.normalizer.media_query(payload)
                    if payload is None:
                        self.speak_reply("Hmm, I couldn't make out the title. Say it again, master.")
                        return
                    self.last_media_query = payload
                    self.session.register_media(payload)
                if kind == "music":
                    act = payload.get("action") if payload else None
                    eng_reply = None
                    if act == "play":
                        eng_reply = self.music.play(payload.get("query"))
                    elif act == "pause":
                        eng_reply = self.music.pause()
                    elif act == "resume":
                        eng_reply = self.music.resume()
                    elif act == "next":
                        eng_reply = self.music.next_track()
                    elif act == "prev":
                        eng_reply = self.music.prev_track()
                    elif act == "stop":
                        eng_reply = self.music.stop()
                    if eng_reply:
                        command_reply = eng_reply
                if kind == "sfx" and payload:
                    self.emit_state("speaking")
                    self.sfx.play(payload)
                    self.emit_state("listening")
                elif command_reply:
                    self.speak_reply(command_reply)
                return
        if self._relay_depth < 1 and intent.looks_like_request(text):
            self._relay_depth += 1
            try:
                understood = self.llm.understand_command(text)
                relay = understood.get("relay") or ""
                answer = understood.get("answer") or ""
                if relay and self._relay_allowed(relay):
                    _log(f"[nlu] {text!r} -> relay {relay!r}")
                    self._process_text(relay)
                    return
                if answer:
                    self.speak_reply(answer)
                    return
            except Exception as exc:
                _log(f"[nlu] error: {exc}")
            finally:
                self._relay_depth -= 1
        self.add_user(text)
        reply = self.get_reply()
        if self.active():
            self.speak_reply(reply)

    _RELAY_ALLOWED = (
        "play ", "pause ", "resume ", "stop ", "open ", "close ", "launch ",
        "start ", "search ", "look up ", "find ", "type ", "press ", "scroll ",
        "click ", "switch to ", "change ", "turn ",
        "minimize ", "maximize ", "show ", "hide ",
        "set ", "ask ", "watch ", "listen to ",
    )

    def _relay_allowed(self, relay):
        r = relay.strip().lower()
        return any(r.startswith(p) for p in self._RELAY_ALLOWED)

    def _resolve_close_target(self, text):
        self._clarify = None
        target = text.strip().strip("\"'").lower()
        if not target:
            self.speak_reply("Which window should I close, master?")
            return
        res = actions.tab_control(f"close {target} window")
        if res is not None and res[0] is not None:
            _kind, _payload, confirm = res
            if confirm:
                self.speak_reply(confirm)
            return
        self.speak_reply("I couldn't close that, master.")

    def speak_reply(self, text):
        _log(f"[tts] speak_reply: {text[:80]}...")
        with self.lock:
            if self._speaking:
                self._pending_text = text
                return
            self._speaking = True
        self.emit_state("speaking")

        def run():
            try:
                self._stage(self._active_utt_id, "tts_start")
                self.tts.speak_chunked(text, should_stop=lambda: self.stop_flag.is_set())
            except Exception as exc:
                _log(f"[tts] error: {exc}")
            finally:
                self.emit({"type": "assistant", "text": text})
                with self.lock:
                    self._speaking = False
                self.stop_flag.clear()
                self.emit_state("listening")
                utt = self._active_utt_id
                if utt and utt in self._latency:
                    e = self._latency[utt]
                    if "tts_start" in e:
                        parts = [f"{k}={1000.0 * (e[k] - e.get('start', e[k])):.0f}ms" for k in ("decode_done", "intent", "tts_start") if k in e]
                        _log(f"[latency] {utt} {' '.join(parts)}")
                self._drain_pending()

        threading.Thread(target=run, daemon=True).start()

    def _drain_pending(self):
        with self.lock:
            pending_reply = self._pending_text
            self._pending_text = None
        if pending_reply is not None:
            self.speak_reply(pending_reply)

    def speak_test(self, text):
        self.tts.speak(text, should_stop=lambda: False)

    _CONNECTOR_LAST = {
        "and", "or", "but", "so", "to", "the", "a", "an", "with", "for",
        "of", "in", "on", "under", "please", "can", "could", "would", "should",
        "want", "need", "like", "play", "open", "close", "search", "type",
        "click", "press", "scroll", "find", "go", "put", "try", "maybe",
        "perhaps", "then", "also", "just", "please", "please.",
    }
    _HOLD_SINGLE = {
        "play", "open", "close", "search", "type", "click", "press", "scroll",
        "find", "go", "put", "switch", "change", "turn", "start", "watch",
        "listen", "show", "set", "ask",
    }

    def _is_complete(self, text):
        words = (text or "").strip().lower().split()
        if not words:
            return True
        if words[-1] in self._CONNECTOR_LAST:
            return False
        if len(words) == 1 and words[0] in self._HOLD_SINGLE:
            return False
        return True

    _COMMAND_WORDS = {
        "stop", "play", "pause", "resume", "next", "previous", "prev",
        "mute", "unmute", "wake", "sleep", "close", "open", "hey",
        "hi", "hello", "yes", "no", "ok", "okay", "please", "thanks",
        "thank", "bye", "good", "morning", "night", "can", "hear", "me",
        "mic", "microphone", "test", "testing", "one", "two", "three",
        "loud", "clear", "fine", "well", "check", "you", "off", "on",
        "up", "down", "left", "right", "here", "there", "now", "then",
        "together", "alone", "really", "actually", "just", "maybe",
        "perhaps", "probably", "definitely", "absolutely", "sure",
        "certainly", "obviously", "apparently", "basically",
    }

    def _looks_like_hallucination(self, text):
        t = (text or "").strip()
        if not t:
            return True
        words = t.lower().split()
        clean = [w for w in words if w.isalpha()]
        if not clean:
            return True
        # Allow single words and short phrases freely — real speech is short
        if len(clean) <= 3:
            return False
        # Only flag obvious garbage: repeated same word, no vowels, etc.
        from collections import Counter
        counts = Counter(clean)
        if len(words) >= 4:
            if counts.most_common(1)[0][1] > max(2, len(words) // 2):
                return True
            unique = len(counts)
            if unique <= 1:
                return True
        # No vowels at all in any word = likely garbage
        if all(not any(c in "aeiouy" for c in w) for w in clean if len(w) > 1):
            return True
        return False

    def _maybe_speculate(self, text, utt):
        if not utt or self._spec_busy or self._speaking or not self.active():
            return
        if not self._awoken:
            return
        if self._clarify is not None or self._relay_depth > 0:
            return
        t = (text or "").strip()
        if len(t.split()) < 3:
            return
        if not self._is_complete(t) or not intent.is_user_question(t):
            return
        if self._looks_like_hallucination(t):
            return
        prev = self._spec_prev.get(utt)
        self._spec_prev[utt] = t
        if prev != t:
            return
        if utt in self._spec_cache:
            return
        self._spec_busy = True

        def job():
            try:
                with self.lock:
                    hist = list(self.history)
                msgs = hist + [{"role": "user", "content": t}]
                reply = self.llm.chat(msgs)
                if reply:
                    with self.lock:
                        self._spec_cache[utt] = (t, reply)
                        if len(self._spec_cache) > 16:
                            del self._spec_cache[next(iter(self._spec_cache))]
            except Exception as exc:
                _log(f"[speculate] error: {exc}")
            finally:
                self._spec_busy = False

        threading.Thread(target=job, daemon=True).start()

    def _start_live(self):
        self._utt_id = uuid.uuid4().hex[:12]
        self._live_buf = []
        self._live_start = time.time()
        self._last_partial = 0.0
        self._partial_running = False
        if self.livekit_stt is not None:
            utt = self._utt_id
            start = self._live_start

            def on_interim(text):
                _log(f"on_interim callback: {text!r}")
                if text:
                    self._last_partial_text = text
                    self._maybe_speculate(text, utt)
                    self.emit({
                        "type": "transcript_partial",
                        "utterance_id": utt,
                        "text": text,
                        "timestamp": start,
                        "detected_language": None,
                        "status": "partial",
                    })

            def on_final(text):
                _log(f"on_final callback: {text!r}")
                if not text:
                    return
                self._lk_finalized.add(utt)
                self.emit({
                    "type": "transcript_partial",
                    "utterance_id": utt,
                    "text": text,
                    "timestamp": start,
                    "detected_language": None,
                    "status": "final",
                })
                self._textq.put((text, utt, start))

            def on_end():
                txt = (self._last_partial_text or "").strip()
                if txt and self._is_complete(txt) and utt not in self._lk_finalized:
                    self._textq.put((txt, utt, start, "hot"))

            self.livekit_stt.start(on_interim=on_interim, on_final=on_final, on_end=on_end)

    def _emit_level(self, f32):
        now = time.time()
        if now - self._last_level_t < 0.1:
            return
        self._last_level_t = now
        rms = float(np.sqrt(np.mean(f32 ** 2)))
        self.emit({"type": "level", "rms": round(rms, 4)})

    def _feed_live(self, f32):
        self._emit_level(f32)
        if self.livekit_stt is not None:
            self.livekit_stt.push((f32 * 32768.0).astype(np.int16))
            self._live_buf.append(f32)
            self._maybe_run_partial()
            return
        if not self._live_enabled:
            return
        if self._utt_id is None:
            self._start_live()
        self._live_buf.append(f32)
        self._maybe_run_partial()

    def _maybe_run_partial(self):
        interval = float(self._live_cfg.get("partial_interval_ms", 600)) / 1000.0
        now = time.time()
        min_s = float(self._live_cfg.get("min_partial_s", 0.6))
        if now - self._live_start < min_s:
            return
        if now - self._last_partial < interval:
            return
        if self._partial_running:
            return
        self._last_partial = now
        window_s = float(self._live_cfg.get("window_max_s", 10.0))
        cap = max(1, int(window_s * self.listener.rate))
        try:
            buf = np.concatenate(self._live_buf)
        except Exception:
            return
        if len(buf) > cap:
            buf = buf[-cap:]
        rms = float(np.sqrt(np.mean(buf ** 2))) if len(buf) else 0.0
        if rms < 0.02:
            return
        self._partial_running = True
        utt = self._utt_id
        start = self._live_start

        def job():
            try:
                if self.stt is None:
                    return
                with self._stt_lock:
                    text, info = self.stt.transcribe_with_info(buf, partial=True)
                if text and not self._looks_like_hallucination(text):
                    self._last_partial_text = text
                    self._maybe_speculate(text, utt)
                    self.emit({
                        "type": "transcript_partial",
                        "utterance_id": utt,
                        "text": text,
                        "timestamp": start,
                        "detected_language": info.get("language"),
                        "status": "partial",
                    })
            except Exception as exc:
                _log(f"[live] partial error: {exc}")
            finally:
                self._partial_running = False

        threading.Thread(target=job, daemon=True).start()

    def _finalize_live(self):
        if self.livekit_stt is not None:
            self.livekit_stt.end()
        if len(self._lk_finalized) > 64:
            self._lk_finalized.clear()
        if len(self._hot_text) > 128:
            self._hot_text.clear()
        self._utt_id = None
        self._live_buf = []
        self._live_start = 0.0
        self._last_partial = 0.0
        self._partial_running = False

    def decode_loop(self):
        while True:
            item = self._audioq.get()
            try:
                audio, utt_id, t0 = item
                if self.stt is None:
                    continue
                with self._stt_lock:
                    text, info = self.stt.transcribe_with_info(audio)
                self._info_by_utt[utt_id] = info
                rms = float(np.sqrt(np.mean(np.asarray(audio, dtype=np.float32) ** 2)))
                if rms < 0.008:
                    _log(f"[filter] dropped low-energy segment rms={rms:.4f}")
                    continue
                if not text:
                    continue
                if self._looks_like_hallucination(text):
                    _log(f"[filter] dropped hallucination: {text!r}")
                    continue
                self.emit({
                    "type": "transcript_partial",
                    "utterance_id": utt_id,
                    "text": text,
                    "timestamp": t0,
                    "detected_language": info.get("language"),
                    "status": "final",
                })
                if self._speaking:
                    if self.is_stop_cmd(text):
                        self.emit({"type": "user", "text": text, "intent": "stop"})
                        self.stop_flag.set()
                        with self.lock:
                            self._pending_text = None
                        self.emit_state("listening")
                        continue
                    self.emit({"type": "ignored", "text": text, "reason": "speaking"})
                    self.emit_state("listening")
                    continue
                if self.wake_enabled and not self._awoken:
                    if not self.passes_wake(text):
                        self.emit({"type": "ignored", "text": text, "reason": "not-awake"})
                        self.emit_state("listening")
                        continue
                    self._awoken = True
                    self.emit({"type": "awake"})
                self._stage(utt_id, "start", t0)
                self._textq.put((text, utt_id, t0))
            except Exception as exc:
                _log(f"[decode] error: {exc}")

    def capture_loop(self):
        while True:
            try:
                if not self.active():
                    time.sleep(0.5)
                    continue
                if not self.mic_enabled:
                    time.sleep(0.2)
                    continue
                if self.stt is None:
                    time.sleep(0.5)
                    continue
                self.emit_state("listening")
                speaking = self._speaking
                vad = self.cfg["vad"]
                if speaking:
                    silence_after = min(vad.get("silence_after", 0.6), 0.8)
                    max_duration = min(vad.get("max_duration", 20.0), 6.0)
                    wake_timeout = min(vad.get("wake_timeout", 30.0), 8.0)
                else:
                    silence_after = vad.get("silence_after", 0.6)
                    max_duration = vad.get("max_duration", 20.0)
                    wake_timeout = vad.get("wake_timeout", 30.0)
                continuation_max = vad.get("continuation_max", 1.5)
                self._start_live()
                t0 = time.time()
                while True:
                    if self._utt_id is None:
                        self._start_live()
                    audio = self.listener.listen(
                        silence_after=silence_after,
                        max_duration=max_duration,
                        wake_timeout=(wake_timeout if not self._live_buf else 4.0),
                        alive=lambda: self.active() and self.mic_enabled,
                        ref=lambda: self.bus.playback_rms,
                        block_cb=self._feed_live,
                    )
                    if audio is None:
                        _log("[capture] listen() returned None (no utterance detected)")
                        break
                    _log(f"[capture] got audio {len(audio)} samples, rms={float(np.sqrt(np.mean(audio**2))):.4f}")
                    utt = self._utt_id or uuid.uuid4().hex[:12]
                    self._audioq.put((audio, utt, t0))
                    self._finalize_live()
                    if self.is_stop_cmd("".join(getattr(self, "_last_partial_text", "") or "")):
                        break
                    if self._speaking:
                        break
                    if time.time() - t0 > 25:
                        break
                    if self._live_buf:
                        silence_after = min(continuation_max, silence_after + 0.8)
                    else:
                        silence_after = vad.get("silence_after", 0.6)
            except Exception as exc:
                _log(f"[capture] error: {exc}")
                self.emit({"type": "mic_error", "message": str(exc)})
                self.emit_state("listening")

    def worker_loop(self):
        while True:
            raw = self._textq.get()
            try:
                if isinstance(raw, (tuple, list)):
                    parts = list(raw) + [None, None, None, None]
                    raw_text, utt_id, t0 = parts[0], parts[1], parts[2]
                    if parts[3] == "hot":
                        continue
                else:
                    raw_text, utt_id, t0 = raw, None, None
                if utt_id is not None and utt_id in self._hot_text:
                    continue
                self._active_utt_id = utt_id
                if t0 is not None:
                    self._stage(utt_id, "decode_done")
                text = raw_text
                ai_cfg = self.cfg.get("stt", {}).get("ai_correct", {})
                if isinstance(ai_cfg, dict):
                    enabled = ai_cfg.get("enabled", False)
                    min_logprob = float(ai_cfg.get("min_logprob", -0.55))
                    max_no_speech = float(ai_cfg.get("max_no_speech", 0.55))
                else:
                    enabled = bool(ai_cfg)
                    min_logprob, max_no_speech = -0.55, 0.55
                if enabled:
                    info = self._info_by_utt.get(utt_id) or {}
                    confident = (
                        info.get("avg_logprob", -99.0) >= min_logprob
                        and info.get("no_speech_prob", 1.0) <= max_no_speech
                    )
                    if not confident:
                        try:
                            corrected = self.llm.correct_transcript(raw_text)
                            if corrected and corrected != raw_text:
                                _log(f"[stt-ai] {raw_text!r} -> {corrected!r} (conf={info.get('avg_logprob')})")
                                text = corrected
                        except Exception as exc:
                            _log(f"[stt-ai] error: {exc}")
                cleaned = self.normalizer.clean(text)
                if cleaned != text:
                    _log(f"[norm] {text!r} -> {cleaned!r}")
                if self._looks_like_hallucination(cleaned):
                    _log(f"[filter] dropped hallucination: {cleaned!r}")
                    continue
                _log(f"[worker] processing: {cleaned!r}")
                self._process_text(cleaned)
            except Exception as exc:
                _log(f"[worker] error: {exc}")
            finally:
                self._active_utt_id = None
                if utt_id is not None:
                    self._hot_text[utt_id] = True

    def _maybe_greet(self):
        if self._greeted or not self.agent_connected:
            return
        g = self.cfg.get("persona", {}).get("greeting", "")
        if g:
            try:
                g = g.format(
                    user_name=self.cfg.get("persona", {}).get("user_name", "daddy"),
                    name=self.cfg.get("persona", {}).get("name", "Theia"),
                )
            except Exception:
                pass
            self.speak_reply(g)
        self._greeted = True

    def _stage(self, utt_id, key, t=None):
        if not utt_id:
            return
        t = time.time() if t is None else t
        entry = self._latency.setdefault(utt_id, {})
        entry[key] = t

    def _emit_user(self, text, intent):
        payload = {"type": "user", "text": text, "intent": intent}
        if self._active_utt_id:
            payload["utterance_id"] = self._active_utt_id
        self.emit(payload)


def config_payload(cfg):
    voices = {v: k for k, v in VOICES.items()}
    voice_id = cfg["tts"]["voice"]
    return {
        "stt": cfg["stt"].get("model", "auto"),
        "llm": cfg["llm"]["model"],
        "voice": voices.get(voice_id, voice_id),
        "voice_id": voice_id,
        "wake": cfg.get("wake", {}).get("word", "theia"),
        "spice": cfg.get("persona", {}).get("spice", 1.0),
        "stt_device": cfg["stt"].get("device", "auto"),
        "stt_compute": cfg["stt"].get("compute_type", "auto"),
        "noise_suppressor": cfg.get("noise", {}).get("suppressor", "rnnoise"),
        "live_transcription": cfg.get("live_transcription", {}).get("enabled", True),
    }


async def ws_handler(request):
    hub = request.app["hub"]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    hub.clients.add(ws)
    await ws.send_json({"type": "state", "state": "listening"})
    await ws.send_json({"type": "voices", "voices": VOICES, "current": hub.cfg["tts"]["voice"]})
    await ws.send_json({"type": "config", "config": config_payload(hub.cfg)})
    await ws.send_json({"type": "mic", "enabled": hub.mic_enabled})
    await ws.send_json({"type": "agent", "connected": hub.agent_connected})
    await ws.send_json({"type": "wake", "enabled": hub.wake_enabled})
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("type") == "voice":
                    voice = data.get("voice")
                    if voice in VOICES:
                        hub.cfg["tts"]["voice"] = voice
                        config.save(hub.cfg)
                        hub.tts.set_voice(voice)
                        await hub.broadcast({"type": "voice", "voice": voice})
                elif data.get("type") == "mic":
                    hub.mic_enabled = bool(data.get("enabled", True))
                    await hub.broadcast({"type": "mic", "enabled": hub.mic_enabled})
                elif data.get("type") == "agent":
                    hub.agent_connected = bool(data.get("connected", True))
                    if not hub.agent_connected:
                        hub._greeted = False
                    else:
                        hub._maybe_greet()
                    await hub.broadcast({"type": "agent", "connected": hub.agent_connected})
                elif data.get("type") == "test-voice":
                    hub.emit_state("speaking")
                    threading.Thread(target=hub.speak_test, args=("I am Theia.",), daemon=True).start()
                elif data.get("type") == "wake":
                    hub.wake_enabled = bool(data.get("enabled", True))
                    if not hub.wake_enabled:
                        hub._awoken = False
                    hub.cfg.setdefault("wake", {})["enabled"] = hub.wake_enabled
                    config.save(hub.cfg)
                    await hub.broadcast({"type": "wake", "enabled": hub.wake_enabled})
                elif data.get("type") == "stop":
                    if hub._speaking:
                        hub.stop_flag.set()
                        with hub.lock:
                            hub._pending_text = None
                        hub.emit_state("idle")
            elif msg.type == WSMsgType.ERROR:
                break
    finally:
        hub.clients.discard(ws)
    return ws


async def index(request):
    return web.FileResponse("web/index.html")


async def drainer(app):
    hub = app["hub"]
    while True:
        payload = await asyncio.to_thread(hub.outq.get)
        await hub.broadcast(payload)


async def start_drainer(app):
    asyncio.create_task(drainer(app))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-voice", action="store_true", help="disable the microphone loop")
    args = parser.parse_args()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def exception_handler(loop_, ctx):
        exc = ctx.get("exception")
        if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
            return
        loop_.default_exception_handler(ctx)

    loop.set_exception_handler(exception_handler)

    cfg = config.load()
    hub = Hub(cfg)

    if not args.no_voice:
        threading.Thread(target=hub.capture_loop, daemon=True).start()
        threading.Thread(target=hub.worker_loop, daemon=True).start()

    app = web.Application()
    app["hub"] = hub
    app.on_startup.append(start_drainer)
    app.router.add_get("/", index)
    app.router.add_get("/console", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/static", "web")

    _log(f"Theia web UI: http://127.0.0.1:{args.port}")

    while True:
        try:
            web.run_app(app, host="127.0.0.1", port=args.port, print=None)
            break
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            _log(f"[server] run_app crashed: {exc}; restarting in 2s")
            time.sleep(2)


if __name__ == "__main__":
    main()

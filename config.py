import json
import os
import pathlib

DEFAULTS = {
    "stt": {
        "model": "auto",
        "device": "auto",
        "compute_type": "auto",
        "backend": "whisper",
        "google_language": "en-US",
        "partial_model": "large",
        "language": None,
        "ai_correct": {"enabled": True, "min_logprob": -0.55, "max_no_speech": 0.55},
        "decode": {
            "beam_size": 5,
            "best_of": 5,
            "temperature": 0.0,
            "vad_filter": True,
            "condition_on_previous_text": False,
            "no_speech_threshold": 0.6,
        },
        "initial_prompt": "lana, theia, now, stop, pause, play, music, song, volume, open, close, click, type, press, scroll, screen, app, google, youtube, hey, baby, daddy, master, miss",
    },
    "noise": {
        "enabled": True,
        "suppressor": "rnnoise",
        "rnnoise_dll": "",
        "noisereduce": True,
    },
    "live_transcription": {
        "enabled": True,
        "partial_interval_ms": 600,
        "window_max_s": 8.0,
        "min_partial_s": 0.6,
    },
    "llm": {"base_url": "https://api.inceptionlabs.ai/v1", "model": "mercury-3", "temperature": 0.8, "max_history": 12},
    "tts": {"voice": "en-US-JennyNeural", "rate": "+8%", "volume": "+0%"},
    "sfx": {"enabled": False, "chance": 0.25},
    "vad": {
        "aggressiveness": 2,
        "silence_after": 0.5,
        "continuation_max": 1.2,
        "max_duration": 20.0,
        "wake_timeout": 30.0,
        "tail_ms": 250,
    },
    "wake": {"enabled": False, "word": "now"},
    "persona": {"name": "Theia", "spice": 1.0, "user_name": "daddy", "greeting": "Hey {user_name}, {name} here. Ready when you are."},
}


def path():
    return pathlib.Path(__file__).parent / "waifu_config.json"


def load_env():
    env_path = pathlib.Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _coerce(cfg):
    stt = cfg.get("stt", {})
    ac = stt.get("ai_correct")
    if isinstance(ac, bool):
        stt["ai_correct"] = {"enabled": ac, "min_logprob": DEFAULTS["stt"]["ai_correct"]["min_logprob"], "max_no_speech": DEFAULTS["stt"]["ai_correct"]["max_no_speech"]}
    return cfg


def load():
    load_env()
    cfg = json.loads(json.dumps(DEFAULTS))
    if path().exists():
        saved = json.loads(path().read_text(encoding="utf-8-sig"))
        for section, values in saved.items():
            if isinstance(values, dict) and isinstance(cfg.get(section), dict):
                cfg[section].update(values)
            else:
                cfg[section] = values
    return _coerce(cfg)


def save(cfg):
    path().write_text(json.dumps(cfg, indent=2), encoding="utf-8")

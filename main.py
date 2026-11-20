import argparse
import time

import config
import persona
from audio_io import Listener
from llm import LLM
from stt import STT
from tts import TTS


def selftest(cfg):
    print("== Yui selftest ==")
    listener = Listener(aggressiveness=cfg["vad"]["aggressiveness"])
    print(f"mic ok (rate {listener.rate}, chunk {listener.chunk})")
    stt = STT(model=cfg["stt"]["model"])
    print("STT model loaded")
    tts = TTS(voice=cfg["tts"]["voice"], rate=cfg["tts"]["rate"], volume=cfg["tts"]["volume"])
    tts.speak("Hehe~ hello senpai! Your waifu assistant is online.")
    print("TTS played a test line - did you hear it?")
    llm = LLM()
    print(f"Ollama model: {llm.model}")
    print("selftest done")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--spice", type=float)
    args = parser.parse_args()

    cfg = config.load()
    if args.spice is not None:
        cfg["persona"]["spice"] = max(0.0, min(1.0, args.spice))
        config.save(cfg)

    if args.selftest:
        selftest(cfg)
        return

    p = persona.build(cfg)
    name = cfg["persona"]["name"]
    print(f"{name} is awake. (persona: {name}, spice={cfg['persona']['spice']})")
    print("Press Ctrl+C to stop.")

    stt = STT(model=cfg["stt"]["model"], device=cfg["stt"]["device"], compute_type=cfg["stt"]["compute_type"])
    llm = LLM(
        base_url=cfg["llm"]["base_url"],
        model=cfg["llm"]["model"],
        system=p,
        temperature=cfg["llm"]["temperature"],
    )
    tts = TTS(voice=cfg["tts"]["voice"], rate=cfg["tts"]["rate"], volume=cfg["tts"]["volume"])
    listener = Listener(aggressiveness=cfg["vad"]["aggressiveness"])
    history = []

    while True:
        print("listening...")
        audio = listener.listen(
            silence_after=cfg["vad"]["silence_after"],
            max_duration=cfg["vad"]["max_duration"],
            wake_timeout=cfg["vad"]["wake_timeout"],
        )
        if audio is None:
            continue
        text = stt.transcribe(audio)
        if not text:
            print("(didn't catch that)")
            continue
        print(f"you: {text}")
        history.append({"role": "user", "content": text})
        reply = llm.chat(history)
        history.append({"role": "assistant", "content": reply})
        if len(history) > cfg["llm"]["max_history"]:
            history = history[-cfg["llm"]["max_history"]:]
        print(f"{name}: {reply}")
        tts.speak(reply)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBye~ come back soon.")
    except Exception as exc:
        print(f"error: {exc}")

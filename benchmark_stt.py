import argparse
import os
import pathlib
import subprocess
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).parent
FIXTURES = [
    ("open youtube and play veera weeran", "open youtube and play veera weeran"),
    ("play summertime sadness by lana del rey", "play summertime sadness by lana del rey"),
    ("open notepad and type hello world", "open notepad and type hello world"),
    ("what is the weather today", "what is the weather today"),
    ("stop the music", "stop the music"),
    ("search for best biryani near me", "search for best biryani near me"),
    ("close the browser tab", "close the browser tab"),
    ("set volume to fifty percent", "set volume to fifty percent"),
]


def levenshtein(a, b):
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            cur = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
            prev = cur
    return dp[n]


def wer(ref, hyp):
    rt = ref.lower().split()
    ht = hyp.lower().split()
    if not rt:
        return 0.0 if not ht else 1.0
    return levenshtein(rt, ht) / len(rt)


def add_noise(audio, sr, level=0.02):
    rng = np.random.default_rng(42)
    noise = rng.normal(0.0, level, audio.shape)
    out = audio + noise
    peak = np.max(np.abs(out))
    if peak > 1.0:
        out = out / peak
    return out.astype(np.float32)


def load_wav(path, sr=16000):
    import scipy.io.wavfile as wavfile
    rate, data = wavfile.read(path)
    if data.dtype == np.int16:
        data = data.astype(np.float32) / 32768.0
    if len(data.shape) > 1:
        data = data.mean(axis=1)
    if rate != sr:
        from scipy.signal import resample_poly
        data = resample_poly(data, sr, rate)
    return data.astype(np.float32)


def generate_fixtures(outdir, voices=("en-GB-LibbyNeural", "en-IN-NeerjaNeural")):
    outdir = pathlib.Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    generated = []
    for i, (name, ref) in enumerate(FIXTURES):
        voice = voices[i % len(voices)]
        mp3 = outdir / f"{i:02d}.mp3"
        wav = outdir / f"{i:02d}.wav"
        if not wav.exists():
            subprocess.run(
                [sys.executable, "-m", "edge_tts", "--text", ref, "--voice", voice, "--write-media", str(mp3)],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3),
                 "-ar", "16000", "-ac", "1", str(wav)],
                check=True, capture_output=True,
            )
        generated.append((name, ref, wav))
    return generated


def run_model(args, model_name, wavs):
    sys.path.insert(0, str(ROOT))
    from stt import STT
    stt = STT(model=model_name, device=args.device, compute_type=args.compute_type)
    results = []
    for i, (name, ref, wav) in enumerate(wavs):
        audio = load_wav(wav)
        if args.noise == "added":
            audio = add_noise(audio, 16000)
        t0 = time.time()
        text, info = stt.transcribe_with_info(audio)
        elapsed = time.time() - t0
        results.append((name, ref, text, wer(ref, text), elapsed, info))
        print(f"  [{model_name}] {name}: WER={wer(ref, text):.2f} t={elapsed*1000:.0f}ms -> {text!r}")
    return results


def summarize(model_name, results):
    n = len(results)
    mean_wer = sum(r[3] for r in results) / n
    mean_t = sum(r[4] for r in results) / n
    print(f"== {model_name}: avg WER={mean_wer:.3f}, avg latency={mean_t*1000:.0f}ms ==")
    return {"model": model_name, "wer": mean_wer, "latency_ms": mean_t * 1000}


def main():
    ap = argparse.ArgumentParser(description="Theia STT benchmark")
    ap.add_argument("--models", nargs="+", default=["small"], help="whisper models to compare")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--compute-type", default="auto")
    ap.add_argument("--noise", choices=["raw", "added"], default="raw")
    ap.add_argument("--fixtures", default=str(ROOT / "bench_fixtures"))
    ap.add_argument("--limit", type=int, default=len(FIXTURES))
    args = ap.parse_args()

    print("hardware:", end=" ")
    try:
        from stt import detect_hardware
        print(detect_hardware())
    except Exception as exc:
        print(f"detect failed: {exc}")

    fixtures = generate_fixtures(args.fixtures)[: args.limit]
    print(f"{len(fixtures)} fixtures ready in {args.fixtures}")

    summary = []
    for model in args.models:
        res = run_model(args, model, fixtures)
        summary.append(summarize(model, res))
    print("\n=== SUMMARY ===")
    for s in summary:
        print(s)


if __name__ == "__main__":
    main()

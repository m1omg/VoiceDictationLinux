"""Auto mode on dictations that switch between English and Slovak, using the real Worker code: FLEURS
sentences joined with pauses (0.35-1 s), and the first second or two of a sentence as a short part.
Each dictation is run without live typing (the final pass alone), and with --live also as it would be
typed while speaking (safe and instant mode, as tests/bench_live.py does it, one pass per 0.4 s of
real time, so the graphics card works as hard as in a real dictation). Reports the word error rate
per kind of dictation and how long the final pass took; single-language dictations of three
sentences must not get worse. --quick: 8 dictations instead of 36 (the live modes take about as long
as the recordings: about 4 minutes).

    ~/.local/share/dictate/venv/bin/python tests/bench_mixed.py [MODEL] [--live] [--quick]
    (DICTATE_DIR=. runs this folder's dictate.py instead of the installed one)

Reference (CachyOS, RTX 3060, large-v3-turbo), one language per dictation -> per-part detection:
without live typing, mixed 44 % -> 5 %, with a short part 34 % -> 14 % (--quick: 8 %), one language
unchanged (2 % / 13 %, which includes Whisper dropping a sentence of a joined pair); final pass of a
mixed dictation 0.75 s -> 1.35 s, one language unchanged (~0.8-1.1 s). Live typing (--quick): mixed
6 %, with a short part 9 %; one language typed before release 100 % (en) / 96 % (sk), as before.
large-v3 (--quick): mixed 41 % -> 5 %, with a short part 38 % -> 8 %.
"""
import random
import sys
import time

import numpy as np
from _common import audio, errors, load_dictate, load_model, sentences

d = load_dictate()
cfg = d.load_config()
tr = load_model(d, cfg)
from faster_whisper.vad import VadOptions, get_speech_timestamps  # noqa: E402 (AMD: only after load() preloads ROCm)

RATE = d.RATE
rng = random.Random(5)
print("model:", tr.desc, "| dictate.py:", d.__file__, flush=True)


def trimmed(a):
    stamps = get_speech_timestamps(a, VadOptions(min_silence_duration_ms=2000, speech_pad_ms=100))
    return a[stamps[0]["start"]:stamps[-1]["end"]] if stamps else a


pool = {}
for lang in ("en", "sk"):
    pool[lang] = []
    for item in sentences(lang):
        a = trimmed(audio(item))
        ends = [w.end for seg in tr.run(a, lang, words=True) for w in (seg.words or [])]
        pool[lang].append((a, item["text"], ends))


def short(lang):
    """About the first 1.2-2.5 s of a sentence, cut after a word (its text: the words heard there)."""
    a, _text, ends = rng.choice(pool[lang])
    seconds = rng.choice([1.2, 1.6, 2.0, 2.5])
    end = next((e for e in ends if e >= seconds), ends[-1])
    piece = a[: int((end + 0.1) * RATE)]
    heard = " ".join(seg.text.strip() for seg in tr.run(piece, lang))
    return piece, heard


def dictation(parts, pause):
    noise = (np.random.default_rng(1).standard_normal(int(pause * RATE)) * 3e-4).astype(np.float32)
    pieces = []
    for k, (a, _text) in enumerate(parts):
        pieces += ([noise] if k else []) + [a]
    return np.concatenate(pieces), " ".join(text for _a, text in parts)


tests = []  # (kind, audio, reference)
patterns = [("sk", "en"), ("en", "sk"), ("sk", "en", "sk"), ("en", "sk", "en")]
for i in range(16):
    pattern = patterns[i % 4]
    tests.append(("mixed", *dictation([rng.choice(pool[lang])[:2] for lang in pattern], [0.35, 0.6, 1.0][i % 3])))
for i in range(12):
    pattern = patterns[i % 4]
    where = i % len(pattern)  # the short part: first, second or last
    parts = [short(lang) if k == where else rng.choice(pool[lang])[:2] for k, lang in enumerate(pattern)]
    tests.append(("with a short part", *dictation(parts, [0.35, 0.6][i % 2])))
for lang in ("en", "sk"):
    for i in range(4):
        tests.append((f"one language ({lang})", *dictation(rng.sample([p[:2] for p in pool[lang]], 3), 0.5)))
if "--quick" in sys.argv:  # 3 mixed, 3 with a short part, one of each language
    tests = tests[:3] + tests[16:19] + tests[28:29] + tests[32:33]
if "--one-language" in sys.argv:  # only how live typing keeps up in a dictation that doesn't switch
    tests = [t for t in tests if t[0].startswith("one language")][::4]


class FakeRecording:
    def __init__(self, samples):
        self.samples, self.t = samples, 0.0

    def seconds(self):
        return self.t

    def snapshot(self):
        return self.samples[: int(self.t * RATE)].copy()


class Collect:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


def run(samples, live, instant=False):
    """The text the app would end up with, the final pass's seconds, and the share of its words typed
    before release (live typing)."""
    out = Collect()
    worker = d.Worker(cfg, tr, out, d.Cues(lambda: False, 0.5), d.Quiet())
    s = d.Session(1, FakeRecording(samples), "auto", live, 0.0, text=d.LiveText(cfg), instant=instant)
    if live:
        t, began = worker.STEP, time.monotonic()
        while t < len(samples) / RATE:
            time.sleep(max(0.0, began + t - time.monotonic()))  # (no faster than the speech itself)
            s.rec.t = t
            worker._live(s)
            t += worker.STEP
    before = len((s.shown if instant else s.text.typed).split())
    s.audio, s.t_release = samples, 1.0
    started = time.monotonic()
    worker._final(s)
    seconds = time.monotonic() - started
    field = ""
    for item in out.items:
        field = (field[:len(field) - item.delete] if item.delete else field) + item.text
    return field, seconds, before / max(len(field.split()), 1)


modes = [("no live typing", False, False)] + ([("live, safe", True, False), ("live, instant", True, True)]
                                             if "--live" in sys.argv else [])
for name, live, instant in modes:
    totals = {}
    for kind, samples, reference in tests:
        text, seconds, share = run(samples, live, instant)
        e, n = errors(reference, text)
        t = totals.setdefault(kind, [0, 0, [], 0, []])
        t[0], t[1], t[3] = t[0] + e, t[1] + n, t[3] + (e > 0.25 * n)
        t[2].append(seconds)
        t[4].append(share)
    print(name)
    for kind, (e, n, seconds, bad, shares) in totals.items():
        print(f"  {kind:22} WER {e / n:6.1%}  more than a quarter wrong: {bad}/{len(seconds)}  "
              f"final pass median {np.median(seconds):.2f} s"
              + (f"  typed before release {np.mean(shares):.0%}" if live else ""), flush=True)

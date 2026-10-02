"""Live typing ("Type while speaking") on recorded speech, using the real Worker code.

Feeds each recording to Worker._live() as if it were being spoken (a pass every Worker.STEP
seconds of audio), then runs the final pass, and reports:
  * single sentences: when the first words would be typed, and how much was typed before release
  * long dictations (6 sentences): live WER versus the one-shot WER

Reference (CachyOS, RTX 3060): first words ~1.9 s (en) / ~3.3 s (sk) after the recording starts,
~90-95% typed before release, auto mode +~1 s and 0/8 wrong language; live WER equal to one-shot
(en 1.3% / 6.2%, sk 5.7% / 8.7% on two 6-sentence sets).
"""
import time

import numpy as np
from _common import audio, errors, load_dictate, sentences, words

d = load_dictate()
cfg = d.load_config()
tr = d.Transcriber(cfg)
tr.load()


class FakeRecording:
    def __init__(self, samples):
        self.samples, self.t = samples, 0.0

    def seconds(self):
        return self.t

    def snapshot(self):
        return self.samples[: int(self.t * d.RATE)].copy()


class Collect:  # stands in for the paster
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


def simulate(samples, mode):
    out = Collect()
    worker = d.Worker(cfg, tr, out, d.Cues(lambda: False, 0.5), d.Quiet())
    s = d.Session(1, FakeRecording(samples), mode, True, 0.0, language=None if mode == "auto" else mode,
                  text=d.LiveText(cfg))
    first = None
    clock, t, total = 0.0, worker.STEP, len(samples) / d.RATE
    while t < total:  # a pass starts once its audio exists and the GPU is free
        s.rec.t = t
        before, started = len(out.items), time.monotonic()
        worker._live(s)
        clock = max(clock, t) + (time.monotonic() - started)
        if first is None and len(out.items) > before:
            first = clock
        t += worker.STEP
    typed_live = len(words(s.text.typed))
    s.audio, s.t_release = samples, 1.0
    worker._final(s)
    text = "".join(item.text for item in out.items)
    return first, typed_live / max(len(words(text)), 1), text, s.language


for lang in ("en", "sk"):
    items = sentences(lang)
    for mode in (lang, "auto"):
        firsts, shares, wrong = [], [], 0
        for item in items[:8]:
            a = audio(item)
            first, share, _text, got = simulate(a, mode)
            firsts.append(first if first is not None else len(a) / d.RATE)
            shares.append(share)
            wrong += got != lang
        print(f"{lang} sentences, mode={mode:4}: first words after median {np.median(firsts):.1f} s, "
              f"{np.mean(shares):.0%} typed before release" + (f", wrong language {wrong}/8" if mode == "auto" else ""))
    for start in (0, 6):
        group = items[start:start + 6]
        gap = np.zeros(int(0.35 * d.RATE), np.float32)
        a = np.concatenate([np.concatenate([audio(item), gap]) for item in group])
        ref = " ".join(item["text"] for item in group)
        _first, _share, live_text, _ = simulate(a, lang)
        one_shot = " ".join(seg.text.strip() for seg in tr.run(a, lang))
        e1, n = errors(ref, live_text)
        e2, _ = errors(ref, one_shot)
        print(f"{lang} {len(a) / d.RATE:.0f} s dictation: live WER {e1 / n:.1%}, one-shot WER {e2 / n:.1%}")

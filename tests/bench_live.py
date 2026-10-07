"""Live typing ("Type while speaking") on recorded speech, using the real Worker code.

Feeds each recording to Worker._live() as if it were being spoken (a pass every Worker.STEP
seconds of audio), then runs the final pass, and reports:
  * single sentences: when the first words would be typed, and how much was typed before release
  * long dictations (6 sentences): live WER versus the one-shot WER

Reference (CachyOS, RTX 3060): first words ~1.9 s (en) / ~3.3 s (sk) after the recording starts,
~90-95% typed before release, auto mode +~1 s and 0/8 wrong language; live WER equal to one-shot
(en 1.3% / 6.2%, sk 5.7% / 8.7% on two 6-sentence sets).
LMDE 7, RX 6700 XT (ROCm), with words also typed at a pause after a sentence: first words 2.0 s
(en) / 4.2 s (sk), 97-98% typed before release, auto mode 0/8 wrong; live WER en 2.7% / 6.2%
(one-shot 1.3% / 6.2%), sk 6.8% / 10.4% (one-shot 6.8% / 7.8%).
"""
import sys
import time

import numpy as np
from _common import audio, errors, load_dictate, load_model, sentences, words

d = load_dictate()
cfg = d.load_config()
tr = load_model(d, cfg)


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


def simulate(samples, mode, instant=False):
    """Returns (first words typed after, share typed before release, final text, language, characters
    taken back). In instant mode the items edit a stand-in text field (delete, then type)."""
    out = Collect()
    worker = d.Worker(cfg, tr, out, d.Cues(lambda: False, 0.5), d.Quiet())
    s = d.Session(1, FakeRecording(samples), mode, True, 0.0, language=None if mode == "auto" else mode,
                  text=d.LiveText(cfg), instant=instant)
    first = None
    clock, t, total = 0.0, worker.STEP, len(samples) / d.RATE
    while t < total:  # a pass starts once its audio exists and the GPU is free
        s.rec.t = t
        before, started = len(out.items), time.monotonic()
        worker._live(s)
        clock = max(clock, t) + (time.monotonic() - started)
        if first is None and any(item.text for item in out.items[before:]):
            first = clock
        t += worker.STEP
    typed_live = len(words(s.shown if instant else s.text.typed))
    s.audio, s.t_release = samples, 1.0
    worker._final(s)
    field, taken_back = "", 0
    for item in out.items:  # instant mode deletes; the safe mode only ever adds
        field = (field[:len(field) - item.delete] if item.delete else field) + item.text
        taken_back += item.delete
    return first, typed_live / max(len(words(field)), 1), field, s.language, taken_back


QUICK = "--quick" in sys.argv  # fewer sentences, and long dictations only in instant mode
for lang in ("en", "sk"):
    items = sentences(lang)
    count = 6 if QUICK else 8
    for mode, instant in ((lang, False), ("auto", False), (lang, True), ("auto", True)):
        firsts, shares, wrong, back, errs, total = [], [], 0, [], 0, 0
        for item in items[:count]:
            a = audio(item)
            first, share, text, got, taken_back = simulate(a, mode, instant)
            firsts.append(first if first is not None else len(a) / d.RATE)
            shares.append(share)
            wrong += got != lang
            back.append(taken_back)
            e, n = errors(item["text"], text)
            errs, total = errs + e, total + n
        print(f"{lang} sentences, {'instant' if instant else 'safe':7} mode={mode:4}: first words after median "
              f"{np.median(firsts):.1f} s, {np.mean(shares):.0%} typed before release, WER {errs / total:.1%}"
              + (f", {np.median(back):.0f} characters taken back" if instant else "")
              + (f", wrong language {wrong}/{count}" if mode == "auto" else ""))
    for start in ((0,) if QUICK else (0, 6)):
        group = items[start:start + 6]
        gap = np.zeros(int(0.35 * d.RATE), np.float32)
        a = np.concatenate([np.concatenate([audio(item), gap]) for item in group])
        ref = " ".join(item["text"] for item in group)
        one_shot = " ".join(seg.text.strip() for seg in tr.run(a, lang))
        e2, n = errors(ref, one_shot)
        line = f"{lang} {len(a) / d.RATE:.0f} s dictation: one-shot WER {e2 / n:.1%}"
        for instant in ((True,) if QUICK else (False, True)):
            _first, _share, live_text, _, taken_back = simulate(a, lang, instant)
            e1, _ = errors(ref, live_text)
            line += f", {'instant' if instant else 'live'} WER {e1 / n:.1%}" + (f" ({taken_back} characters taken back)" if instant else "")
        print(line)

"""One-shot accuracy, speed and auto-detect accuracy on the FLEURS sentences.

Reference (CachyOS, RTX 3060, large-v3-turbo fp16): English WER ~3.9%, Slovak ~7.0%,
~0.35 s per ~9 s sentence, auto-detect 15/15 and 30/30.
LMDE 7, RX 6700 XT (ROCm): English 3.9%, Slovak 5.9%, 0.40 s / 0.48 s, auto-detect 15/15 and 30/30.
"""
import time

import numpy as np
from _common import audio, errors, load_dictate, load_model, sentences

d = load_dictate()
cfg = d.load_config()
tr = load_model(d, cfg)
print("model:", tr.desc)
for lang in ("en", "sk"):
    items = sentences(lang)
    errs = total = detected = 0
    seconds = []
    for item in items:
        a = audio(item)
        started = time.monotonic()
        text = " ".join(seg.text.strip() for seg in tr.run(a, lang))
        seconds.append(time.monotonic() - started)
        e, n = errors(item["text"], text)
        errs, total = errs + e, total + n
        detected += tr.detect(a)[0] == lang
    print(f"{lang}: WER {errs / total:.1%} over {len(items)} sentences, median {np.median(seconds):.2f} s per "
          f"sentence, auto-detect {detected}/{len(items)} correct")

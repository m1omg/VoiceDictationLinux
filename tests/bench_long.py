"""Long dictations in one language: 1-4 different FLEURS sentences joined with pauses of 0.4-1.2 s
(5-57 s), transcribed by the app's pass (Transcriber.run). Reports the word error rate and how many
dictations lost more than a quarter of their words (a sentence left out).

    ~/.local/share/dictate/venv/bin/python tests/bench_long.py [MODEL] [--quick]
    (DICTATE_DIR=. runs this folder's dictate.py instead of the installed one)

Reference (CachyOS, RTX 3060, large-v3-turbo, 20 dictations each), one window -> pieces of at most
Transcriber.PIECE_LIMIT s of speech: Slovak 1-4 sentences 6.4 / 8.8 -> 8.3 / 8.5 -> 6.7 / 12.8 -> 6.1 %
(more than a quarter wrong: 0 / 1 -> 1 / 1 -> 0 / 3 -> 0 of 20), English 2.7 / 3.3 / 4.6 / 4.2 -> 3.9 %;
4 Slovak sentences 0.85 -> 1.09 s. (Those figures are for always cutting. Since then pieces only replace a
Slovak pass that wrote little, when they bring back words: Transcriber._recover_lost.)
"""
import random
import sys
import time

import numpy as np
from _common import audio, errors, load_dictate, load_model, sentences

d = load_dictate()
cfg = d.load_config()
tr = load_model(d, cfg)
print("model:", tr.desc, "| dictate.py:", d.__file__, flush=True)
RATE = d.RATE
count = 8 if "--quick" in sys.argv else 20
for per in (1, 2, 3, 4):
    for lang in ("sk", "en"):
        rng = random.Random(7)  # the same dictations for every model and version
        items = sentences(lang)
        errs = total = lost = 0
        seconds, lengths = [], []
        for _ in range(count):
            while True:  # (FLEURS has some sentences twice, by different speakers)
                chosen = rng.sample(items, per)
                if len({item["text"] for item in chosen}) == per:
                    break
            pieces = []
            for item in chosen:
                pieces += [audio(item), np.zeros(int(rng.uniform(0.4, 1.2) * RATE), np.float32)]
            samples = np.concatenate(pieces)
            lengths.append(len(samples) / RATE)
            started = time.monotonic()
            text = " ".join(seg.text.strip() for seg in tr.run(samples, lang))
            seconds.append(time.monotonic() - started)
            e, n = errors(" ".join(item["text"] for item in chosen), text)
            errs, total, lost = errs + e, total + n, lost + (e > 0.25 * n)
        print(f"{per} sentence{'s' if per > 1 else ' '} {lang} ({min(lengths):.0f}-{max(lengths):.0f} s): WER {errs / total:5.1%}, "
              f"more than a quarter wrong {lost}/{count}, median {np.median(seconds):.2f} s", flush=True)

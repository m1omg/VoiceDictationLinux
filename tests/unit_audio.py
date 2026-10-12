"""The PortAudio recorder's rate conversion (Windows and macOS, when a microphone can't record at
16 kHz itself): converting block by block, in blocks of any size, gives exactly what converting the
whole recording at once gives.

    python3 tests/unit_audio.py        (any Python 3.11+ with numpy)
"""
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("XDG_RUNTIME_DIR", "/tmp")
import dictate as d  # noqa: E402

checks = []
rng = np.random.default_rng(0)
for rate in (48000, 44100, 32000, 22050):
    x = np.sin(np.arange(rate * 2) / rate * 2 * np.pi * 440).astype(np.float32)
    rec = d.PortAudioRecorder(lambda *a: None)
    rec.rate, rec.carry, rec.pos = float(rate), np.zeros(0, np.float32), 0.0
    out, i = [], 0
    while i < len(x):
        n = int(rng.integers(1, 3000))  # sound cards deliver blocks of any size, even 1 sample
        out.append(rec._resample(x[i:i + n]))
        i += n
    y = np.concatenate(out)
    ref = np.interp(np.arange(len(y)) * rate / d.RATE, np.arange(len(x)), x)
    checks.append((f"{rate} Hz: {len(y)} samples for 2 s, same as converting at once",
                   abs(len(y) - 2 * d.RATE) <= 1 and np.abs(y - ref).max() < 1e-6))
# keep_recordings: the last N dictations as 16 kHz WAV files, older ones deleted, none when it is 0
import tempfile  # noqa: E402
import time  # noqa: E402
import types  # noqa: E402
import wave  # noqa: E402

folder = Path(tempfile.mkdtemp(prefix="dictate-test-")) / "recordings"
voice = (np.sin(np.arange(d.RATE) / d.RATE * 2 * np.pi * 220) * 0.5).astype(np.float32)
d.keep_recording(types.SimpleNamespace(keep_recordings=0), voice, "auto-sk-1.0s", folder)
checks.append(("keep_recordings = 0 keeps nothing", not folder.exists()))
for k in range(3):
    d.keep_recording(types.SimpleNamespace(keep_recordings=2), voice, f"auto-sk-{k}", folder)
    time.sleep(1.05)  # (the names are by the second)
kept = sorted(folder.glob("*.wav"))
checks.append(("keep_recordings = 2 keeps the newest two", [p.name.split("_")[-1] for p in kept] == ["auto-sk-1.wav", "auto-sk-2.wav"]))
with wave.open(str(kept[-1])) as w:
    back = np.frombuffer(w.readframes(w.getnframes()), "<i2") / 32767
    checks.append(("a kept recording is the audio, 16 kHz mono", (w.getframerate(), w.getnchannels()) == (16000, 1)
                   and np.abs(back - voice).max() < 1e-3))
checks.append(("only this user may read them", (folder.stat().st_mode & 0o777) == 0o700 or sys.platform == "win32"))
for name, good in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}")
sys.exit(0 if all(good for _, good in checks) else 1)

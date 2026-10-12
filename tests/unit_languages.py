"""Auto mode on dictations that switch between English and Slovak, without a model: a stand-in
recording whose samples say the language of each stretch (+ Slovak, - English, 0 a pause), a stand-in
voice activity detection and language detection. Checked: how the evidence decides each stretch's
language, parts that end in the middle of pauses, the final pass transcribing each part in its own
language, a part in the other language confirmed (or not) on its own, and live typing switching
language at a pause without typing a word in the wrong language.

    python3 tests/unit_languages.py        (any Python 3.11+ with numpy)
"""
import os
import sys
import tempfile
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="dictate-test-"))
os.environ.update(XDG_STATE_HOME=str(TMP / "state"), XDG_CONFIG_HOME=str(TMP / "config"), XDG_RUNTIME_DIR=str(TMP),
                  LOCALAPPDATA=str(TMP), HOME=str(TMP), USERPROFILE=str(TMP))
import dictate as d  # noqa: E402

RATE = d.RATE
checks = []


def check(name, got, want):
    checks.append((name, got == want, got, want))


# --- the decision, on its own ----------------------------------------------------------------------
sure_sk, sure_en = {"en": 0.01, "sk": 0.99}, {"en": 0.99, "sk": 0.01}
check("Slovak evidence is positive, English negative",
      (d.language_evidence(3.0, sure_sk) > 0, d.language_evidence(3.0, sure_en) < 0), (True, True))
check("an English verdict on 1.5 s of speech counts for less than a Slovak one",
      abs(d.language_evidence(1.5, sure_en)) < abs(d.language_evidence(1.5, sure_sk)) / 2, True)
check("no detection is no evidence", d.language_evidence(3.0, None), 0.0)
strong_sk, strong_en = d.language_evidence(4.0, sure_sk), d.language_evidence(4.0, sure_en)
check("one language stays one", d.decide_languages([strong_sk, strong_sk, 0.0, strong_sk]), ["sk"] * 4)
check("Slovak, then English", d.decide_languages([strong_sk, strong_sk, strong_en]), ["sk", "sk", "en"])
check("English, Slovak, English", d.decide_languages([strong_en, strong_sk, strong_en]), ["en", "sk", "en"])
weak_en = d.language_evidence(1.4, sure_en)
check("1.4 s that seems English among Slovak stays Slovak", d.decide_languages([strong_sk, weak_en, strong_sk]),
      ["sk"] * 3)
check("a stretch without evidence goes with its neighbours", d.decide_languages([strong_en, 0.0, strong_en]), ["en"] * 3)
check("nothing to decide", d.decide_languages([]), [])
check("parts end in the middle of the pauses, the first from 0, the last to the end",
      d.language_runs([(100, 200), (300, 400), (600, 900)], ["sk", "sk", "en"], 1000),
      [(0, 500, "sk"), (500, 1000, "en")])
check("one part covers it all", d.language_runs([(100, 200)], ["en"], 1000), [(0, 1000, "en")])

# --- a stand-in recording: + Slovak, - English, 0 a pause; a stand-in VAD and language detection ----


def recording(*stretches):
    """("sk", 3.0) speaks Slovak for 3 s, (None, 0.6) is a pause; "sk?" and "en?" sound unsure. Every
    0.5 s of speech is one word, numbered: the samples carry the language (the sign), how sure it
    sounds (0.3 and more: sure, below: unsure) and the word's number (in thousandths)."""
    pieces, word = [], 0
    for language, seconds in stretches:
        for k in range(int(round(seconds / 0.5))):
            if language is None:
                value = 0.0
            else:
                value = (0.3 if language in ("sk", "en") else 0.1) + 0.001 * word
                value *= 1 if language.startswith("sk") else -1
                word += 1
            pieces.append(np.full(RATE // 2, value, np.float32))
    return np.concatenate(pieces)


def word_of(value: float) -> tuple[str, int, bool]:
    """(language, number, sure) of a sample."""
    size = abs(value)
    base = 0.3 if size >= 0.3 else 0.1
    return ("sk" if value > 0 else "en"), round((size - base) / 0.001), base == 0.3


def fake_regions(audio, pause=d.PART_PAUSE):
    talking = np.abs(audio) > 0.01
    regions, start, quiet = [], None, 0
    for i in range(0, len(audio), 160):  # 10 ms steps
        if talking[i]:
            if start is None:
                start = i
            elif quiet >= pause * RATE:
                regions.append((start, last_end))
                start = i
            quiet, last_end = 0, min(i + 160, len(audio))
        elif start is not None:
            quiet += 160
    if start is not None:
        regions.append((start, last_end))
    return regions


d.speech_regions = fake_regions
d.speech_total = lambda audio: sum(end - start for start, end in fake_regions(audio)) / RATE
d.speech_seconds = lambda audio: (len(audio) - fake_regions(audio)[0][0]) / RATE if fake_regions(audio) else 0.0
d.silence_seconds = lambda audio: (len(audio) - fake_regions(audio)[-1][1]) / RATE if fake_regions(audio) else len(audio) / RATE
detections = []


def fake_shares(piece, frames=d.Transcriber.PIECE_FRAMES, allowed=d.AUTO_LANGUAGES):
    """What the samples say: sure speech 99 %; unsure Slovak 95 % padded to 10 s but 60 % padded to
    30 s, unsure English 85 % either way."""
    detections.append(frames)
    talking = piece[np.abs(piece) > 0.01]
    if not talking.size:
        return {"en": 0.5, "sk": 0.5}
    language, _number, sure = word_of(float(np.median(talking)))
    sk = 0.99 if sure else (0.95 if frames < 3000 else 0.6) if language == "sk" else 0.15
    sk = sk if language == "sk" else (0.01 if sure else 0.15)
    return {"en": 1 - sk, "sk": sk}


class Transcriber(d.Transcriber):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.on_cpu, self.passes = False, []
        self.ready.set()

    def shares(self, piece, frames=d.Transcriber.PIECE_FRAMES, allowed=d.AUTO_LANGUAGES):
        return fake_shares(piece, frames, allowed)

    def detect(self, audio, allowed=d.AUTO_LANGUAGES):
        shares = fake_shares(audio[: 30 * RATE])
        best = max(shares, key=shares.get)
        return best, shares[best]

    def run(self, audio, language, *, words=False, prompt=None, beam_size=None, hotwords=True):
        """A pass "hears" every 0.5 s of speech as one word: the word is its language as heard, the
        language the pass is in if it is the same, else "TRANSLATED"."""
        self.passes.append((round(len(audio) / RATE, 2), language))
        out = []
        changes = np.flatnonzero(np.diff(audio)) + 1  # every word is a run of one value
        for start, end in zip(np.r_[0, changes], np.r_[changes, len(audio)]):
            if abs(float(audio[start])) < 0.01 or end - start < RATE // 10:  # a pause, or a scrap of a word
                continue
            heard, number, _sure = word_of(float(audio[start]))
            out.append(types.SimpleNamespace(start=start / RATE, end=end / RATE,
                                             word=f" {heard if heard == language else 'TRANSLATED'}{number}"))
        if words:
            return [types.SimpleNamespace(text="".join(w.word for w in out), words=out)]
        return [types.SimpleNamespace(text="".join(w.word for w in out), words=None)]


class Collect:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


cfg = d.load_config()
cfg.trailing_space = False


def final(audio, live=False):
    tr = Transcriber(cfg)
    out = Collect()
    worker = d.Worker(cfg, tr, out, d.Cues(lambda: False, 0.5), d.Quiet())
    s = d.Session(1, None, "auto", live, 0.0, text=d.LiveText(cfg))
    s.audio, s.t_release = audio, 1.0
    detections.clear()
    worker._final(s)
    return "".join(item.text for item in out.items), tr.passes, s.language


text, passes, language = final(recording(("sk", 4.0), (None, 0.5), ("en", 3.0)))
check("Slovak, then English: each part in its own language", text.split(), [f"sk{i}" for i in range(8)] + [f"en{i}" for i in range(8, 14)])
check("... in two passes, split in the middle of the pause", passes, [(4.25, "sk"), (3.25, "en")])
check("... and the dictation counts as Slovak (most of it)", language, "sk")
text, passes, _ = final(recording(("en", 3.0), (None, 0.5), ("sk", 3.0), (None, 0.5), ("en", 2.5)))
check("English, Slovak, English: three passes", [lang for _, lang in passes], ["en", "sk", "en"])
check("... nothing translated", "TRANSLATED" in text, False)
text, passes, language = final(recording(("sk", 3.0), (None, 0.5), ("sk", 2.0), (None, 0.5), ("sk", 3.0)))
check("Slovak with pauses: one pass over all of it", passes, [(9.0, "sk")])
check("... and each stretch was detected once, padded to 10 s", detections, [d.Transcriber.PIECE_FRAMES] * 3)
text, passes, _ = final(recording(("sk", 4.0), (None, 0.5), ("en", 1.5), (None, 0.5), ("sk", 4.0)))
check("1.5 s that seems English inside Slovak stays Slovak", passes, [(10.5, "sk")])
_, passes, _ = final(recording(("sk", 4.0), (None, 0.5), ("en?", 2.0)))
check("a short English ending that isn't sure on its own (85 %) still switches", [lang for _, lang in passes], ["sk", "en"])
_, passes, _ = final(recording(("en", 4.0), (None, 0.5), ("sk?", 2.0)))
check("a Slovak ending that the check on its own puts at 60 % stays English", passes, [(6.5, "en")])
check("... it was checked once more, padded to 30 s", 3000 in detections, True)
_, passes, _ = final(recording(("sk", 4.0), (None, 0.5), ("en", 4.0)))
check("a long, sure part needs no second check", 3000 in detections, False)

# --- live typing: switching at a pause, never typing a word in the wrong language ------------------


class FakeRecording:
    def __init__(self, samples):
        self.samples, self.t = samples, 0.0

    def seconds(self):
        return self.t

    def snapshot(self):
        return self.samples[: int(self.t * RATE)].copy()


def live(audio, instant=False):
    tr = Transcriber(cfg)
    out = Collect()
    worker = d.Worker(cfg, tr, out, d.Cues(lambda: False, 0.5), d.Quiet())
    s = d.Session(1, FakeRecording(audio), "auto", True, 0.0, text=d.LiveText(cfg), instant=instant)
    t, typed_while = worker.STEP, []
    while t < len(audio) / RATE:
        s.rec.t = t
        before = len(out.items)
        worker._live(s)
        typed_while += [(t, item.text) for item in out.items[before:] if item.text]
        t += worker.STEP
    s.audio, s.t_release = audio, 1.0
    worker._final(s)
    field = ""
    for item in out.items:
        field = (field[:len(field) - item.delete] if item.delete else field) + item.text
    return field, typed_while, s


audio = recording(("sk", 6.0), (None, 1.0), ("en", 4.0), (None, 1.0))
field, typed_while, s = live(audio)
check("live, safe: Slovak, then English, nothing translated", "TRANSLATED" in field, False)
check("... every word typed once, in order", [w.lower().strip(".") for w in field.split()],
      [f"sk{i}" for i in range(12)] + [f"en{i}" for i in range(12, 20)])
check("... the switch is in the middle of the pause", [(round(at, 1), lang) for at, lang in s.parts], [(0.0, "sk"), (6.5, "en")])
check("... words were typed while speaking", bool(typed_while), True)
field, _, s = live(recording(("sk", 6.0), (None, 1.0), ("en", 4.0), (None, 1.0)), instant=True)
check("live, instant: the same text in the end", [w.lower().strip(".") for w in field.split()],
      [f"sk{i}" for i in range(12)] + [f"en{i}" for i in range(12, 20)])
field, _, s = live(recording(("en", 5.0), (None, 0.5), ("en", 3.0), (None, 0.5)))
check("live, one language: no switch", [lang for _, lang in s.parts], ["en"])
check("... all of it typed, once, in order", [w.lower().strip(".") for w in field.split()], [f"en{i}" for i in range(16)])

for name, good, got, want in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}" + ("" if good else f": got {got!r}, want {want!r}"))
sys.exit(0 if all(c[1] for c in checks) else 1)

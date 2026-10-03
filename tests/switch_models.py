"""Switching models the way the tray menu does, with the real Worker: each choice loads between
passes, a dictation recorded while a switch waits is still transcribed (by the old model, before
the switch), and memory doesn't grow over repeated switches. Runs on the CPU.

Needs the tiny and base models (dictate.py --download tiny base) and, for the dictation check,
the FLEURS sentences (tests/fetch_fleurs.py). No microphone, windows or user.

Reference (4-core container): every check ok; with base loaded memory settles after two rounds
(383, 439, 455, 455, 456 MB).
"""
import time

from _common import FLEURS, audio, load_dictate, sentences

d = load_dictate()
cfg = d.load_config()


class Collect:  # stands in for the paster
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


def rss_mb() -> float:
    import psutil
    return psutil.Process().memory_info().rss / 2**20


tr = d.Transcriber(cfg)
out = Collect()
worker = d.Worker(cfg, tr, out, d.Cues(lambda: False, 0.5), d.Quiet())
worker.start()
checks = []


def switch(name: str, timeout=120.0) -> bool:
    choice = ("cpu", "large-v3-turbo", name)
    worker.want(choice)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if worker.choice == choice and worker.wanted is None and worker.loading is None and tr.ready.is_set():
            return tr.name == name
        time.sleep(0.05)
    return False


memory = []  # with base loaded, after each round; glibc fragments a little at first, then settles
for i in range(10):
    name = ("tiny", "base")[i % 2]
    checks.append((f"switch {i + 1} to {name}", switch(name)))
    if name == "base":
        memory.append(rss_mb())
checks.append((f"memory with base loaded, rounds 1-5: {', '.join(f'{m:.0f}' for m in memory)} MB",
               memory[-1] - memory[2] < 60))

if (FLEURS / "eng_Latn.json").exists():
    item = sentences("en")[0]
    samples = audio(item)
    used = []
    final = worker._final
    worker._final = lambda s: (used.append(tr.name), final(s))  # which model transcribes it
    session = d.Session(1, None, "en", False, 0.0, language="en", text=d.LiveText(cfg))
    worker.begin(session)  # the key is down
    worker.want(("cpu", "large-v3-turbo", "tiny"))  # the menu asks for another model meanwhile
    time.sleep(0.5)
    checks.append(("no switch while dictating", worker.choice[2] == "base"))
    worker.end(session, samples, time.monotonic())
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not (out.items and worker.choice[2] == "tiny" and tr.ready.is_set()):
        time.sleep(0.05)
    text = out.items[-1].text if out.items else ""
    checks.append(("dictation transcribed by the old model first", used == ["base"] and len(text.split()) > 3))
    checks.append(("then the switch happened", tr.name == "tiny"))
else:
    print("(skipping the dictation check: run tests/fetch_fleurs.py first)")

tr.model = None  # free it while the worker thread can't use it any more
for name, good in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}", flush=True)
raise SystemExit(0 if all(good for _, good in checks) else 1)

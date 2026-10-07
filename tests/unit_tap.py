"""The push-to-talk state machine with "Tap to start and stop" on and off, driven with made-up key
events (no microphone, model or desktop): a hold records while the key is down either way; a tap
latches the dictation until the next press only when tapping is on, and is a short hold when off.

    python3 tests/unit_tap.py        (any Python 3.11+ with numpy)
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="dictate-test-"))
os.environ.update(XDG_STATE_HOME=str(TMP / "state"), XDG_CONFIG_HOME=str(TMP / "config"), XDG_RUNTIME_DIR=str(TMP),
                  LOCALAPPDATA=str(TMP), HOME=str(TMP))
import numpy as np  # noqa: E402
import dictate as d  # noqa: E402

checks = []


def check(name, got, want):
    checks.append((name, got == want, got, want))


class Recorder:  # three seconds of "speech" whatever happens
    def __init__(self, emit):
        self.t_first = 0.0

    def start(self):
        pass

    def stop(self):
        return np.ones(3 * d.RATE, np.float32) * 0.1

    def seconds(self):
        return 3.0


class Worker:
    def __init__(self):
        self.ended = []

    def begin(self, session):
        pass

    def end(self, session, audio, t_release):
        self.ended.append(session.id)

    def drop(self, session):
        pass


class Quiet:
    def update(self, **changes):
        pass

    def busy(self, delta):
        pass

    def play(self, name):
        pass


d.new_recorder = Recorder
d.keyboard_repeat = lambda: (True, 0.5, 0.03)


def controller(tap: bool):
    cfg = d.load_config()
    ui = d.UiState(cfg)
    ui._values["tap"] = tap  # in memory only; nothing is saved
    worker = Worker()
    return d.Controller(cfg, ui, Quiet(), worker, Quiet()), worker


for tap in (True, False):
    c, worker = controller(tap)
    c.handle("press", 10.0)
    c.handle("release", 10.2)  # a tap: 0.2 s
    check(f"tap on={tap}: state after a tap", c.state, "LATCHED" if tap else "TAIL")
    if tap:
        c.handle("press", 15.0)  # the next press stops it
        c.handle("release", 15.1)
        c.tick(15.0 + c.cfg.tail_ms / 1000 + 0.05)
        check("tap on: the second press ended the dictation", worker.ended, [1])
    else:
        c.tick(10.2 + c.cfg.tail_ms / 1000 + 0.05)
        check("tap off: the short press was a short hold", (c.state, worker.ended), ("IDLE", [1]))

    c, worker = controller(tap)  # a hold is the same either way
    c.handle("press", 20.0)
    for i in range(1, 60):  # GNOME repeats the press every 30 ms after 500 ms
        c.handle("press", 20.5 + i * 0.03)
    c.handle("release", 22.4)
    c.tick(22.4 + c.cfg.tail_ms / 1000 + 0.05)
    check(f"tap on={tap}: a hold records until release", (c.state, worker.ended), ("IDLE", [1]))

failed = [c for c in checks if not c[1]]
for name, ok, got, want in checks:
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f": got {got!r}, want {want!r}"))
sys.exit(1 if failed else 0)

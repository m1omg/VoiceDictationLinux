"""The paster's decisions, with a stand-in clipboard and keyboard (nothing reaches your desktop): with
a key combination on a keyboard that can't see Ctrl/Alt (uinput, on Wayland), live words wait until
the key is up, since Shift+Insert would otherwise arrive as Ctrl+Alt+Shift+Insert; a dictation
cancelled by a shortcut types nothing more and isn't reported as typed; the earlier clipboard comes
back afterwards.

    python3 tests/unit_paster.py        (any Python 3.11+ with numpy)
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="dictate-test-"))
os.environ.update(XDG_STATE_HOME=str(TMP / "state"), XDG_CONFIG_HOME=str(TMP / "config"), XDG_RUNTIME_DIR=str(TMP),
                  LOCALAPPDATA=str(TMP), HOME=str(TMP))
import dictate as d  # noqa: E402

d.screen_locked = lambda: False
checks = []


def check(name, got, want):
    checks.append((name, got == want, got, want))


class Clipboard:
    def __init__(self):
        self.text, self.served = b"earlier", 0

    def publish(self, text, immediate=False):
        self.text = text.encode()
        return True

    def counts(self):
        return self.served, 0

    def wait_served(self, base, timeout):
        self.served += 1
        return True

    def read_clipboard(self):
        return self.text

    def restore_clipboard(self, data):
        self.text = data
        return True


class Uinput:
    """Like VirtualKeyboard: presses the paste keys, can't tell which keys are down."""

    def __init__(self, clipboard):
        self.clipboard, self.typed = clipboard, []

    def shift_insert(self):
        self.typed.append(self.clipboard.text.decode())


def wait(condition, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not condition():
        time.sleep(0.02)
    return condition()


cfg = d.load_config()
cfg.trigger = "Ctrl+Alt+D"
clipboard = Clipboard()
keyboard = Uinput(clipboard)
paster = d.Paster(cfg, clipboard, keyboard)
events = []
paster.on_done = lambda kind, text: events.append((kind, text))
paster.start()


def session(n):
    return d.Session(n, None, "en", True, d.sleep_offset(), t_press=time.monotonic(), language="en",
                     text=d.LiveText(cfg))


s = session(1)
paster.put(d.PasteItem("Hello ", s, final=False))
time.sleep(0.5)
check("live words wait while the combination is held", keyboard.typed, [])
s.key_up_at = time.monotonic()
check("and go out once the key is up", wait(lambda: keyboard.typed == ["Hello "]), True)
s.t_release = time.monotonic()
paster.put(d.PasteItem("world.", s, final=True))
check("the end is typed and reported", wait(lambda: events == [("typed", "world.")]), True)
check("the earlier clipboard comes back", wait(lambda: clipboard.text == b"earlier"), True)

s = session(2)
paster.put(d.PasteItem("Not this ", s, final=False))
time.sleep(0.3)
s.dropped = True  # Right Ctrl+C: the key was part of a shortcut
paster.put(d.PasteItem("", s, final=True))  # what Worker.drop() sends when words had gone out
time.sleep(0.6)
check("a cancelled dictation types nothing more", keyboard.typed, ["Hello ", "world."])
check("and is not reported as typed", events, [("typed", "world.")])

cfg.trigger = "KP_Delete"
plain = d.Paster(cfg, clipboard, keyboard)
check("a single key doesn't wait (live typing on GNOME as before)", plain.wait_for_key, False)

for name, good, got, want in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}" + ("" if good else f": got {got!r}, want {want!r}"))
sys.exit(0 if all(c[1] for c in checks) else 1)

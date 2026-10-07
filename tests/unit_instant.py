"""Instant live typing with a stand-in clipboard, keyboard and app (nothing reaches your desktop): as
the words heard so far change, the app's text always ends up exactly as shown in the top bar; only
the end that changed is deleted (the start of a sentence can change a few times); nothing typed before the dictation is ever deleted; the earlier
clipboard comes back afterwards; and a dictation that can't be typed leaves its whole text on the
clipboard.

    python3 tests/unit_instant.py        (any Python 3.11+ with numpy)
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
import dictate as d  # noqa: E402

d.screen_locked = lambda: False
d.notify = lambda summary, body="": None
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


class App:
    """A text field with the cursor at its end, already holding the user's own text."""

    def __init__(self, clipboard):
        self.clipboard, self.text, self.deleted = clipboard, "Own text. ", 0

    def shift_insert(self):
        self.text += self.clipboard.text.decode()

    def backspace(self, count):
        self.deleted += count
        self.text = self.text[:-count] if count else self.text


class Paster:  # hands the worker's items to the real paster at once
    def __init__(self, real):
        self.real = real

    def put(self, item):
        self.real.paste(item)


cfg = d.load_config()
clip = Clipboard()
app = App(clip)
paster = d.Paster(cfg, clip, app)
check("a keyboard with Backspace allows instant typing", paster.can_correct, True)
worker = d.Worker(cfg, None, Paster(paster), d.Cues(lambda: False, 0.5), d.Quiet())
s = d.Session(1, None, "sk", True, d.sleep_offset(), language="sk", text=d.LiveText(cfg), instant=True)
s.t_release = 0.0
W = lambda *ws: [(0.0, 0.0, w) for w in ws]  # noqa: E731
# how the words heard so far changed during one real Slovak sentence (see the README)
passes = [W(" Prevágeno"), W(" Prevažná", " väčšina,"), W(" Prevážno", " väčšinou", " televíčov,"),
          W(" Prevažná", " väčšina", " televízorov", " je"),
          W(" Prevažná", " väčšina", " televízorov", " je", " prispôsobená", " tak,")]
for words in passes:
    worker._show(s, d.render_words(cfg, words, "sk"))
    check(f"app shows the words heard so far ({len(words)} words)", app.text, "Own text. " + d.render_words(cfg, words, "sk"))
final = d.render_words(cfg, W(" Prevažná", " väčšina", " televízorov", " je", " prispôsobená", " tak."), "sk", final=True)
worker._show(s, final, final=True)
check("after release: the final text, with its trailing space", app.text, "Own text. Prevažná väčšina televízorov je prispôsobená tak. ")
check("the user's own text was never touched", app.text.startswith("Own text. "), True)
check("the earlier clipboard came back", clip.text, b"earlier")
print(f"info: {app.deleted} characters were taken back while the start of the sentence settled")

# A dictation that can't be typed (screen locked) leaves the whole text on the clipboard.
d.screen_locked = lambda: True
s2 = d.Session(2, None, "en", True, d.sleep_offset(), language="en", text=d.LiveText(cfg), instant=True)
s2.t_release = 0.0
before = app.text
worker._show(s2, "Hello there")
worker._show(s2, "Hello there, friend. ", final=True)
check("locked: nothing typed into the app", app.text, before)
check("locked: the whole dictation is on the clipboard", clip.text, b"Hello there, friend. ")

failed = [c for c in checks if not c[1]]
for name, ok, got, want in checks:
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f": got {got!r}, want {want!r}"))
sys.exit(1 if failed else 0)

"""The large-text settings window (bigui.Settings), driven from inside its own process: a choice
redraws only the rows it changed, the keys move the focus frame (Tab round, the arrows not, Home,
End, Page Down), a click chooses, the wheel and the touchpad scroll (fast movements added up), and
what dictation reports shows without moving the view. Prints how long opening, a choice and
scrolling take, and how pictures reach Tk. Settings go to a temporary folder.

It opens a window (on Linux it can run in a private Xvfb: XVFB=/path/to/Xvfb), so on Windows and macOS
run it only when the computer may be left alone for a few seconds (GitHub's machines run it).

    ~/.local/share/dictate/venv/bin/python tests/bigui_window.py

Reference (CachyOS, i5-11400F, Xvfb, size 2x): opening 0.4 s, a choice 5 ms, Pillow's own pictures.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
xvfb = None
if sys.platform.startswith("linux") and (os.environ.get("XVFB") or not os.environ.get("DISPLAY")):
    binary = os.environ.get("XVFB") or shutil.which("Xvfb")
    if not binary:
        sys.exit("Xvfb not found; see tests/x11_paste.py")
    ready_r, ready_w = os.pipe()
    xvfb = subprocess.Popen([binary, "-displayfd", str(ready_w), "-screen", "0", "1440x900x24", "-nolisten", "tcp"],
                            pass_fds=[ready_w], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(ready_w)
    os.environ["DISPLAY"] = ":" + os.read(ready_r, 16).decode().strip()
    os.environ["XDG_SESSION_TYPE"] = "x11"
TMP = Path(tempfile.mkdtemp(prefix="dictate-bigui-"))
os.environ.update(XDG_STATE_HOME=str(TMP / "state"), XDG_CONFIG_HOME=str(TMP / "config"), XDG_RUNTIME_DIR=str(TMP),
                  LOCALAPPDATA=str(TMP), HOME=str(TMP), USERPROFILE=str(TMP))
import dictate as d  # noqa: E402
import bigui  # noqa: E402

assert d.STATE_PATH.is_relative_to(TMP), d.STATE_PATH
d.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
d.STATE_PATH.write_text(json.dumps({"ui_scale": 2.0}), encoding="utf-8")
d.STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
checks = []


def check(name, got, want):
    checks.append((name, got == want, got, want))


def state() -> dict:
    return json.loads(d.STATE_PATH.read_text(encoding="utf-8"))


drawn = []  # the pictures made
real_row_image = bigui.row_image
bigui.row_image = lambda look, focused=False: drawn.append(look) or real_row_image(look, focused)
try:
    started = time.perf_counter()
    s = bigui.Settings()
    s.root.update()
    opened = time.perf_counter() - started
    s.canvas.focus_force()
    s.root.update()
    ids = [b.row_id for b in s.buttons]

    def key(name, shift=False):
        s.canvas.event_generate("<KeyPress>", keysym=name, state=1 if shift else 0)
        s.root.update()
        return s.focused.row_id if s.focused else None

    # A choice: only the rows it changed are drawn again
    drawn.clear()
    started = time.perf_counter()
    s.set(sounds=not s.ui.sounds)
    s.root.update()
    choice = time.perf_counter() - started
    check("a choice draws only the rows it changed (the check box, focused or not)", len(drawn) <= 2, True)
    check("... and is saved", state().get("sounds"), s.ui.sounds)

    # Keys
    check("the first choice has the focus", s.focused.row_id, ids[0])
    check("Down moves to the next one", key("Down"), ids[1])
    check("Up goes back", key("Up"), ids[0])
    check("Up at the top stays there", key("Up"), ids[0])
    check("Shift+Tab at the top goes round to the last", key("Tab", shift=True), ids[-1])
    check("Down at the bottom stays there", key("Down"), ids[-1])
    check("Tab at the bottom goes round to the first", key("Tab"), ids[0])
    check("End goes to the last", key("End"), ids[-1])
    check("Home goes to the first", key("Home"), ids[0])
    check("Page Down moves on by several", ids.index(key("Next")) > 3, True)
    key("Home")
    key("Down")  # English -> Slovenčina
    key("space")
    check("Space chooses", state().get("language"), "sk")
    s.root.update()
    check("the focus stays on the choice after it is drawn again", s.focused.row_id, "lang:sk")
    check("only the focused button has the focus frame", sum(s.canvas.itemcget(b.item, "image") == str(
        s.pictures.get((b.look, True))) for b in s.buttons), 1)

    # A click
    target = next(b for b in s.buttons if b.row_id == "lang:en")
    key("Home")
    s.canvas.event_generate("<Button-1>", x=40, y=int(target.top - s.canvas.canvasy(0) + target.height // 2))
    s.root.update()
    check("a click chooses", state().get("language"), "en")
    check("... and moves the focus frame there", s.focused.row_id, "lang:en")

    # Scrolling
    s.canvas.yview_moveto(0)
    s.root.update()
    for _ in range(50):  # a fast swipe: 50 events before the window draws once
        s.scroll_pixels(-10)
    check("a fast swipe is one movement of the view", s.pending_dy, -500)
    s.root.update()
    check("... by all of it", round(s.canvas.canvasy(0)), 500)
    started = time.perf_counter()
    for _ in range(60):
        s.scroll_pixels(-15)
        s.root.update()
    scrolling = (time.perf_counter() - started) / 60
    s.canvas.yview_moveto(0)
    s.root.update()
    s._wheel(type("E", (), {"num": 5, "delta": 0})())
    s.root.update()
    check("a wheel notch scrolls three lines", abs(s.canvas.canvasy(0) - 3 * s.line) <= 1, True)

    # What dictation reports, while the view is scrolled away from the focused button
    key("Home")
    s.canvas.yview_moveto(0.5)
    s.root.update()
    def first_in_view():
        """(the first button in view, where it is on the screen)"""
        row = next(b for b in s.buttons if b.top >= s.canvas.canvasy(0))
        return row.row_id, round(row.top - s.canvas.canvasy(0))
    before = first_in_view()
    d.STATUS_PATH.write_text(json.dumps({"pid": os.getpid(), "tip": "Listening… and a long text that takes two lines "
                                         "in the window at this size", "model": "small"}), encoding="utf-8")
    drawn.clear()
    s._watch()
    s.root.update()
    check("dictation's state shows: only its line and the Stop button are drawn", len(drawn), 2)
    check("... and the buttons in view stay where they are on the screen (the line above grew)", first_in_view(),
          before)
    check("... and the focus stays", s.focused.row_id, ids[0])
    s.root.destroy()
finally:
    if xvfb is not None:
        xvfb.terminate()
print(f"pictures reach Tk by {bigui.PHOTO_WAYS[0].__name__.removeprefix('_photo_')}; opening {opened:.2f} s, "
      f"a choice {choice * 1000:.0f} ms, a scroll step {scrolling * 1000:.1f} ms")
for name, good, got, want in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}" + ("" if good else f": got {got!r}, want {want!r}"))
sys.exit(0 if all(c[1] for c in checks) else 1)

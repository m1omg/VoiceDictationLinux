"""The large-text mode inside a private Xvfb display (nothing appears on your screen, and your
settings are not touched): the big panel shows and hides without taking the keyboard focus from
the app you type into, and the settings window works by keyboard alone (move, choose, capture a
new key, close). Screenshots go to $SHOTS (default: tests/screenshots/).

    XVFB=/path/to/Xvfb ~/.local/share/dictate/venv/bin/python tests/bigui_test.py

Reference result (Xvfb in a container): every check ok.
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
SHOTS = Path(os.environ.get("SHOTS", ROOT / "tests/screenshots"))
XVFB = os.environ.get("XVFB") or shutil.which("Xvfb")
if not XVFB:
    sys.exit("Xvfb not found; see tests/x11_paste.py")
ready_r, ready_w = os.pipe()
xvfb = subprocess.Popen([XVFB, "-displayfd", str(ready_w), "-screen", "0", "1600x1000x24", "-nolisten", "tcp"],
                        pass_fds=[ready_w], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
os.close(ready_w)
os.environ["DISPLAY"] = ":" + os.read(ready_r, 16).decode().strip()
TMP = Path(tempfile.mkdtemp(prefix="dictate-bigui-"))
env = {**os.environ, "XDG_STATE_HOME": str(TMP / "state"), "XDG_CONFIG_HOME": str(TMP / "config"),
       "XDG_RUNTIME_DIR": str(TMP / "run"), "XDG_SESSION_TYPE": "x11"}
(TMP / "run").mkdir()
STATE = TMP / "state/dictate/state.json"
SHOTS.mkdir(parents=True, exist_ok=True)

import numpy as np  # noqa: E402
from PIL import ImageGrab  # noqa: E402
from Xlib import X, XK, display  # noqa: E402
from Xlib.ext import xtest  # noqa: E402

app = display.Display()
win = app.screen().root.create_window(100, 100, 400, 200, 0, X.CopyFromParent, background_pixel=0x336699)
win.map()
app.sync()
time.sleep(0.3)
win.set_input_focus(X.RevertToParent, X.CurrentTime)
app.sync()
focus = lambda: app.get_input_focus().focus.id  # noqa: E731
checks = []


def shot(name: str):
    image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
    image.save(SHOTS / f"{name}.png")
    return image


def yellow_pixels(image, box) -> int:
    px = np.asarray(image.crop(box).convert("RGB")).astype(int)
    return int(((px[..., 0] > 200) & (px[..., 1] > 200) & (px[..., 2] < 80)).sum())


def frame_pixels(image) -> int:
    """Pixels of the focus frame's colour (#ff6060 in yellow on black)."""
    px = np.asarray(image.convert("RGB")).astype(int)
    return int(((abs(px[..., 0] - 0xff) < 8) & (abs(px[..., 1] - 0x60) < 8) & (abs(px[..., 2] - 0x60) < 8)).sum())


def press(*names, hold=0.05):
    codes = [app.keysym_to_keycode(XK.string_to_keysym(n)) for n in names]
    for c in codes:
        xtest.fake_input(app, X.KeyPress, c)
    app.sync()
    time.sleep(hold)
    for c in reversed(codes):
        xtest.fake_input(app, X.KeyRelease, c)
    app.sync()
    time.sleep(0.25)


def state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


try:
    # The big panel
    panel = subprocess.Popen([sys.executable, str(ROOT / "bigui.py"), "panel"], stdin=subprocess.PIPE, text=True,
                             encoding="utf-8", env=env)

    def send(**msg):
        panel.stdin.write(json.dumps({"scale": 2.0, "colors": "yellow-on-black", "position": "bottom", **msg}) + "\n")
        panel.stdin.flush()
    send(show=True, title="Listening — SK", mark="dot", text="Toto je skúška: čšťžýáíé ľ ň ô ä, a teraz trochu dlhšia veta, "
         "aby bolo vidno zalamovanie riadkov na viac riadkov panela.", accent=True)
    time.sleep(2.5)
    image = shot("panel_listening")
    bottom = (0, 500, 1600, 1000)
    checks.append(("panel shows at the bottom", yellow_pixels(image, bottom) > 500))
    checks.append(("panel left the focus with the app", focus() == win.id))
    send(show=True, title="Typed", mark="tick", text="Hello, this went into the app.", hide_after=1)
    time.sleep(0.8)
    shot("panel_typed")
    time.sleep(1.5)
    checks.append(("panel hides by itself", yellow_pixels(shot("panel_hidden"), bottom) == 0))
    send(show=True, title="Transcribing…", text="", colors="black-on-white", scale=1.0, position="top")
    time.sleep(1.5)
    shot("panel_small_top")
    checks.append(("focus still with the app", focus() == win.id))
    panel.stdin.close()
    checks.append(("panel quits when dictation ends", panel.wait(timeout=5) == 0))

    # The settings window, by keyboard alone
    settings = subprocess.Popen([sys.executable, str(ROOT / "bigui.py"), "settings"], env=env)
    time.sleep(4)
    checks.append(("the focused choice has a thick frame", frame_pixels(shot("settings")) > 500))
    status_line = (0, 0, 1600, 300)
    before = shot("settings").crop(status_line)
    (TMP / "run/dictate-status.json").write_text(json.dumps({"pid": os.getpid(), "tip": "Listening…", "model": "small"}),
                                                 encoding="utf-8")
    time.sleep(2.5)
    checks.append(("what dictation does shows by itself", shot("settings_status").crop(status_line).tobytes()
                   != before.tobytes()))
    press("Down")  # English -> Slovenčina
    press("space")
    time.sleep(0.5)
    checks.append(("arrow key and Space choose Slovak", state().get("language") == "sk"))
    for _ in range(11):  # down to "Change it…" (3 languages, 2 typing, 5 models, 2 devices)
        press("Down")
    press("Return")
    time.sleep(0.5)
    shot("settings_capture")
    press("Control_L", "Alt_L", "d")
    time.sleep(0.8)
    checks.append(("a new key combination is captured", state().get("trigger") == "Ctrl+Alt+D"))
    shot("settings_new_key")
    press("Return")  # the focus stayed on "Change it…"
    code = app.keysym_to_keycode(XK.string_to_keysym("Control_R"))
    for _ in range(3):  # held: repeated presses, as Windows sends for a held modifier
        xtest.fake_input(app, X.KeyPress, code)
        app.sync()
        time.sleep(0.15)
    xtest.fake_input(app, X.KeyRelease, code)
    app.sync()
    time.sleep(0.8)
    checks.append(("a modifier on its own (held, repeating) is captured", state().get("trigger") == "Control_R"))
    XK.load_keysym_group("xkb")
    altgr = app.keysym_to_keycode(XK.string_to_keysym("ISO_Level3_Shift"))
    if altgr:  # (in Xvfb's default keymap)
        press("Return")
        xtest.fake_input(app, X.KeyPress, altgr)
        app.sync()
        time.sleep(0.15)
        xtest.fake_input(app, X.KeyRelease, altgr)
        app.sync()
        time.sleep(0.8)
        checks.append(("AltGr on its own is captured", state().get("trigger") == "ISO_Level3_Shift"))
    STATE.write_text(json.dumps({**state(), "ui_colors": "black-on-white", "ui_scale": 1.5}), encoding="utf-8")
    time.sleep(2)
    shot("settings_white")
    press("Escape")
    checks.append(("Esc closes the window", settings.wait(timeout=5) == 0))
    # macOS Tk delivers the first button's focus before the window shows: the view must stay at the top
    scrolled = subprocess.run([sys.executable, "-c", "import bigui\ns = bigui.Settings()\n"
                               "s._scroll_to(None, s.buttons[0])\nprint(s.canvas.canvasy(0))\ns.root.destroy()"],
                              env=env, cwd=ROOT, capture_output=True, text=True, timeout=30).stdout.strip()
    checks.append(("a focus before the window shows doesn't scroll it", scrolled == "0.0"))
finally:
    xvfb.terminate()
for name, good in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}", flush=True)
print(f"screenshots: {SHOTS}")
sys.exit(0 if checks and all(good for _, good in checks) else 1)

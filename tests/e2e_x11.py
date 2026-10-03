"""A whole dictation on the X11 path, inside a private Xvfb display: the real dictate.py runs with a
stand-in recorder that plays a FLEURS sentence, the key is held through XTEST, and the words must
arrive in a stand-in app window. Also: the big panel shows while dictating, a choice saved by the
settings window is applied, and its "quit" command ends dictation. Nothing touches your screen,
microphone, clipboard or settings (it uses its own folders, and the tiny model you downloaded).

    XVFB=/path/to/Xvfb ~/.local/share/dictate/venv/bin/python tests/e2e_x11.py

Needs: dictate.py --download tiny, and tests/fetch_fleurs.py. Reference (container, 4 cores):
every check ok.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from _common import DICTATE_DIR, FLEURS, errors, sentences

ROOT = Path(__file__).resolve().parent.parent
XVFB = os.environ.get("XVFB") or shutil.which("Xvfb")
SHOTS = Path(os.environ.get("SHOTS", ROOT / "tests/screenshots"))
if not XVFB:
    sys.exit("Xvfb not found; see tests/x11_paste.py")
if not (DICTATE_DIR / "models/tiny/model.bin").exists() or not (FLEURS / "eng_Latn.json").exists():
    sys.exit("needs the tiny model (dictate.py --download tiny) and tests/fetch_fleurs.py")

ready_r, ready_w = os.pipe()
xvfb = subprocess.Popen([XVFB, "-displayfd", str(ready_w), "-screen", "0", "1600x1000x24", "-nolisten", "tcp"],
                        pass_fds=[ready_w], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
os.close(ready_w)
os.environ["DISPLAY"] = ":" + os.read(ready_r, 16).decode().strip()

TMP = Path(tempfile.mkdtemp(prefix="dictate-e2e-"))
home_models = TMP / "home/.local/share/dictate/models"
home_models.parent.mkdir(parents=True)
home_models.symlink_to(DICTATE_DIR / "models")
state = TMP / "state/dictate/state.json"
state.parent.mkdir(parents=True)
state.write_text(json.dumps({"device": "cpu", "cpu_model": "tiny", "language": "en", "live": False,
                             "sounds": False, "big_panel": True, "trigger": "KP_Delete"}))
item = sentences("en")[2]
# A stand-in for pw-record: the sentence as raw 16 kHz float32, at the speed of speech, then silence.
recorder = TMP / "bin/pw-record"
recorder.parent.mkdir()
recorder.write_text(f"""#!{sys.executable}
import signal, sys, time
import numpy as np
from faster_whisper import decode_audio
signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
audio = decode_audio({str(FLEURS / item["file"])!r}, sampling_rate=16000)
audio = np.concatenate([np.zeros(3200, np.float32), audio, np.zeros(16000 * 30, np.float32)])
for i in range(0, len(audio), 320):
    sys.stdout.buffer.write(audio[i:i + 320].astype("<f4").tobytes())
    sys.stdout.buffer.flush()
    time.sleep(0.02)
""")
recorder.chmod(0o755)
env = {**os.environ, "HOME": str(TMP / "home"), "XDG_STATE_HOME": str(TMP / "state"),
       "XDG_CONFIG_HOME": str(TMP / "config"), "XDG_RUNTIME_DIR": str(TMP / "run"), "XDG_SESSION_TYPE": "x11",
       "PATH": f"{recorder.parent}{os.pathsep}{os.environ['PATH']}", "DBUS_SESSION_BUS_ADDRESS": "disabled:"}
(TMP / "run").mkdir()

import numpy as np  # noqa: E402
from faster_whisper import decode_audio  # noqa: E402
from PIL import ImageGrab  # noqa: E402
from Xlib import X, XK, display  # noqa: E402
from Xlib.ext import xtest  # noqa: E402
from Xlib.protocol import event  # noqa: E402


class App(threading.Thread):
    """A focused window that pastes CLIPBOARD on Shift+Insert, like a GUI app."""

    def __init__(self):
        super().__init__(daemon=True)
        self.d = display.Display()
        self.win = self.d.screen().root.create_window(100, 100, 500, 200, 0, X.CopyFromParent,
                                                      background_pixel=0x336699,
                                                      event_mask=X.KeyPressMask | X.KeyReleaseMask)
        self.win.map()
        self.d.sync()
        time.sleep(0.3)
        self.win.set_input_focus(X.RevertToParent, X.CurrentTime)
        atom = self.d.intern_atom
        self.CLIPBOARD, self.UTF8, self.PROP = atom("CLIPBOARD"), atom("UTF8_STRING"), atom("APP_PASTE")
        self.d.sync()
        self.pasted = []

    def run(self):
        while True:
            e = self.d.next_event()
            if e.type == X.KeyPress:
                if self.d.keycode_to_keysym(e.detail, 0) == XK.XK_Insert and e.state & X.ShiftMask:
                    self.win.convert_selection(self.CLIPBOARD, self.UTF8, self.PROP, e.time)
                    self.d.flush()
            elif e.type == X.SelectionNotify and e.property:
                prop = self.win.get_full_property(self.PROP, X.AnyPropertyType)
                self.pasted.append(bytes(prop.value).decode())
            elif e.type == X.SelectionRequest:  # dictate reads the clipboard first: there is none
                e.requestor.send_event(event.SelectionNotify(time=e.time, requestor=e.requestor, selection=e.selection,
                                                             target=e.target, property=X.NONE))
                self.d.flush()


checks = []
log_path = TMP / "dictate.log"
daemon = None
try:
    app = App()
    app.start()
    with open(log_path, "w") as log:
        daemon = subprocess.Popen([sys.executable, str(ROOT / "dictate.py")], env=env, stdout=log, stderr=log)

    def wait_log(text, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if text in log_path.read_text():
                return True
            time.sleep(0.2)
        return False
    checks.append(("model loads (tiny on the CPU)", wait_log("model ready: tiny on cpu", 60)))
    keys = display.Display()
    code = keys.keysym_to_keycode(XK.string_to_keysym("KP_Delete"))
    xtest.fake_input(keys, X.KeyPress, code)
    keys.sync()
    time.sleep(len(decode_audio(str(FLEURS / item["file"]), sampling_rate=16000)) / 16000 + 0.8)
    SHOTS.mkdir(parents=True, exist_ok=True)
    image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
    image.save(SHOTS / "e2e_listening.png")
    px = np.asarray(image.crop((0, 600, 1600, 1000)).convert("RGB")).astype(int)
    yellow = ((px[..., 0] > 200) & (px[..., 1] > 200) & (px[..., 2] < 80)).sum()
    checks.append(("big panel shows while dictating", yellow > 300))
    xtest.fake_input(keys, X.KeyRelease, code)
    keys.sync()
    deadline = time.monotonic() + 30
    while not app.pasted and time.monotonic() < deadline:
        time.sleep(0.2)
    text = "".join(app.pasted)
    errs, n = errors(item["text"], text)
    checks.append((f"the sentence arrives in the app (word errors {errs}/{n})", bool(text) and errs / n < 0.5))
    time.sleep(1)
    ImageGrab.grab(xdisplay=os.environ["DISPLAY"]).save(SHOTS / "e2e_typed.png")
    saved = json.loads(state.read_text())
    state.write_text(json.dumps({**saved, "ui_colors": "black-on-white"}))
    checks.append(("a choice from the settings window is applied", wait_log("settings window: ui_colors=black-on-white", 5)))
    (TMP / "run/dictate-command").write_text("quit\n")
    checks.append(("its quit command ends dictation", daemon.wait(timeout=10) == 0))
finally:
    if daemon and daemon.poll() is None:
        daemon.kill()
    xvfb.terminate()
for name, good in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}", flush=True)
if not all(good for _, good in checks):
    print(log_path.read_text()[-3000:])
os._exit(0 if checks and all(good for _, good in checks) else 1)

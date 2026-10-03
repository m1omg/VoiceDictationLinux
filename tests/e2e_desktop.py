"""A whole dictation on Windows or macOS, as a user does it: the real dictate.py runs with a
stand-in microphone that plays FLEURS sentences, the dictation key is pressed the way another
program presses keys (SendInput on Windows, a posted event on macOS), and the words must arrive in
a text field that has the focus. Checked: a hold with live typing off, the big panel (without
taking the focus), a choice saved by the settings window (live typing on), words typed while the
key is held, the settings window itself, and its "quit" command. It uses its own settings folder
and the tiny model you downloaded.

It opens windows and takes the focus for about a minute: run it when you are not typing, with
dictation stopped (Windows allows one copy at a time). On macOS the app running it (Terminal)
needs the Accessibility permission, as dictation does.

    %LOCALAPPDATA%\\dictate\\venv\\Scripts\\python.exe tests\\e2e_desktop.py             (Windows)
    ~/Library/"Application Support"/dictate/venv/bin/python tests/e2e_desktop.py       (macOS)

Needs: dictate.py --download tiny, and tests/fetch_fleurs.py. Screenshots go to tests/screenshots.
Reference (GitHub's windows-latest runner): every check ok.
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

from _common import DICTATE_DIR, FLEURS, errors, sentences

ROOT = Path(__file__).resolve().parent.parent
WINDOWS, MACOS = sys.platform == "win32", sys.platform == "darwin"
SHOTS = Path(os.environ.get("SHOTS", ROOT / "tests/screenshots"))
if not (WINDOWS or MACOS):
    sys.exit("Windows and macOS only (Linux: tests/e2e_x11.py)")
if not (DICTATE_DIR / "models/tiny/model.bin").exists() or not (FLEURS / "eng_Latn.json").exists():
    sys.exit("needs the tiny model (dictate.py --download tiny) and tests/fetch_fleurs.py")
sys.path.insert(0, str(ROOT))
import numpy as np  # noqa: E402
from faster_whisper import decode_audio  # noqa: E402
from PIL import ImageGrab  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="dictate-e2e-"))
env = {**os.environ, "PYTHONPATH": str(TMP / "stub"), "PYTHONUTF8": "1"}
if WINDOWS:
    env["LOCALAPPDATA"] = str(TMP)
    app_dir = TMP / "dictate"
else:
    env["HOME"] = str(TMP / "home")
    app_dir = TMP / "home/Library/Application Support/dictate"
shutil.copytree(DICTATE_DIR / "models/tiny", app_dir / "models/tiny")
TRIGGER = "KP_Delete" if WINDOWS else "Alt_R"
state = app_dir / "state.json"
state.write_text(json.dumps({"device": "cpu", "cpu_model": "tiny", "language": "en", "live": False,
                             "sounds": False, "big_panel": True, "trigger": TRIGGER}), encoding="utf-8")

# A stand-in for sounddevice (PortAudio): the "microphone" plays the file named in mic.txt in real
# time, then silence. It is found before the real one through PYTHONPATH.
MIC = TMP / "mic.txt"
(TMP / "stub").mkdir()
(TMP / "stub/sounddevice.py").write_text(f'''
import threading, time
from pathlib import Path
import numpy as np

MIC = Path({str(MIC)!r})


class PortAudioError(Exception):
    pass


def query_devices(kind=None):
    return {{"name": "stand-in microphone", "default_samplerate": 16000.0}}


def _initialize():
    pass


def _terminate():
    pass


class InputStream:
    def __init__(self, samplerate=16000, channels=1, dtype="float32", latency=None, callback=None):
        assert samplerate == 16000
        self.callback, self.running, self.thread = callback, False, None
        audio = np.zeros(0, np.float32)
        if callback and MIC.exists():
            from faster_whisper import decode_audio
            audio = decode_audio(MIC.read_text(encoding="utf-8").strip(), sampling_rate=16000)
        self.audio = np.concatenate([np.zeros(3200, np.float32), audio])

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._play, daemon=True)
        self.thread.start()

    def _play(self):
        block, i, due = 320, 0, time.monotonic()
        while self.running:
            chunk = self.audio[i:i + block]
            if self.callback:
                self.callback(np.pad(chunk, (0, block - len(chunk))).reshape(-1, 1), block, None, None)
            i += block
            due += block / 16000
            time.sleep(max(0.0, due - time.monotonic()))

    def stop(self):
        self.running = False
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(1)

    close = stop

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
''', encoding="utf-8")

checks = []


def check(name, good, required=True):
    checks.append((name, bool(good), required))
    print(f"{'ok  ' if good else 'FAIL' if required else 'info'} {name}", flush=True)


# --- the focused app: a text field ---
root = tk.Tk()
root.title("dictate test")
root.geometry("900x260+60+60")
field = tk.Text(root, font=("TkDefaultFont", 14), wrap="word")
field.pack(fill="both", expand=True)


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.02)


def wait(condition, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        root.update()
        if condition():
            return True
        time.sleep(0.05)
    return condition()


def typed():
    return field.get("1.0", "end").strip()


def take_focus():
    root.deiconify()
    root.lift()
    if MACOS:
        from AppKit import NSApplication
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    root.focus_force()
    field.focus_set()
    pump(0.5)
    if WINDOWS and not has_focus():  # Windows lets a program take the focus after an Alt press
        w.send([w.key_input(0x12), w.key_input(0x12, up=True)])
        root.focus_force()
        field.focus_set()
        pump(0.5)


def has_focus():
    if WINDOWS:
        return w.user32().GetForegroundWindow() == int(root.wm_frame(), 16)
    from AppKit import NSApplication
    return bool(NSApplication.sharedApplication().isActive())


# --- the dictation key, pressed as another program would ---
if WINDOWS:
    import windows as w

    def key(down):
        event = w.INPUT(type=w.INPUT_KEYBOARD, u=w.INPUTUNION(ki=w.KEYBDINPUT(
            wVk=0x2E, wScan=0x53, dwFlags=0 if down else w.KEYEVENTF_KEYUP)))  # numpad Del, NumLock off
        return w.user32().SendInput(1, ctypes.byref(event), ctypes.sizeof(w.INPUT)) == 1
else:
    import Quartz

    def key(down):  # Right Option: a flagsChanged event with Option and the right-hand device bit
        event = Quartz.CGEventCreateKeyboardEvent(None, 0x3D, down)
        Quartz.CGEventSetType(event, Quartz.kCGEventFlagsChanged)
        Quartz.CGEventSetFlags(event, (Quartz.kCGEventFlagMaskAlternate | 0x40) if down else 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        return True


def screenshot(name):
    try:
        image = ImageGrab.grab()
        SHOTS.mkdir(parents=True, exist_ok=True)
        image.save(SHOTS / name)
        return image
    except Exception as e:
        print(f"     (no screenshot: {e})")
        return None


def yellow_pixels(image):
    """The big panel's text (yellow on black) in the lower part of the screen."""
    if image is None:
        return 0
    px = np.asarray(image.convert("RGB").crop((0, image.height * 55 // 100, image.width, image.height))).astype(int)
    return int(((px[..., 0] > 200) & (px[..., 1] > 200) & (px[..., 2] < 80)).sum())


def outcomes():
    """How many dictations have ended: typed, or left on the clipboard."""
    text = log_path.read_text(encoding="utf-8", errors="replace")
    return text.count("typed; release -> done") + text.count("on the clipboard:")


def dictate(item, while_held=None):
    """Hold the key while the stand-in microphone plays the sentence; return the words that arrived
    in the text field, and what while_held(seconds) returned (it runs while the key is held)."""
    MIC.write_text(str(FLEURS / item["file"]), encoding="utf-8")
    field.delete("1.0", "end")
    take_focus()
    seconds = len(decode_audio(str(FLEURS / item["file"]), sampling_rate=16000)) / 16000 + 0.8
    done = outcomes()
    key(True)
    held = time.monotonic()
    result = while_held(seconds) if while_held else None
    pump(max(0.0, seconds - (time.monotonic() - held)))
    key(False)
    wait(lambda: outcomes() > done, 30)
    pump(1)
    return typed(), result


log_path = TMP / "dictate.log"
daemon = settings = None
try:
    with open(log_path, "w", encoding="utf-8") as log:
        daemon = subprocess.Popen([sys.executable, "-X", "utf8", str(ROOT / "dictate.py")], env=env,
                                  stdout=log, stderr=log, cwd=ROOT)

    def logged(text):
        return text in log_path.read_text(encoding="utf-8", errors="replace")
    check("dictation starts and loads tiny on the CPU", wait(lambda: logged("model ready: tiny on cpu"), 120))
    check("the dictation key is set up", wait(lambda: logged("push-to-talk key:"), 30))

    # 1. Hold the key, live typing off: the sentence arrives when the key goes up.
    item = sentences("en")[2]
    panel = {}

    def look_at_panel(seconds):
        pump(min(seconds, 4))
        panel["image"] = screenshot("e2e_desktop_listening.png")
        panel["focus"] = has_focus()
    take_focus()
    check("the test window has the focus", has_focus(), required=False)
    text, _ = dictate(item, while_held=look_at_panel)
    errs, n = errors(item["text"], text)
    check(f"held: the sentence arrives in the text field (word errors {errs}/{n})", text and errs / n < 0.5)
    print(f"     typed: {text!r}")
    check("the big panel shows while dictating", yellow_pixels(panel["image"]) > 300, required=not MACOS)
    check("and the text field keeps the focus meanwhile", panel["focus"])
    screenshot("e2e_desktop_typed.png")

    # 2. Live typing on (saved as the settings window saves it): words appear while the key is held.
    saved = json.loads(state.read_text(encoding="utf-8"))
    state.write_text(json.dumps({**saved, "live": True}), encoding="utf-8")
    check("a choice from the settings window is applied", wait(lambda: logged("settings window: live=True"), 5))
    item = sentences("en")[0]
    text, early = dictate(item, while_held=lambda seconds: wait(lambda: len(typed().split()) >= 3, seconds))
    errs, n = errors(item["text"], text)
    check("live: words appear while the key is held", early)
    check(f"live: the whole sentence arrives (word errors {errs}/{n})", text and errs / n < 0.5)
    print(f"     typed: {text!r}")

    # 3. The settings window opens (and reads dictation's status).
    settings = subprocess.Popen([sys.executable, "-X", "utf8", str(ROOT / "bigui.py"), "settings"], env=env, cwd=ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    pump(6)
    screenshot("e2e_desktop_settings.png")
    alive = settings.poll() is None
    if not alive:
        print(settings.stdout.read().decode(errors="replace")[-2000:])
    check("the settings window opens", alive)

    # 4. Its "Stop dictation" command ends dictation.
    (app_dir / "run").mkdir(exist_ok=True)
    (app_dir / "run/dictate-command").write_text("quit\n", encoding="utf-8")
    try:
        code = daemon.wait(timeout=15)
    except subprocess.TimeoutExpired:
        code = None
    check(f"its quit command ends dictation (exit code {code})", code == 0)
finally:
    for proc in (settings, daemon):
        if proc and proc.poll() is None:
            proc.kill()
    root.destroy()
if not all(good for _, good, required in checks if required):
    print(log_path.read_text(encoding="utf-8", errors="replace")[-6000:])
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if checks and all(good for _, good, required in checks if required) else 1)

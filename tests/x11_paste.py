"""The X11 path (Cinnamon, XFCE, MATE) inside a private Xvfb display: nothing appears on your screen,
and your clipboard and keyboard are not touched.

    XVFB=/path/to/Xvfb ~/.local/share/dictate/venv/bin/python tests/x11_paste.py

Without root on Debian/LMDE:  apt-get download xvfb && dpkg -x xvfb_*.deb /tmp/xvfb  (then
XVFB=/tmp/xvfb/usr/bin/Xvfb). A stand-in app window has the focus, owns an earlier clipboard text and
pastes CLIPBOARD on Shift+Insert. XTEST plays the key: pressed, held (the server repeats it, as it
does for a keyboard plugged in after start-up), released.

Reference result (LMDE 7; combinations: Xvfb in a container): every check ok.
"""
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

from _common import load_dictate

XVFB = os.environ.get("XVFB") or shutil.which("Xvfb")
if not XVFB:
    sys.exit("Xvfb not found; see the top of this file")
ready_r, ready_w = os.pipe()
xvfb = subprocess.Popen([XVFB, "-displayfd", str(ready_w), "-screen", "0", "640x480x24", "-nolisten", "tcp"],
                        pass_fds=[ready_w], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
os.close(ready_w)
os.environ["DISPLAY"] = ":" + os.read(ready_r, 16).decode().strip()  # dictate's X connections use this

from Xlib import X, XK, Xatom, display  # noqa: E402
from Xlib.ext import xtest  # noqa: E402
from Xlib.protocol import event  # noqa: E402

d = load_dictate()
TEXT, EARLIER = "Hello from the X11 test, ěščřžýáíé.", b"text copied earlier"


class App(threading.Thread):
    """A focused window that records its keys and pastes CLIPBOARD on Shift+Insert, like a GUI app."""

    def __init__(self):
        super().__init__(daemon=True)
        self.d = display.Display()
        self.win = self.d.screen().root.create_window(0, 0, 300, 100, 0, X.CopyFromParent,
                                                      event_mask=X.KeyPressMask | X.KeyReleaseMask)
        self.win.map()
        self.d.sync()
        self.win.set_input_focus(X.RevertToParent, X.CurrentTime)
        atom = self.d.intern_atom
        self.CLIPBOARD, self.UTF8, self.TARGETS, self.PROP = (atom("CLIPBOARD"), atom("UTF8_STRING"),
                                                             atom("TARGETS"), atom("APP_PASTE"))
        self.win.set_selection_owner(self.CLIPBOARD, X.CurrentTime)
        self.d.sync()
        self.keys, self.pasted = [], []

    def run(self):
        while True:
            e = self.d.next_event()
            if e.type == X.KeyPress:
                keysym = self.d.keycode_to_keysym(e.detail, 0)
                self.keys.append(keysym)
                if keysym == XK.XK_Insert and e.state & X.ShiftMask:
                    self.win.convert_selection(self.CLIPBOARD, self.UTF8, self.PROP, e.time)
                    self.d.flush()
            elif e.type == X.SelectionNotify and e.property:
                prop = self.win.get_full_property(self.PROP, X.AnyPropertyType)
                self.pasted.append(bytes(prop.value).decode())
            elif e.type == X.SelectionRequest:  # dictate saving the clipboard before it pastes
                ok = e.target in (self.UTF8, self.TARGETS)
                if e.target == self.UTF8:
                    e.requestor.change_property(e.property, self.UTF8, 8, EARLIER)
                elif e.target == self.TARGETS:
                    e.requestor.change_property(e.property, Xatom.ATOM, 32, [self.TARGETS, self.UTF8])
                e.requestor.send_event(event.SelectionNotify(time=e.time, requestor=e.requestor, selection=e.selection,
                                                             target=e.target, property=e.property if ok else X.NONE))
                self.d.flush()


def clipboard() -> bytes | None:
    c = display.Display()
    w = c.screen().root.create_window(0, 0, 1, 1, 0, X.CopyFromParent)
    w.convert_selection(c.intern_atom("CLIPBOARD"), c.intern_atom("UTF8_STRING"), c.intern_atom("CHECK"), X.CurrentTime)
    c.flush()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if not c.pending_events():
            time.sleep(0.01)
        elif (e := c.next_event()).type == X.SelectionNotify:
            prop = w.get_full_property(e.property, X.AnyPropertyType) if e.property else None
            return bytes(prop.value) if prop else None
    return None


def repeats() -> bool:
    return bool(keys.get_keyboard_control().auto_repeats[code // 8] & (1 << (code % 8)))


checks = []
try:
    app = App()
    app.start()
    cfg = d.load_config()
    events: queue.Queue = queue.Queue()
    d.X11Hotkey(cfg, lambda kind, value=None: events.put(kind)).start()
    checks.append(("key grabbed", events.get(timeout=5) == "key_ready"))
    keys = display.Display()
    code = keys.keysym_to_keycode(XK.string_to_keysym(cfg.trigger))
    checks.append(("auto-repeat off for the key", not repeats()))

    keys.change_keyboard_control(key=code, auto_repeat_mode=X.AutoRepeatModeOn)  # as on a keyboard plugged in
    xtest.fake_input(keys, X.KeyPress, code)  # since the start: the server repeats the key while it is held
    keys.sync()
    checks.append(("press reported", events.get(timeout=2) == "press"))
    time.sleep(1.0)  # past the repeat delay
    selection = d.SelectionOwner(bridged=False)
    selection.start()
    paster = d.Paster(cfg, selection, d.XTestKeyboard())
    session = d.Session(1, None, "en", False, d.sleep_offset(), language="en", t_release=time.monotonic())
    paster.paste(d.PasteItem(d.clean_text(TEXT, cfg), session, final=True))  # while the key is held
    xtest.fake_input(keys, X.KeyRelease, code)
    keys.sync()
    checks.append(("release reported", events.get(timeout=2) == "release"))
    time.sleep(0.3)
    checks.append(("repeats ignored", events.empty()))
    checks.append(("key kept from the app", not {XK.XK_KP_Delete, XK.XK_KP_Decimal} & set(app.keys)))
    checks.append(("text pasted, accents intact", app.pasted == [d.clean_text(TEXT, cfg)]))
    checks.append(("earlier clipboard restored", clipboard() == EARLIER))

    def hotkey(trigger):
        """Another key grabbed by its own X11Hotkey thread; returns its event queue."""
        q: queue.Queue = queue.Queue()
        d.X11Hotkey(d.SimpleNamespace(**{**vars(cfg), "trigger": trigger}), lambda kind, value=None: q.put(kind)).start()
        return q

    def tap(*names, release=True):
        codes = [keys.keysym_to_keycode(XK.string_to_keysym(n)) for n in names]
        for c in codes:
            xtest.fake_input(keys, X.KeyPress, c)
        if release:
            for c in reversed(codes):
                xtest.fake_input(keys, X.KeyRelease, c)
        keys.sync()

    combo = hotkey("Ctrl+Alt+D")
    checks.append(("combination Ctrl+Alt+D grabbed", combo.get(timeout=5) == "key_ready"))
    d_code = keys.keysym_to_keycode(XK.string_to_keysym("d"))
    checks.append(("D keeps its auto-repeat for typing",
                   bool(keys.get_keyboard_control().auto_repeats[d_code // 8] & (1 << (d_code % 8)))))
    app.keys.clear()
    tap("Control_L", "Alt_L", "d", release=False)
    checks.append(("combination press reported", combo.get(timeout=2) == "press"))
    second = "Typed once Ctrl and Alt are up."
    session = d.Session(2, None, "en", False, d.sleep_offset(), language="en", t_release=time.monotonic())
    typing = threading.Thread(target=paster.paste, args=(d.PasteItem(d.clean_text(second, cfg), session, final=True),))
    typing.start()
    time.sleep(0.8)
    checks.append(("typing waits while Ctrl/Alt are held", len(app.pasted) == 1))
    for name in ("d", "Alt_L", "Control_L"):
        xtest.fake_input(keys, X.KeyRelease, keys.keysym_to_keycode(XK.string_to_keysym(name)))
    keys.sync()
    checks.append(("combination release reported", combo.get(timeout=2) == "release"))
    typing.join(10)
    checks.append(("then typed", app.pasted[-1:] == [d.clean_text(second, cfg)]))
    checks.append(("D kept from the app", XK.XK_d not in app.keys))

    lone = hotkey("Control_R")
    checks.append(("Right Ctrl alone grabbed", lone.get(timeout=5) == "key_ready"))
    tap("Control_R", release=False)
    checks.append(("Right Ctrl press reported", lone.get(timeout=2) == "press"))
    tap("c", release=False)  # Right Ctrl+C: a shortcut, not dictation (held as long as a person would)
    time.sleep(0.1)
    xtest.fake_input(keys, X.KeyRelease, keys.keysym_to_keycode(XK.string_to_keysym("c")))
    keys.sync()
    checks.append(("another key cancels it", lone.get(timeout=2) == "cancel"))
    xtest.fake_input(keys, X.KeyRelease, keys.keysym_to_keycode(XK.string_to_keysym("Control_R")))
    keys.sync()
    checks.append(("Right Ctrl release reported", lone.get(timeout=2) == "release"))

    if shutil.which("xinput"):  # a keyboard plugged in starts out repeating every key
        keys.change_keyboard_control(key=code, auto_repeat_mode=X.AutoRepeatModeOn)
        keys.sync()
        subprocess.run(["xinput", "create-master", "dictate-test"], check=True)
        time.sleep(0.5)
        checks.append(("auto-repeat off again after a keyboard appears", not repeats()))
finally:
    threading.excepthook = lambda args: None  # the X threads die with the display
    xvfb.terminate()
for name, good in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}", flush=True)
os._exit(0 if checks and all(good for _, good in checks) else 1)  # daemon X threads would block exit

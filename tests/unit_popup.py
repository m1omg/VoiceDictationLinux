"""The status pop-up: which one shows (automatic = small while the model runs on the processor), its
menu, what dictation sends it while transcribing, how the pop-up counts the seconds against the
expected time, and the settings window's touchpad scrolling. Stand-ins for the tray, the panel
process and the speech model: no windows, no microphone, nothing reaches your desktop.

    python3 tests/unit_popup.py        (any Python 3.11+ with numpy and Pillow)
"""
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="dictate-test-"))
os.environ.update(XDG_STATE_HOME=str(TMP / "state"), XDG_CONFIG_HOME=str(TMP / "config"), XDG_RUNTIME_DIR=str(TMP),
                  LOCALAPPDATA=str(TMP), HOME=str(TMP), USERPROFILE=str(TMP))
import dictate as d  # noqa: E402
import i18n  # noqa: E402
import tray  # noqa: E402

checks = []


def check(name, got, want):
    checks.append((name, got == want, got, want))


class StandInTray:
    needs_main_thread = False

    def __init__(self, item_id, title, build_menu, on_click, on_activate=None):
        self.build_menu, self.on_activate = build_menu, on_activate
        self.menu = build_menu()

    def refresh_menu(self):
        self.menu = self.build_menu()

    def set_activate(self, on_activate):
        self.on_activate = on_activate

    def set(self, icon=None, label=None, tooltip=None):
        pass

    def notify(self, summary, body=""):
        pass

    def start(self):
        pass


class StandInPanel:  # records what would go to the pop-up's process
    def __init__(self):
        self.messages = []

    def send(self, msg):
        self.messages.append(msg)


class StandInSwitch:  # what the menu and the pop-up ask the model switcher
    def __init__(self, gpu):
        self.gpu = gpu
        self.worker = types.SimpleNamespace(transcriber=types.SimpleNamespace(gpu_available=gpu, on_cpu=not gpu))

    def on_gpu(self):
        return self.gpu


stand_in = types.ModuleType("tray_pystray")
stand_in.MenuItem, stand_in.TrayIcon = tray.MenuItem, StandInTray
sys.modules["tray_pystray"] = stand_in
d.LINUX = False  # the pystray path, so the stand-in tray is used on any OS
assert d.STATE_PATH.is_relative_to(TMP), d.STATE_PATH
d.notify = lambda summary, body="": None

ui = d.UiState(d.load_config())
top = d.TopBar(ui)
top.panel = StandInPanel()

# --- which pop-up ---
check("automatic, before the model is loaded: none", (ui.popup, ui.big_panel, top.popup_mode()), ("auto", False, "off"))
top.switch = StandInSwitch(gpu=True)
check("automatic, on the graphics card: none", top.popup_mode(), "off")
top.switch = StandInSwitch(gpu=False)
check("automatic, on the processor: the small one", top.popup_mode(), "small")
items = {item.label: item for item in top.tray.menu}
popup_menu = items["Status pop-up while dictating"]
check("the menu offers the four choices", [c.label for c in popup_menu.children],
      ["Automatic: small, when the model runs on the processor", "Small", "Large, for low vision", "Off"])
top.clicked(433)
check("Off: none, even on the processor", (ui.popup, top.popup_mode()), ("off", "off"))
top.clicked(431)
top.switch = StandInSwitch(gpu=True)
check("Small: also on the graphics card", (ui.popup, top.popup_mode()), ("on", "small"))
top.clicked(432)
check("Large: the big panel", (ui.big_panel, top.popup_mode()), (True, "large"))
check("the choice is saved", json.loads(d.STATE_PATH.read_text())["big_panel"], True)
top.clicked(430)
check("Automatic again", (ui.popup, ui.big_panel, top.popup_mode()), ("auto", False, "off"))
check("the menu marks the current choice", [c.checked for c in
                                            {i.label: i for i in top.tray.menu}["Status pop-up while dictating"].children],
      [True, False, False, False])

# --- what dictation sends while transcribing ---
top.panel.messages.clear()
top.panel_last = None
since = time.time()
top.update(busy=1, working={"since": since, "expected": 6.0})
check("transcribing: the pop-up gets the start time and the estimate",
      {k: top.panel.messages[-1].get(k) for k in ("title", "working")},
      {"title": "Transcribing…", "working": {"since": since, "expected": 6.0}})
top.update(working=None)
check("then without the count", "working" in top.panel.messages[-1], False)

# The process side: size, language and the rest go along with every message.
lines = []
panel = d.PanelProcess(ui, lambda: "small")
panel.proc = types.SimpleNamespace(poll=lambda: None,
                                   stdin=types.SimpleNamespace(write=lines.append, flush=lambda: None, close=lambda: None))
panel.send({"show": True, "title": "x"})
sent = json.loads(lines[-1])
check("each message says which pop-up and in which language", (sent["size"], sent["lang"]), ("small", "en"))
off = d.PanelProcess(ui, lambda: "off")
off.proc = panel.proc
off.send({"show": True, "title": "x"})
check("switched off: the process is let go, nothing more is sent", (off.proc, len(lines)), (None, 1))


# --- the worker's estimate: seconds of work per second of audio, from earlier dictations ---
class Recorder:
    def __init__(self):
        self.updates = []

    def update(self, **changes):
        self.updates.append(changes)

    def event(self, kind, text=""):
        pass

    def busy(self, delta):
        pass


rec, pasted = Recorder(), []
worker = d.Worker(d.load_config(), None, types.SimpleNamespace(put=pasted.append), d.Cues(lambda: False, 0.5), rec)


def slow_text(s, typed_live):
    time.sleep(0.3)
    return "Hello there.", None


worker._final_text = slow_text
cfg = d.load_config()
for n in (1, 2):
    s = d.Session(n, None, "en", False, d.sleep_offset(), language="en", text=d.LiveText(cfg))
    s.audio = np.zeros(d.RATE * 4, dtype=np.float32)
    worker._final(s)
first, second = [u["working"] for u in rec.updates if u.get("working")]
check("the first dictation after a model load has no estimate yet", first["expected"], None)
check("the next one is estimated from it (0.3 s for 4 s of audio)", round(second["expected"], 1), 0.3)
check("the count ends with each dictation", rec.updates[-1], {"working": None})
check("the text still goes out", [item.text for item in pasted], ["Hello there.", "Hello there."])
worker.transcriber = types.SimpleNamespace(load=lambda *a: None, desc="stand-in")  # (loads nothing)
worker._load(("cpu", None, "small"))
check("another model: the estimate starts over", worker.pace, None)

# --- the pop-up's side: the seconds so far, the bar, the words for it ---
import bigui  # noqa: E402

now = time.time()
view = bigui.progress_view({"title": "Transcribing…", "working": {"since": now - 3, "expected": 10}}, now)
check("3 s of an expected 10 s", (view["title"], view["note"], round(view["bar"], 2)),
      ("Transcribing…  3 s", "About 7 s left", 0.3))
view = bigui.progress_view({"title": "T", "working": {"since": now - 10.5, "expected": 10}}, now)
check("past the estimate: almost done, the bar nearly full", (view["note"], view["bar"]), ("Almost done…", 0.97))
view = bigui.progress_view({"title": "T", "working": {"since": now - 40, "expected": 10}}, now)
check("far past it: it says so (it hasn't silently stopped)", view["note"], "Taking longer than usual…")
view = bigui.progress_view({"title": "T", "working": {"since": now - 4.5, "expected": None}}, now)
check("no estimate yet: a piece travels along the bar", ("bar" in view, round(view["bar_phase"], 2)), (False, 0.5))
view = bigui.progress_view({"title": "Prepisujem…", "lang": "sk", "working": {"since": now - 2, "expected": 5}}, now)
check("in Slovak", view["note"], "Zostáva asi 3 s")
i18n.set_language("en")
check("a message without a count stays as it is", bigui.progress_view({"title": "Typed"}, now), {"title": "Typed"})
small = {"title": "Transcribing…  12 s", "note": "About 3 s left", "bar": 0.5, "size": "small",
         "text": "one two three four five six seven eight nine ten " * 6}
px = bigui.panel_px(small, 1.0)
image = bigui.panel_image(small, bigui.panel_width(small, 1920, px), px)
large = dict(small, size="large", scale=2.0)
lpx = bigui.panel_px(large, 1.0)
check("the small pop-up: about the desktop's text size, a narrow box (plus its frame)",
      (px, bigui.panel_width(small, 1920, px), image.width), (16, 416, 420))
check("the big panel: the chosen size, most of the screen's width", (lpx, bigui.panel_width(large, 1920, lpx)), (55, 1536))
def bright_bottom(msg):  # pixels of text colour in the bar's rows
    return int((np.asarray(bigui.panel_image(msg, 416, px).convert("L"))[-14:] > 128).sum())


check("the bar fills as it goes", bright_bottom(dict(small, bar=0.0)) < bright_bottom(small) < bright_bottom(dict(small, bar=1.0)),
      True)
moving = {k: v for k, v in small.items() if k != "bar"} | {"bar_phase": 0.25}
check("the travelling piece is drawn in the same place", bigui.panel_image(moving, 416, px).size, image.size)

# --- touchpad scrolling in the settings window (Tk 9 packs x and y into one number) ---
check("touchpad: fingers down 5 pixels", bigui.touchpad_dy(5), 5)
check("touchpad: up 3 pixels", bigui.touchpad_dy(0xFFFD), -3)
check("touchpad: sideways movement is left out", bigui.touchpad_dy((7 << 16) | 0xFFFE), -2)

for name, good, got, want in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}" + ("" if good else f": got {got!r}, want {want!r}"))
sys.exit(0 if all(c[1] for c in checks) else 1)

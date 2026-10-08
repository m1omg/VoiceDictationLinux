"""The tray menu as Windows and macOS build it: pystray asks for the menu while the icon is being
created, so everything the menu reads must exist by then (it didn't once, and dictation crashed at
start-up there while Linux was fine). A stand-in tray module records the menu: no windows, no
D-Bus, no clicks reach your desktop, and the settings go to a temporary folder.

    python3 tests/unit_topbar.py        (any Python 3.11+ with numpy)
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="dictate-test-"))
os.environ.update(XDG_STATE_HOME=str(TMP / "state"), XDG_CONFIG_HOME=str(TMP / "config"), XDG_RUNTIME_DIR=str(TMP),
                  LOCALAPPDATA=str(TMP), HOME=str(TMP))
import dictate as d  # noqa: E402
import tray  # noqa: E402

checks = []


def check(name, got, want):
    checks.append((name, got == want, got, want))


class StandInTray:
    """pystray's order of events: the menu is built inside the constructor, and again on refresh."""
    needs_main_thread = False

    def __init__(self, item_id, title, build_menu, on_click, on_activate=None):
        self.build_menu, self.on_click, self.on_activate = build_menu, on_click, on_activate
        self.menu = build_menu()

    def refresh_menu(self):
        self.menu = self.build_menu()

    def set_activate(self, on_activate):
        self.on_activate = on_activate
        self.refresh_menu()

    def set(self, icon=None, label=None, tooltip=None):
        pass

    def notify(self, summary, body=""):
        pass

    def start(self):
        pass


stand_in = types.ModuleType("tray_pystray")
stand_in.MenuItem, stand_in.TrayIcon = tray.MenuItem, StandInTray
sys.modules["tray_pystray"] = stand_in
d.LINUX = False  # the Windows and macOS path, on any OS
assert d.STATE_PATH.is_relative_to(TMP), d.STATE_PATH

ui = d.UiState(d.load_config())
top = d.TopBar(ui)
labels = [item.label for item in top.tray.menu]
check("the menu exists once the icon does", bool(labels), True)
check("it starts with the key", labels[0].startswith("Hold "), True)
check("speech model, run-on, Slovak and English model submenus", [x.split(":")[0] for x in labels if ":" in x],
      ["Speech model", "Run on", "Model for Slovak", "Model for English"])
models_menu = next(item for item in top.tray.menu if item.label.startswith("Speech model"))
check("every model listed, the missing ones with their size",
      [item.label.split(" ")[0] for item in models_menu.children], list(d.models.MODELS))
check("a missing model offers its download", "download 76 MB" in models_menu.children[0].label, True)
top.update(key="Press KP_Delete")  # how GNOME reports the shortcut the user approved
check("the key as GNOME describes it, in plain words", top.how_to(), "Hold numpad Del to dictate, or tap it to start and stop")
top.update(key="Press <Super>F9")
check("a description it can't read stays as GNOME wrote it", top.key_label(), "Press <Super>F9")
top.update(key=None)
top.clicked(20)
check("Type while speaking toggles and is saved", (ui.live, d.UiState(d.load_config()).live), (False, False))
top.clicked(421)
top.changed()
check("big settings window: a click on the icon opens it", top.tray.on_activate is not None, True)

for name, good, got, want in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}" + ("" if good else f": got {got!r}, want {want!r}"))
sys.exit(0 if all(c[1] for c in checks) else 1)

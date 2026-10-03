"""Capturing a new dictation key in the big settings window, fed the key events Tk sends on each
system (no window is opened): Windows repeats held modifiers, names Right Shift's release Shift_L,
names the numpad by its character and AltGr letters by theirs; macOS Tk names Command Meta_L and fn
Super_L, and Option changes letters; on Linux AltGr can be the key only on its own.

    python3 tests/unit_capture.py        (any Python 3.11+ with Tk and Pillow)
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bigui  # noqa: E402
import keys  # noqa: E402

EXT = bigui.WINDOWS_EXTENDED
checks = []


def check(name, got, want):
    checks.append((name, got == want, got, want))


class Capture(bigui.Settings):
    """The settings window's key capture, without the window."""

    def __init__(self, platform: str):
        bigui.WINDOWS, bigui.MACOS = platform == "win32", platform == "darwin"
        keys.ANY_KEYSYM = platform == "linux"
        self.keys, self.capturing, self.down, self.lone, self.key_message = keys, True, {}, None, ""
        self.chosen = None

    def set_key(self, trigger: str) -> None:
        self.chosen, self.capturing = trigger, False

    def build(self) -> None:
        pass


def play(platform: str, *events) -> Capture:
    """events: ("down" or "up", keycode, keysym[, state])"""
    c = Capture(platform)
    for kind, keycode, keysym, *state in events:
        event = SimpleNamespace(keycode=keycode, keysym=keysym, state=state[0] if state else 0)
        (c._key if kind == "down" else c._key_up)(event)
    return c


def mac(code: int, char: str = "\x10") -> int:  # Tk's keycode on macOS: the Mac keycode in the top byte
    return code << 24 | ord(char)


# Windows: Tk's keycode is the virtual-key code
check("Windows: Right Ctrl held (it repeats) and released alone", play(
    "win32", *[("down", 0x11, "Control_R", EXT)] * 3, ("up", 0x11, "Control_R", EXT)).chosen, "Control_R")
check("Windows: Right Shift alone (Tk names its release Shift_L)", play(
    "win32", ("down", 0x10, "Shift_R"), ("up", 0x10, "Shift_L")).chosen, "Shift_R")
check("Windows: numpad Del, NumLock off (Delete without the extended flag) and on (a comma)",
      (play("win32", ("down", 0x2E, "Delete")).chosen, play("win32", ("down", 0x6E, "comma")).chosen),
      ("KP_Delete", "KP_Delete"))
c = play("win32", ("down", 0x2E, "Delete", EXT))
check("Windows: the Delete key alone is refused (it types)", (c.chosen, c.capturing), (None, True))
check("Windows: AltGr+D on a Slovak layout (Ctrl, Right Alt, then đ)", play(
    "win32", ("down", 0x11, "Control_L"), ("down", 0x12, "Alt_R", EXT), ("down", 0x44, "dstroke")).chosen, "Ctrl+Alt+D")
check("Windows: Ctrl+Shift+1 (Tk says exclam)", play(
    "win32", ("down", 0x11, "Control_L"), ("down", 0x10, "Shift_L"), ("down", 0x31, "exclam")).chosen, "Ctrl+Shift+1")
check("Windows: numpad Enter (Enter with the extended flag) with Ctrl", play(
    "win32", ("down", 0x11, "Control_L"), ("down", 0x0D, "Return", EXT)).chosen, "Ctrl+KP_Enter")

# macOS
check("macOS: Right Option alone", play("darwin", ("down", mac(0x3D), "Alt_R"), ("up", mac(0x3D), "Alt_R")).chosen,
      "Alt_R")
check("macOS: Control+Option+D (Option makes it ∂)", play(
    "darwin", ("down", mac(0x3B), "Control_L"), ("down", mac(0x3A), "Alt_L"),
    ("down", mac(0x02, "∂"), "partialderivative")).chosen, "Ctrl+Alt+D")
check("macOS: fn alone (Tk names it Super_L)", play("darwin", ("down", mac(0x3F), "Super_L"),
                                                    ("up", mac(0x3F), "Super_L")).chosen, "Fn")
check("macOS: fn+F5 is F5 (how Mac keyboards type F-keys)", play(
    "darwin", ("down", mac(0x3F), "Super_L"), ("down", mac(0x60, ""), "F5")).chosen, "F5")
check("macOS: Right Command alone (Tk names it Meta_R)", play(
    "darwin", ("down", mac(0x36), "Meta_R"), ("up", mac(0x36), "Meta_R")).chosen, "Super_R")

# Linux (X11 keycodes)
check("Linux: AltGr alone", play("linux", ("down", 108, "ISO_Level3_Shift"), ("up", 108, "ISO_Level3_Shift")).chosen,
      "ISO_Level3_Shift")
c = play("linux", ("down", 108, "ISO_Level3_Shift"), ("down", 40, "dstroke"))
check("Linux: AltGr with a letter is refused, and says why", (c.chosen, "AltGr" in c.key_message), (None, True))
check("Linux: Ctrl+Alt+D, and numpad Del with NumLock on", (
    play("linux", ("down", 37, "Control_L"), ("down", 64, "Alt_L"), ("down", 40, "d")).chosen,
    play("linux", ("down", 91, "KP_Decimal")).chosen), ("Ctrl+Alt+D", "KP_Delete"))
check("Linux: two modifiers pressed and released choose nothing", play(
    "linux", ("down", 37, "Control_L"), ("down", 50, "Shift_L"), ("up", 50, "Shift_L"), ("up", 37, "Control_L")).chosen,
      None)

for name, good, got, want in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}" + ("" if good else f": got {got!r}, want {want!r}"))
sys.exit(0 if all(c[1] for c in checks) else 1)

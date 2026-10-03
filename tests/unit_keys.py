"""The push-to-talk key's spelling and what it maps to on X11, the Wayland portal, Windows and macOS.

    python3 tests/unit_keys.py        (any Python 3.11+)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import keys  # noqa: E402

checks = []


def check(name, got, want):
    checks.append((name, got == want, got, want))


def fails(text):
    try:
        keys.parse(text)
    except ValueError:
        return True
    return False


T = keys.parse
check("default key", T("KP_Delete"), keys.Trigger(frozenset(), "KP_Delete"))
check("combination", T("Ctrl+Alt+D"), keys.Trigger(frozenset({"ctrl", "alt"}), "d"))
check("spaces and case", T("ctrl + shift + Space"), keys.Trigger(frozenset({"ctrl", "shift"}), "space"))
check("aliases", [T(a).key for a in ("RightCtrl", "Right Option", "rightcommand", "NumpadDel", "PageUp", "fn")],
      ["Control_R", "Alt_R", "Super_R", "KP_Delete", "Prior", "Fn"])
check("Cmd/Win/Option are modifiers", T("Cmd+Option+K").mods, frozenset({"super", "alt"}))
check("lone modifier", (T("Control_R").lone_modifier, T("Ctrl+D").lone_modifier, T("F13").lone_modifier),
      (True, False, False))
check("bad spellings rejected", [fails(t) for t in ("", "Ctrl+", "Hyper+D", "Ctrl+Shift_R", "Ctrl+Dee", "+")],
      [True] * 6)
check("written back", [str(T(t)) for t in ("ctrl+alt+d", "KP_Delete", "shift+super+f13")],
      ["Ctrl+Alt+D", "KP_Delete", "Shift+Super+F13"])
check("labels", [keys.label(T("KP_Delete")), keys.label(T("Control_R"), "win32"), keys.label(T("Alt_R"), "darwin"),
                 keys.label(T("Ctrl+Alt+D"), "darwin"), keys.label(T("Super+H"), "win32")],
      ["numpad Del", "Right Ctrl", "Right Option", "Control+Option+D", "Win+H"])
check("X11 masks", (keys.x11_mask(T("Ctrl+Alt+D")), keys.x11_mask(T("Super+Shift+F1"))), (12, 65))
check("portal format", (keys.portal(T("KP_Delete")), keys.portal(T("Ctrl+Alt+D")), keys.portal(T("Super+space"))),
      ("KP_Delete", "CTRL+ALT+d", "LOGO+space"))
check("Windows: numpad Del by scan code, not extended", keys.windows(T("KP_Delete")), (None, 0x53, False))
check("Windows: Delete key is the extended one", keys.windows(T("Delete")), (0x2E, None, True))
check("Windows: letters, F13, Right Ctrl", (keys.windows(T("Ctrl+D")), keys.windows(T("F13")), keys.windows(T("Control_R"))),
      ((0x44, None, None), (0x7C, None, None), (0xA3, None, None)))
check("Windows: every named key mapped", all(n in keys.WINDOWS_KEYS for n in keys.NAMED if n != "Fn"), True)
check("macOS keycodes", (keys.mac(T("Alt_R")), keys.mac(T("KP_Delete")), keys.mac(T("Cmd+V")), keys.mac(T("F5"))),
      (0x3D, 0x41, 0x09, 0x60))
check("macOS: right Option's device bit", keys.MAC_DEVICE_BITS["Alt_R"], 0x40)
check("macOS flags", keys.mac_flags(T("Ctrl+Alt+D")), 0x40000 | 0x80000)
check("macOS: every letter and digit", all(c in keys.MAC_LETTERS for c in "abcdefghijklmnopqrstuvwxyz0123456789"), True)
check("Tk keysyms", [keys.from_tk(k) for k in ("KP_Decimal", "Control_R", "Option_R", "D", "F13", "comma", "ž")],
      ["KP_Delete", "Control_R", "Alt_R", "d", "F13", None, None])

for name, good, got, want in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}" + ("" if good else f": got {got!r}, want {want!r}"))
sys.exit(0 if all(c[1] for c in checks) else 1)

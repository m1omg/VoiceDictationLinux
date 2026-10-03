"""Windows code that can be checked anywhere: the Win32 structure sizes (a wrong INPUT size makes
SendInput fail silently) and the keyboard hook's decisions for made-up key events: numpad Del with
NumLock on and off, the Delete key, repeats, combinations, and Right Ctrl used in a shortcut.

    python3 tests/unit_windows.py        (any 64-bit Python 3.11+)
"""
import ctypes
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import windows as w  # noqa: E402

checks = []


def check(name, got, want):
    checks.append((name, got == want, got, want))


check("sizeof(INPUT) (SendInput needs 40)", ctypes.sizeof(w.INPUT), 40)
check("sizeof(KEYBDINPUT)", ctypes.sizeof(w.KEYBDINPUT), 24)
check("sizeof(KBDLLHOOKSTRUCT)", ctypes.sizeof(w.KBDLLHOOKSTRUCT), 24)
check("sizeof(MSG)", ctypes.sizeof(w.MSG), 48)
check("ki at offset 8 in INPUT", w.INPUT.u.offset, 8)

sent = []
w.send = lambda inputs: sent.append([(i.u.ki.wVk, bool(i.u.ki.dwFlags & w.KEYEVENTF_KEYUP)) for i in inputs]) or len(inputs)


def hook(trigger):
    events = []
    h = w.Hotkey(SimpleNamespace(trigger=trigger), lambda kind, value=None: events.append(kind))
    return h, events


def key(vk, scan, extended=False):
    return SimpleNamespace(vkCode=vk, scanCode=scan, flags=w.LLKHF_EXTENDED if extended else 0)


h, events = hook("KP_Delete")
NUMPAD_DEL, NUMPAD_DOT, DELETE = key(0x2E, 0x53), key(0x6E, 0x53), key(0x2E, 0x53, extended=True)
check("numpad Del (NumLock off) taken", h._key(NUMPAD_DEL, True), True)
check("repeat taken, reported once", (h._key(NUMPAD_DEL, True), events), (True, ["press"]))
check("the Delete key passes", h._key(DELETE, True), False)
check("release taken", (h._key(NUMPAD_DEL, False), events), (True, ["press", "release"]))
check("numpad . (NumLock on) is the same key", (h._key(NUMPAD_DOT, True), h._key(NUMPAD_DOT, False)), (True, True))
check("with Shift held it passes (Shift+numpad Del)", (h._key(key(0xA0, 0x2A), True), h._key(NUMPAD_DEL, True)),
      (False, False))

h, events = hook("Ctrl+Alt+D")
LCTRL, LALT, LSHIFT, D = key(0xA2, 0x1D), key(0xA4, 0x38), key(0xA0, 0x2A), key(0x44, 0x20)
h._key(LCTRL, True)
h._key(LALT, True)
check("Ctrl+Alt+D taken", (h._key(D, True), events), (True, ["press"]))
check("released with D", (h._key(D, False), events), (True, ["press", "release"]))
h._key(LSHIFT, True)
check("Ctrl+Alt+Shift+D is not it", h._key(D, True), False)

h, events = hook("Control_R")
RCTRL, C = key(0xA3, 0x1D, extended=True), key(0x43, 0x2E)
check("Right Ctrl alone taken", (h._key(RCTRL, True), events), (True, ["press"]))
check("Right Ctrl+C: cancelled, Ctrl and C replayed", (h._key(C, True), events, sent[-1]),
      (True, ["press", "cancel"], [(0xA3, False), (0x43, False)]))
check("C's release passes", h._key(C, False), False)
check("Right Ctrl's release passes (the system saw its press)", (h._key(RCTRL, False), events[-1]), (False, "release"))
check("next time it is taken again", h._key(RCTRL, True), True)

for name, good, got, want in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}" + ("" if good else f": got {got!r}, want {want!r}"))
sys.exit(0 if all(c[1] for c in checks) else 1)

"""The macOS event tap's decisions for made-up events (a stand-in replaces pyobjc's Quartz): Right
Option alone, Right Option+2 (typing @ on a Slovak layout cancels the dictation and keeps the
character), Left Option, a combination, auto-repeat, keyboards that set no left/right bits, a release
whose press was missed, and a key Macs don't have.

    python3 tests/unit_macos.py        (any Python 3.11+)
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
DOWN = set()  # keycodes the keyboard reports down (CGEventSourceKeyState)
Q = SimpleNamespace(kCGEventKeyDown=10, kCGEventKeyUp=11, kCGEventFlagsChanged=12, kCGKeyboardEventAutorepeat=8,
                    kCGEventSourceStateHIDSystemState=1,
                    CGEventGetIntegerValueField=lambda event, field: event.get(field, 0),
                    CGEventSetFlags=lambda event, flags: event.__setitem__("flags", flags),
                    CGEventSourceKeyState=lambda state, code: code in DOWN)
sys.modules["Quartz"] = Q
import dictate  # noqa: E402
import macos  # noqa: E402

notes = []
dictate.notify = lambda summary, body="": notes.append(summary)

OPTION, CTRL, SHIFT, RIGHT_OPTION_BIT, LEFT_OPTION_BIT = 0x80000, 0x40000, 0x20000, 0x40, 0x20
checks = []


def check(name, got, want):
    checks.append((name, got == want, got, want))


def tap(trigger):
    events = []
    return macos.Hotkey(SimpleNamespace(trigger=trigger), lambda kind, value=None: events.append(kind)), events


def flags_changed(h, code, flags):
    event = {"flags": flags}
    return h._key(Q.kCGEventFlagsChanged, code, flags, event), event


def key(h, code, flags=0, down=True, repeat=False):
    event = {"flags": flags, Q.kCGKeyboardEventAutorepeat: int(repeat)}
    return h._key(Q.kCGEventKeyDown if down else Q.kCGEventKeyUp, code, flags, event), event


h, events = tap("Alt_R")
check("Right Option down: kept from apps", (flags_changed(h, 0x3D, OPTION | RIGHT_OPTION_BIT)[0], events),
      (None, ["press"]))
check("Right Option up: released", (flags_changed(h, 0x3D, 0)[0], events), (None, ["press", "release"]))
check("Left Option passes", flags_changed(h, 0x3A, OPTION | LEFT_OPTION_BIT)[0] is not None, True)
flags_changed(h, 0x3A, 0)
flags_changed(h, 0x3D, OPTION | RIGHT_OPTION_BIT)
result, event = key(h, 0x13)  # the 2 key: Right Option+2 types @ on a Slovak layout
check("Right Option+2: cancelled, the key keeps Option", (result is event, events[-1], event["flags"] & OPTION != 0),
      (True, "cancel", True))
check("then the release still ends it", (flags_changed(h, 0x3D, 0)[0], events[-1]), (None, "release"))

h, events = tap("Ctrl+Option+D")
check("Control+Option+D taken", (key(h, 0x02, CTRL | OPTION)[0], events), (None, ["press"]))
check("its auto-repeat taken", key(h, 0x02, CTRL | OPTION, repeat=True)[0], None)
check("released with D", (key(h, 0x02, CTRL | OPTION, down=False)[0], events), (None, ["press", "release"]))
check("Control+Option+Shift+D passes", key(h, 0x02, CTRL | OPTION | SHIFT)[0] is not None, True)
check("plain D passes", key(h, 0x02)[0] is not None, True)

h, events = tap("Alt_R")  # a keyboard that reports Option without the left/right bit
DOWN.add(0x3D)
flags_changed(h, 0x3D, OPTION)
DOWN.discard(0x3D)
flags_changed(h, 0x3D, OPTION)
check("no device bits: the keyboard's key state decides", events, ["press", "release"])
h, events = tap("Alt_R")  # its press was missed (the tap was off): the release starts nothing
check("a release without its press does nothing", (flags_changed(h, 0x3D, 0)[0] is not None, events), (True, []))

h, events = tap("Pause")  # no such key on a Mac
check("a key Macs don't have: Right Option instead, and a notification",
      (h.code, str(h.trigger), notes[-1:]), (0x3D, "Alt_R", ["Dictation key not on a Mac"]))

for name, good, got, want in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}" + ("" if good else f": got {got!r}, want {want!r}"))
sys.exit(0 if all(c[1] for c in checks) else 1)

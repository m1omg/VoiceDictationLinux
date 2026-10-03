"""The push-to-talk key: one key ("KP_Delete", "Control_R", "F13") or a combination ("Ctrl+Alt+D"),
written the way X11 names keys, and what that means on each system.

Pure tables and string handling, no OS calls (tests/unit_keys.py covers them).
"""
from __future__ import annotations

from dataclasses import dataclass

MODIFIERS = {"ctrl": "ctrl", "control": "ctrl", "shift": "shift", "alt": "alt", "option": "alt", "opt": "alt",
             "super": "super", "win": "super", "windows": "super", "cmd": "super", "command": "super",
             "meta": "super", "logo": "super"}
ORDER = ("ctrl", "alt", "shift", "super")
ALIASES = {  # friendlier spellings -> the X11 keysym name used everywhere else
    "rightctrl": "Control_R", "rctrl": "Control_R", "leftctrl": "Control_L", "lctrl": "Control_L",
    "rightalt": "Alt_R", "ralt": "Alt_R", "rightoption": "Alt_R", "option_r": "Alt_R", "leftalt": "Alt_L",
    "leftoption": "Alt_L", "option_l": "Alt_L", "rightshift": "Shift_R", "leftshift": "Shift_L",
    "rightcommand": "Super_R", "rightcmd": "Super_R", "command_r": "Super_R", "meta_r": "Super_R",
    "rightwin": "Super_R", "leftcommand": "Super_L", "command_l": "Super_L", "meta_l": "Super_L",
    "leftwin": "Super_L", "numpaddel": "KP_Delete", "numpaddelete": "KP_Delete", "numpaddecimal": "KP_Delete",
    "kp_decimal": "KP_Delete", "numpad0": "KP_Insert", "numpadins": "KP_Insert", "kp_0": "KP_Insert",
    "pageup": "Prior", "pagedown": "Next", "page_up": "Prior", "page_down": "Next", "del": "Delete",
    "ins": "Insert", "esc": "Escape", "enter": "Return", "scrolllock": "Scroll_Lock", "printscreen": "Print",
    "apps": "Menu", "contextmenu": "Menu", "fn": "Fn", "globe": "Fn", "spacebar": "space",
}
NAMED = ["KP_Delete", "KP_Insert", "KP_Enter", "KP_Add", "KP_Subtract", "KP_Multiply", "KP_Divide", "Insert",
         "Delete", "Home", "End", "Prior", "Next", "Pause", "Scroll_Lock", "Print", "Menu", "space", "Tab",
         "Return", "Escape", "Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R", "Super_L",
         "Super_R", "Fn"] + [f"F{n}" for n in range(1, 25)]
LONE_MODIFIERS = {"Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R", "Super_L", "Super_R", "Fn"}


@dataclass(frozen=True)
class Trigger:
    mods: frozenset  # of "ctrl", "alt", "shift", "super"
    key: str  # an X11 keysym name: "KP_Delete", "Control_R", "d", "F13"

    @property
    def lone_modifier(self) -> bool:
        """A modifier key on its own (Right Ctrl, Right Option): used in shortcuts too, so pressing
        another key while it is held cancels the dictation and lets the shortcut happen."""
        return not self.mods and self.key in LONE_MODIFIERS

    def __str__(self) -> str:
        return "+".join([m.capitalize() for m in ORDER if m in self.mods]
                        + [self.key.upper() if len(self.key) == 1 else self.key])


def parse(text: str) -> Trigger:
    """"Ctrl+Alt+D", "ctrl + shift + space", "KP_Delete", "RightCtrl" -> Trigger. Raises ValueError."""
    parts = [p.strip() for p in text.replace(" + ", "+").split("+")]
    if not text.strip() or not all(parts):
        raise ValueError(f"not a key: {text!r}")
    *mod_names, key = parts
    mods = set()
    for name in mod_names:
        if name.lower() not in MODIFIERS:
            raise ValueError(f"{name!r} is not a modifier (Ctrl, Alt/Option, Shift, Super/Win/Cmd)")
        mods.add(MODIFIERS[name.lower()])
    key = ALIASES.get(key.lower().replace(" ", ""), key)
    if len(key) == 1 and key.isascii() and key.isalnum():
        key = key.lower()
    else:
        known = {n.lower(): n for n in NAMED}
        if key.lower() not in known:
            raise ValueError(f"unknown key {key!r}")
        key = known[key.lower()]
    if mods and key in LONE_MODIFIERS:
        raise ValueError("a combination needs a key that isn't a modifier, like Ctrl+Alt+D")
    return Trigger(frozenset(mods), key)


LABELS = {"KP_Delete": "numpad Del", "KP_Insert": "numpad 0", "KP_Enter": "numpad Enter", "Prior": "Page Up",
          "Next": "Page Down", "Scroll_Lock": "Scroll Lock", "Print": "Print Screen", "space": "Space",
          "Control_R": "Right Ctrl", "Control_L": "Left Ctrl", "Alt_R": "Right Alt", "Alt_L": "Left Alt",
          "Shift_R": "Right Shift", "Shift_L": "Left Shift", "Super_R": "Right Super", "Super_L": "Left Super",
          "Menu": "Menu key"}
MAC_LABELS = {"Alt_R": "Right Option", "Alt_L": "Left Option", "Super_R": "Right Command",
              "Super_L": "Left Command", "Control_R": "Right Control", "Control_L": "Left Control", "Fn": "fn"}
WIN_LABELS = {"Super_R": "Right Windows key", "Super_L": "Left Windows key"}


def label(trigger: Trigger, platform: str = "linux") -> str:
    """How to call the key in menus and messages: "numpad Del", "Right Option", "Ctrl+Alt+D"."""
    names = {**LABELS, **(MAC_LABELS if platform == "darwin" else WIN_LABELS if platform == "win32" else {})}
    mod_names = {"darwin": {"ctrl": "Control", "alt": "Option", "shift": "Shift", "super": "Command"},
                 "win32": {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "super": "Win"}}.get(
        platform, {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "super": "Super"})
    key = names.get(trigger.key, trigger.key.upper() if len(trigger.key) == 1 else trigger.key)
    return "+".join([mod_names[m] for m in ORDER if m in trigger.mods] + [key])


# --- X11 and the Wayland portal ---------------------------------------------------------
X11_MASKS = {"shift": 1, "ctrl": 4, "alt": 8, "super": 64}  # ShiftMask, ControlMask, Mod1Mask, Mod4Mask


def x11_mask(trigger: Trigger) -> int:
    return sum(X11_MASKS[m] for m in trigger.mods)


def portal(trigger: Trigger) -> str:
    """The XDG shortcuts format the GlobalShortcuts portal takes as preferred_trigger: "CTRL+ALT+d"."""
    names = {"ctrl": "CTRL", "alt": "ALT", "shift": "SHIFT", "super": "LOGO"}
    return "+".join([names[m] for m in ORDER if m in trigger.mods] + [trigger.key])


# --- Windows: virtual-key code, scan code, extended flag ----------------------------------
# The numpad's Del/. and the Delete key share scan code 0x53 and differ in the extended flag;
# NumLock changes the numpad key's virtual-key code (VK_DECIMAL / VK_DELETE) but not its scan code.
WINDOWS_KEYS = {  # key: (vk or None, scan or None, extended or None)
    "KP_Delete": (None, 0x53, False), "KP_Insert": (None, 0x52, False), "KP_Enter": (0x0D, None, True),
    "KP_Add": (0x6B, None, None), "KP_Subtract": (0x6D, None, None), "KP_Multiply": (0x6A, None, None),
    "KP_Divide": (0x6F, None, None), "Insert": (0x2D, None, True), "Delete": (0x2E, None, True),
    "Home": (0x24, None, True), "End": (0x23, None, True), "Prior": (0x21, None, True), "Next": (0x22, None, True),
    "Pause": (0x13, None, None), "Scroll_Lock": (0x91, None, None), "Print": (0x2C, None, None),
    "Menu": (0x5D, None, None), "space": (0x20, None, None), "Tab": (0x09, None, None),
    "Return": (0x0D, None, False), "Escape": (0x1B, None, None),
    "Control_L": (0xA2, None, None), "Control_R": (0xA3, None, None), "Alt_L": (0xA4, None, None),
    "Alt_R": (0xA5, None, None), "Shift_L": (0xA0, None, None), "Shift_R": (0xA1, None, None),
    "Super_L": (0x5B, None, None), "Super_R": (0x5C, None, None),
    **{f"F{n}": (0x6F + n, None, None) for n in range(1, 25)},
}
WINDOWS_MODIFIER_VKS = {"ctrl": (0xA2, 0xA3), "alt": (0xA4, 0xA5), "shift": (0xA0, 0xA1), "super": (0x5B, 0x5C)}


def windows(trigger: Trigger) -> tuple:
    """(vk, scan, extended) to match a low-level hook event against; None = any."""
    if len(trigger.key) == 1:
        return ord(trigger.key.upper()), None, None
    if trigger.key not in WINDOWS_KEYS:
        raise ValueError(f"{trigger.key} has no Windows equivalent")
    return WINDOWS_KEYS[trigger.key]


# --- macOS: virtual keycodes (ANSI positions) and modifier flags --------------------------
MAC_LETTERS = dict(zip("asdfhgzxcv", range(0x00, 0x0A))) | {
    "b": 0x0B, "q": 0x0C, "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10, "t": 0x11, "1": 0x12, "2": 0x13, "3": 0x14,
    "4": 0x15, "6": 0x16, "5": 0x17, "9": 0x19, "7": 0x1A, "8": 0x1C, "0": 0x1D, "o": 0x1F, "u": 0x20, "i": 0x22,
    "p": 0x23, "l": 0x25, "j": 0x26, "k": 0x28, "n": 0x2D, "m": 0x2E}
MAC_KEYS = {"KP_Delete": 0x41, "KP_Insert": 0x52, "KP_Enter": 0x4C, "KP_Add": 0x45, "KP_Subtract": 0x4E,
            "KP_Multiply": 0x43, "KP_Divide": 0x4B, "Insert": 0x72, "Delete": 0x75, "Home": 0x73, "End": 0x77,
            "Prior": 0x74, "Next": 0x79, "space": 0x31, "Tab": 0x30, "Return": 0x24, "Escape": 0x35,
            "Control_L": 0x3B, "Control_R": 0x3E, "Alt_L": 0x3A, "Alt_R": 0x3D, "Shift_L": 0x38, "Shift_R": 0x3C,
            "Super_L": 0x37, "Super_R": 0x36, "Fn": 0x3F,
            **dict(zip([f"F{n}" for n in range(1, 21)], [0x7A, 0x78, 0x63, 0x76, 0x60, 0x61, 0x62, 0x64, 0x65, 0x6D,
                                                          0x67, 0x6F, 0x69, 0x6B, 0x71, 0x6A, 0x40, 0x4F, 0x50, 0x5A]))}
# A modifier key's own bit in a flagsChanged event (left and right are told apart only this way).
MAC_DEVICE_BITS = {"Control_L": 0x01, "Shift_L": 0x02, "Shift_R": 0x04, "Super_L": 0x08, "Super_R": 0x10,
                   "Alt_L": 0x20, "Alt_R": 0x40, "Control_R": 0x2000, "Fn": 0x800000}
MAC_FLAGS = {"shift": 0x20000, "ctrl": 0x40000, "alt": 0x80000, "super": 0x100000}  # kCGEventFlagMask*


def mac(trigger: Trigger) -> int:
    """The virtual keycode (letters by their position on a US keyboard)."""
    code = MAC_LETTERS.get(trigger.key) if len(trigger.key) == 1 else MAC_KEYS.get(trigger.key)
    if code is None:
        raise ValueError(f"{trigger.key} has no Mac equivalent")
    return code


def mac_flags(trigger: Trigger) -> int:
    return sum(MAC_FLAGS[m] for m in trigger.mods)


# --- Tk key events (capturing a new key in the settings window) ---------------------------
TK_NAMES = {"Option_L": "Alt_L", "Option_R": "Alt_R", "Meta_L": "Super_L", "Meta_R": "Super_R",
            "Command_L": "Super_L", "Command_R": "Super_R", "Win_L": "Super_L", "Win_R": "Super_R",
            "App": "Menu", "KP_Decimal": "KP_Delete", "KP_0": "KP_Insert", "Escape": "Escape"}


def from_tk(keysym: str) -> str | None:
    """A Tk keysym as one of our key names, or None if it can't be a trigger key."""
    name = TK_NAMES.get(keysym, keysym)
    if len(name) == 1:
        return name.lower() if name.isascii() and name.isalnum() else None
    known = {n.lower(): n for n in NAMED}
    return known.get(name.lower())

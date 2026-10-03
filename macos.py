"""macOS backends for dictate, written with pyobjc (Quartz, AppKit):

  Hotkey     the push-to-talk key through an event tap (it sees keys before the apps do)
  Keyboard   Cmd+V posted with explicit flags (a held Option doesn't turn it into Cmd+Option+V)
  Clipboard  the general pasteboard, with the interface of dictate's X11 SelectionOwner
  and screen_locked(), permissions(), check().

macOS asks for permissions in the name of the app that started dictation (Dictate.app, built by
install.sh): Accessibility (to post Cmd+V and to keep the key from apps), Input Monitoring (to see
the key) and the microphone. The pyobjc imports happen inside the functions and classes, so the
module imports on any OS.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time

import keys

log = logging.getLogger("dictate")
MARK = 0x44494354  # "DICT" in kCGEventSourceUserData: our own events
SETTINGS_PANES = {"accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
                  "input": "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
                  "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"}
FLAG_MASK = sum(keys.MAC_FLAGS.values())  # Shift, Control, Option, Command
DEVICE_BITS = {  # a modifier key's group and its own (left/right) bit in flagsChanged events
    0x3B: ("ctrl", 0x01), 0x3E: ("ctrl", 0x2000), 0x38: ("shift", 0x02), 0x3C: ("shift", 0x04),
    0x3A: ("alt", 0x20), 0x3D: ("alt", 0x40), 0x37: ("super", 0x08), 0x36: ("super", 0x10), 0x3F: ("fn", 0x800000)}


def screen_locked() -> bool:
    try:
        import Quartz
        session = Quartz.CGSessionCopyCurrentDictionary() or {}
        return bool(session.get("CGSSessionScreenIsLocked", False))
    except Exception as e:
        log.debug("screen lock query failed: %s", e)
        return False


def permissions(ask: bool = False) -> dict:
    """Which of the permissions dictation needs are granted; ask=True shows macOS's prompts."""
    result = {}
    try:
        import Quartz
        result["input"] = bool(Quartz.CGPreflightListenEventAccess())
        result["post"] = bool(Quartz.CGPreflightPostEventAccess())
        if ask and not result["input"]:
            Quartz.CGRequestListenEventAccess()
        if ask and not result["post"]:
            Quartz.CGRequestPostEventAccess()
    except Exception as e:  # macOS 10.14 and older: no such calls
        log.debug("event access preflight: %s", e)
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
        result["accessibility"] = bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: ask}))
    except Exception as e:
        log.debug("accessibility check: %s", e)
    return result


def open_settings(pane: str) -> None:
    subprocess.Popen(["open", SETTINGS_PANES[pane]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ask_for_microphone(lock: threading.Lock) -> None:
    """Open the microphone once at start-up, so macOS asks for it now, not on the first key press.
    `lock`: PortAudio's (it must not be used while another thread re-initialises it)."""
    try:
        with lock:
            import sounddevice as sd
            with sd.InputStream(samplerate=16000, channels=1):
                time.sleep(0.2)
    except Exception as e:
        log.warning("microphone: %s", e)


class Hotkey(threading.Thread):
    """The push-to-talk key through an event tap on its own run loop. macOS disables a tap whose
    callback is slow (it is re-enabled), so the callback only queues events. A modifier on its own
    (Right Option) is kept from apps while held; if another key goes down with it, that was meant
    for typing (Right Option+2 = @ on some layouts): the dictation is cancelled and the key goes
    out with the modifier's flags added."""

    def __init__(self, cfg, emit):
        super().__init__(name="mac-key", daemon=True)
        self.cfg, self.emit = cfg, emit
        self.trigger = keys.parse(cfg.trigger)
        try:
            self.code = keys.mac(self.trigger)
        except ValueError:  # e.g. Pause or Scroll Lock: Mac keyboards have none
            log.warning("%s has no key on a Mac: using Right Option", self.trigger)
            from dictate import notify
            notify("Dictation key not on a Mac", f"{self.trigger} doesn't exist on a Mac keyboard, so Right Option "
                   "is used. Choose another key in the settings window.")
            self.trigger = keys.parse("Alt_R")
            self.code = keys.mac(self.trigger)
        self.flags = keys.mac_flags(self.trigger)
        self.group, self.bit = DEVICE_BITS.get(self.code, (None, 0))
        self.down = self.replayed = False
        self.tap = None

    def run(self):
        import Quartz
        mask = (Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown) | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
                | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged))
        warned = False
        while self.tap is None:
            self.tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                               Quartz.kCGEventTapOptionDefault, mask, self._callback, None)
            if self.tap is None:  # no Accessibility / Input Monitoring permission yet
                if not warned:
                    warned = True
                    self.emit("key_lost")
                    permissions(ask=True)
                    open_settings("accessibility")
                    from dictate import notify
                    notify("Allow Dictate to use the keyboard", "In System Settings > Privacy & Security, switch "
                           "Dictate on under Accessibility and under Input Monitoring.")
                time.sleep(3)
        source = Quartz.CFMachPortCreateRunLoopSource(None, self.tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self.tap, True)
        log.info("push-to-talk key: %s (macOS event tap)", self.trigger)
        self.emit("key_ready", self.cfg.trigger)
        Quartz.CFRunLoopRun()

    def _callback(self, proxy, kind, event, refcon):
        import Quartz
        try:
            if kind in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                Quartz.CGEventTapEnable(self.tap, True)
                if self.down and not self.key_is_down():  # its release came while the tap was off
                    self.down = False
                    self.emit("release", time.monotonic())
                return event
            if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData) == MARK:
                return event  # our own Cmd+V
            code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            flags = Quartz.CGEventGetFlags(event)
            return self._key(kind, code, flags, event)
        except Exception:
            log.exception("event tap")
            return event

    def key_is_down(self) -> bool:
        """Whether our key is down now, as the keyboard reports it."""
        import Quartz
        return bool(Quartz.CGEventSourceKeyState(Quartz.kCGEventSourceStateHIDSystemState, self.code))

    def _key(self, kind, code, flags, event):
        """Decide one event: return it to let it through, None to keep it from apps."""
        import Quartz
        if code == self.code:
            if self.group:  # a modifier key: flagsChanged, down while its own bit is set
                own_bits = sum(b for g, b in DEVICE_BITS.values() if g == self.group)
                # Some keyboards set no left/right bit: then ask the keyboard (not toggling, which a
                # single missed event would turn upside down).
                down = bool(flags & self.bit) if flags & own_bits else self.key_is_down()
            else:
                if kind == Quartz.kCGEventFlagsChanged:
                    return event
                down = kind == Quartz.kCGEventKeyDown
                if down and Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat):
                    return None if self.down else event
            if down and not self.down:
                if self.trigger.lone_modifier or (flags & FLAG_MASK) == self.flags:
                    self.down, self.replayed = True, False
                    self.emit("press", time.monotonic())
                    return None
                return event
            if not down and self.down:
                self.down = False
                self.emit("release", time.monotonic())
                return None
            return None if self.down else event
        if self.down and self.trigger.lone_modifier and kind == Quartz.kCGEventKeyDown:
            if not self.replayed:
                self.replayed = True
                self.emit("cancel")
            group_flag = keys.MAC_FLAGS.get(self.group, 0x800000 if self.group == "fn" else 0)
            Quartz.CGEventSetFlags(event, flags | group_flag | self.bit)  # the key, with the held modifier
        return event


class Keyboard:
    """Pastes with Cmd+V from a private event source, with the flags set on each event, so a key
    the user still holds (Right Option) doesn't change the shortcut."""

    def __init__(self):
        import Quartz
        self.Q = Quartz
        self.source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
        self.lock = threading.Lock()

    def shift_insert(self) -> None:  # the Paster's name for "paste"
        Q = self.Q
        with self.lock:
            for keycode, down, flags in ((0x37, True, Q.kCGEventFlagMaskCommand), (0x09, True, Q.kCGEventFlagMaskCommand),
                                         (0x09, False, Q.kCGEventFlagMaskCommand), (0x37, False, 0)):
                event = Q.CGEventCreateKeyboardEvent(self.source, keycode, down)
                Q.CGEventSetFlags(event, flags)
                Q.CGEventSetIntegerValueField(event, Q.kCGEventSourceUserData, MARK)
                Q.CGEventPost(Q.kCGHIDEventTap, event)

    def modifiers_held(self) -> bool:
        return False  # every event carries its own flags

    def close(self) -> None:
        pass


class Clipboard:
    """The general pasteboard. macOS doesn't tell when an app reads it, so after Cmd+V the Paster
    waits (0.8 s between live pieces, 1.5 s before the earlier clipboard comes back). Our text is
    marked transient, so clipboard managers skip it (nspasteboard.org), and kept to this Mac."""

    def __init__(self):
        self.change = None

    def start(self) -> None:
        pass

    def _pasteboard(self):
        from AppKit import NSPasteboard
        return NSPasteboard.generalPasteboard()

    def publish(self, text: str, immediate: bool = False) -> bool:
        import objc
        from AppKit import NSPasteboardTypeString
        from Foundation import NSData
        with objc.autorelease_pool():
            pb = self._pasteboard()
            try:  # this Mac only: not to the user's other devices through Universal Clipboard
                import AppKit
                pb.prepareForNewContentsWithOptions_(AppKit.NSPasteboardContentsCurrentHostOnly)
            except AttributeError:
                pb.clearContents()
            ok = pb.setString_forType_(text, NSPasteboardTypeString)
            if not immediate:
                pb.setData_forType_(NSData.data(), "org.nspasteboard.TransientType")
            self.change = pb.changeCount()
            return bool(ok)

    def counts(self):
        return 0, 0

    def wait_served(self, base, timeout: float) -> bool:
        time.sleep(timeout)  # no word from the app: give it the whole time before the text changes
        return True

    def read_clipboard(self):
        import objc
        from AppKit import NSPasteboardTypeString
        with objc.autorelease_pool():
            text = self._pasteboard().stringForType_(NSPasteboardTypeString)
            return str(text).encode("utf-8") if text is not None else None

    def restore_clipboard(self, data: bytes) -> bool:
        import objc
        from AppKit import NSPasteboardTypeString
        with objc.autorelease_pool():
            pb = self._pasteboard()
            if pb.changeCount() != self.change:  # the user copied something meanwhile: keep it
                return False
            pb.clearContents()
            pb.setString_forType_(data.decode("utf-8", "replace"), NSPasteboardTypeString)
            self.change = pb.changeCount()
            return True


def check(cfg, report) -> None:
    """--check on macOS."""
    import platform
    report("macOS", True, f"{platform.mac_ver()[0]} on {platform.machine()}")
    granted = permissions()
    for name, label in (("accessibility", "Accessibility"), ("input", "Input Monitoring"), ("post", "posting keys")):
        if name in granted:
            report(f"permission: {label}", granted[name], "allowed" if granted[name] else
                   "not allowed: System Settings > Privacy & Security (it applies to Dictate.app, not to Terminal)",
                   required=False)
    try:
        import sounddevice as sd
        report("microphone", True, sd.query_devices(kind="input")["name"])
    except Exception as e:
        report("microphone", False, f"no input device ({e})")
    try:
        report("key", True, f"{keys.label(keys.parse(cfg.trigger), 'darwin')} (keycode {keys.mac(keys.parse(cfg.trigger))})")
    except ValueError as e:
        report("key", False, str(e))
    from pathlib import Path
    app = Path.home() / "Applications/Dictate.app"
    report("Dictate.app", app.exists(), str(app))

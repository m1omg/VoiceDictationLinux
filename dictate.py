#!/usr/bin/env python3
"""dictate: hold numpad Del to talk; your words are typed into the focused app.

Push-to-talk dictation for Linux (GNOME/KDE on Wayland, X11 desktops), Windows and macOS:
  hotkey  Wayland: xdg-desktop-portal GlobalShortcuts; X11: a key grab; Windows: a low-level
          keyboard hook; macOS: an event tap. The key never reaches the focused app.
  audio   pw-record (PipeWire) or PortAudio; the microphone is open only while the key is held
  speech  faster-whisper kept loaded on the GPU or CPU; English, Slovak or auto-detect
  live    optionally types words while you speak, once two consecutive passes agree on them
  output  the clipboard (X11 CLIPBOARD + PRIMARY on Linux), then Shift+Insert (Cmd+V on macOS)
  menu    a top-bar / tray icon to switch language, model, CPU/GPU, live typing and sounds

Settings: config.toml (~/.config/dictate on Linux). Menu choices: state.json.
Run with --help for the setup and test modes.
"""
from __future__ import annotations

import argparse
import ctypes
import faulthandler
import functools
import gc
import glob
import inspect
import itertools
import json
import logging
import os
import queue
import re
import secrets
import select
import shutil
import signal
import struct
import subprocess
import sys
import sysconfig
import threading
import time
import tomllib
import wave
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import keys
import models

WINDOWS, MACOS = sys.platform == "win32", sys.platform == "darwin"
LINUX = not (WINDOWS or MACOS)
APP_ID = "io.github.m1omg.VoiceDictationLinux"  # replaced by the app_id setting at start-up
if WINDOWS:  # one folder holds everything: program, models, settings, log
    APP_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "dictate"
    CONFIG_PATH, STATE_PATH, RUNTIME_DIR = APP_DIR / "config.toml", APP_DIR / "state.json", APP_DIR / "run"
elif MACOS:
    APP_DIR = Path.home() / "Library/Application Support/dictate"
    CONFIG_PATH, STATE_PATH, RUNTIME_DIR = APP_DIR / "config.toml", APP_DIR / "state.json", APP_DIR / "run"
else:
    APP_DIR = Path.home() / ".local/share/dictate"
    CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "dictate/config.toml"
    STATE_PATH = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "dictate/state.json"
    RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
MODELS_DIR = APP_DIR / "models"
LOG_PATH = APP_DIR / "dictate.log"  # used when there is no journal (autostart desktops, Windows, macOS)
RATE = 16000
STALE_SECONDS = 15
C_LOCALE = {**os.environ, "LC_ALL": "C"}  # pw-cat parses numbers with the locale (sk uses ",")
LANGUAGES = {"en": "English", "sk": "Slovenčina", "auto": "Auto-detect (English / Slovak)"}
AUTO_LANGUAGES = ("en", "sk")

# Keep caches inside APP_DIR even when run by hand (the systemd unit sets these as well).
os.environ.setdefault("HF_HOME", str(APP_DIR / "cache/hf"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("CUDA_CACHE_PATH", str(APP_DIR / "cache/nv"))
os.environ["XDG_CACHE_HOME"] = str(APP_DIR / "cache")  # onnxruntime (VAD) keeps a device id there

log = logging.getLogger("dictate")

DEFAULTS = {
    "app_id": APP_ID,  # GNOME/KDE remember the approved shortcut under this id (needs <app_id>.desktop)
    "backend": "auto",  # "auto", "wayland" (GNOME/KDE: portal + uinput) or "x11" (e.g. Cinnamon)
    "trigger": "KP_Delete",
    "shortcut_id": "push-to-talk",
    "model": "large-v3-turbo",  # starting values for the menu choices: the model on the GPU,
    "fallback_model": "small",  # the model on the CPU (all are folders in models/),
    "device": "auto",  # "auto" (the GPU when one works), "gpu" or "cpu",
    "language": "en",
    "live_typing": True,
    "sounds": True,
    "beam_size": 5,
    "cpu_beam_size": 2,  # on the CPU a narrower beam saves time for little accuracy
    "vocabulary": ["Claude", "Claude Code", "GNOME", "Wayland", "Python", "Git", "GitHub", "Linux"],
    "trailing_space": True,
    "remove_fillers": True,
    "sound_volume": 0.5,
    "restore_clipboard": True,
    "paste_chunk_chars": 750,
    "tail_ms": 200,
    "max_seconds": 300,
    "big_panel": False,  # large-text mode (low vision): a big status panel while dictating,
    "big_settings": False,  # clicking the tray icon opens the big settings window,
    "ui_scale": 2.0,  # their text size (1-3 times),
    "ui_colors": "yellow-on-black",  # and colours (see COLOR_SCHEMES)
    "panel_position": "bottom",
}
COLOR_SCHEMES = ("yellow-on-black", "white-on-black", "black-on-white", "black-on-yellow")


def load_config() -> SimpleNamespace:
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "rb") as f:
            user = tomllib.load(f)
    except FileNotFoundError:
        user = {}
    except tomllib.TOMLDecodeError as e:
        log.error("config: %s is not valid TOML (%s); using defaults", CONFIG_PATH, e)
        notify("Dictation settings could not be read", f"{CONFIG_PATH}: {e}")
        user = {}
    for key, value in user.items():
        default = DEFAULTS.get(key)
        if key not in DEFAULTS:
            log.warning("config: unknown setting %r ignored", key)
        elif type(value) is not type(default) and not (type(default) is float and type(value) is int):
            log.warning("config: %r should be a %s; using %r", key, type(default).__name__, default)
        else:
            cfg[key] = value
    return SimpleNamespace(**cfg)


DEVICES = ("auto", "gpu", "cpu")  # "auto": the GPU when one works, else quietly the CPU


def valid_trigger(text: str) -> bool:
    try:
        keys.parse(text)
        return True
    except ValueError as e:
        log.warning("the key %r can't be used: %s", text, e)
        return False


def shortcut_id(cfg, trigger: str) -> str:
    """The id the desktop stores the approved shortcut under (Wayland). A new key gets a new id, so
    the desktop asks again: GNOME and KDE keep the key a user approved under an id."""
    if trigger == cfg.trigger:
        return cfg.shortcut_id
    return f"{cfg.shortcut_id}-" + re.sub(r"[^a-z0-9]+", "-", trigger.lower()).strip("-")


class UiState:
    """Choices made from the tray menu or the settings window, kept across restarts in state.json
    (config.toml only gives their starting values). Read them as attributes: ui.language, ui.device."""

    def __init__(self, cfg):
        self._defaults = {"language": cfg.language if cfg.language in LANGUAGES else "en",
                          "live": cfg.live_typing, "sounds": cfg.sounds,
                          "device": cfg.device if cfg.device in DEVICES else "auto",
                          "gpu_model": cfg.model, "cpu_model": cfg.fallback_model,
                          "trigger": cfg.trigger if valid_trigger(cfg.trigger) else DEFAULTS["trigger"],
                          "big_panel": cfg.big_panel, "big_settings": cfg.big_settings,
                          "ui_scale": min(3.0, max(1.0, float(cfg.ui_scale))),
                          "ui_colors": cfg.ui_colors if cfg.ui_colors in COLOR_SCHEMES else "yellow-on-black",
                          "panel_position": cfg.panel_position if cfg.panel_position in ("bottom", "top") else "bottom"}
        self._lock = threading.Lock()
        self.listeners: list = []
        self._values = self._read()

    def _read(self) -> dict:
        values = dict(self._defaults)
        try:
            saved = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            saved = {}
        except (OSError, ValueError) as e:
            log.warning("state: %s is unreadable (%s); using defaults", STATE_PATH, e)
            saved = {}
        for key, value in saved.items() if isinstance(saved, dict) else ():
            if key == "ui_scale" and type(value) is int:
                value = float(value)
            if key in values and type(value) is type(values[key]) and self._valid(key, value):
                values[key] = value
        return values

    def reload(self) -> set[str]:
        """Take over choices another process (the settings window) saved; returns what changed."""
        with self._lock:
            new = self._read()
            changed = {k for k in new if new[k] != self._values.get(k)}
            self._values = new
        if changed:
            log.info("settings window: %s", ", ".join(f"{k}={new[k]}" for k in sorted(changed)))
            for listener in self.listeners:
                listener()
        return changed

    @staticmethod
    def _valid(key: str, value) -> bool:
        if key == "language":
            return value in LANGUAGES
        if key == "device":
            return value in DEVICES
        if key.endswith("_model"):  # a folder name in models/
            return bool(value) and not value.startswith(".") and not set("/\\:") & set(value)
        if key == "trigger":
            return valid_trigger(value)
        if key == "ui_scale":
            return 1.0 <= value <= 3.0
        if key == "ui_colors":
            return value in COLOR_SCHEMES
        if key == "panel_position":
            return value in ("bottom", "top")
        return True

    def __getattr__(self, name: str):
        values = self.__dict__.get("_values", {})
        if name in values:
            return values[name]
        raise AttributeError(name)

    def set(self, **changes) -> None:
        with self._lock:
            self._values.update(changes)
            data = json.dumps(self._values, indent=1) + "\n"
        try:
            STATE_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            tmp.write_text(data, encoding="utf-8")
            tmp.replace(STATE_PATH)
        except OSError as e:
            log.warning("state: cannot save %s: %s", STATE_PATH, e)
        log.info("menu: %s", ", ".join(f"{k}={v}" for k, v in changes.items()))
        for listener in self.listeners:
            listener()


NOTIFY_HOOK = None  # Windows: the tray icon shows notifications (set by the tray once it runs)


def notify(summary: str, body: str = "") -> None:
    """Desktop notification, used only for problems the user should act on."""
    try:
        if NOTIFY_HOOK is not None:
            NOTIFY_HOOK(summary, body)
        elif MACOS:  # the texts go in as arguments, never into the script
            subprocess.Popen(["osascript", "-e", "on run argv", "-e", 'display notification (item 2 of argv) '
                              'with title "Dictate" subtitle (item 1 of argv)', "-e", "end run", summary, body],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif LINUX:
            subprocess.Popen(["notify-send", "--app-name=Dictate", "--icon=audio-input-microphone",
                              f"--hint=string:desktop-entry:{APP_ID}", summary, body],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log.debug("notification failed: %s", e)


def sleep_offset() -> float:
    """Grows when the machine suspends: a clock that counts suspended time minus one that doesn't."""
    if WINDOWS:
        from windows import sleep_offset as windows_sleep_offset
        return windows_sleep_offset()
    if MACOS:  # there CLOCK_MONOTONIC keeps counting during sleep and CLOCK_UPTIME_RAW stops
        return time.clock_gettime(time.CLOCK_MONOTONIC) - time.clock_gettime(time.CLOCK_UPTIME_RAW)
    return time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()


def keyboard_repeat() -> tuple[bool, float, float]:
    """GNOME's key-repeat settings: (enabled, delay s, interval s). Elsewhere our own key hook
    ignores repeats and always sees the release, so there is nothing to infer from repeats."""
    if not LINUX:
        return False, 0.5, 0.03

    def get(key):
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.peripherals.keyboard", key],
                             capture_output=True, text=True, timeout=3).stdout.split()
        return out[-1]
    try:
        return get("repeat") == "true", int(get("delay")) / 1000, int(get("repeat-interval")) / 1000
    except Exception:
        return True, 0.5, 0.03


def single_instance():
    """Holds a lock for the life of the process; exits if another copy holds it."""
    if WINDOWS:
        from windows import single_instance as windows_single_instance
        return windows_single_instance()
    import fcntl
    RUNTIME_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(RUNTIME_DIR / "dictate.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def lock_or_exit():
    lock = single_instance()
    if lock is None:
        sys.exit("dictate is already running" + (" (stop it with: systemctl --user stop dictate)" if LINUX else ""))
    return lock


def dbus_call(conn, msg, timeout=30):
    from jeepney import HeaderFields, MessageType
    reply = conn.send_and_get_reply(msg, timeout=timeout)
    if reply.header.message_type == MessageType.error:
        raise RuntimeError(f"{reply.header.fields.get(HeaderFields.error_name)}: {reply.body}")
    return reply.body


def screen_locked() -> bool:
    if WINDOWS:
        from windows import screen_locked as windows_screen_locked
        return windows_screen_locked()
    if MACOS:
        from macos import screen_locked as macos_screen_locked
        return macos_screen_locked()
    try:
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.blocking import open_dbus_connection
        with open_dbus_connection("SESSION") as conn:
            for name in ("org.gnome.ScreenSaver", "org.cinnamon.ScreenSaver", "org.freedesktop.ScreenSaver"):
                path = "/" + name.replace(".", "/")
                try:
                    if dbus_call(conn, new_method_call(DBusAddress(path, name, name), "GetActive"), timeout=2)[0]:
                        return True
                except Exception:
                    continue
    except Exception as e:
        log.debug("screensaver query failed: %s", e)
    return False


class Cues:
    """Short generated tones, played without blocking (pw-play or paplay, winsound, afplay)."""
    TONES = {"start": [(880, 0.07)], "stop": [(587, 0.07)],
             "error": [(220, 0.09), (0, 0.06), (220, 0.09)]}

    def __init__(self, enabled, volume: float):
        self.enabled = enabled  # a callable: sounds can be switched from the menu
        self.dir = RUNTIME_DIR / "dictate-sounds"
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.player = None if WINDOWS else "afplay" if MACOS else shutil.which("pw-play") or "paplay"
        for name, parts in self.TONES.items():
            self._write(self.dir / f"{name}.wav", parts, 0.35 * min(max(volume, 0.0), 1.0))

    @staticmethod
    def _write(path: Path, parts, amplitude: float, rate=48000):
        pieces = []
        for freq, seconds in parts:
            t = np.arange(int(rate * seconds)) / rate
            fade = np.minimum(1.0, np.minimum(t, seconds - t) / 0.008)  # 8 ms ramps avoid clicks
            pieces.append(np.sin(2 * np.pi * freq * t) * fade if freq else np.zeros_like(t))
        pcm = (np.concatenate(pieces) * amplitude * 32767).astype("<i2")
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())

    def play(self, name: str) -> None:
        if not self.enabled():
            return
        path = str(self.dir / f"{name}.wav")
        try:
            if WINDOWS:
                import winsound
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            else:
                subprocess.Popen([self.player, path], env=C_LOCALE, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        except (OSError, RuntimeError):
            pass


class VirtualKeyboard:
    """A uinput keyboard used only to press Shift+Insert (keycodes, so the layout doesn't matter)."""
    UI_SET_EVBIT, UI_SET_KEYBIT, UI_DEV_SETUP = 0x40045564, 0x40045565, 0x405C5503
    UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
    EV_SYN, EV_KEY, KEY_LEFTSHIFT, KEY_INSERT = 0, 1, 42, 110

    def __init__(self):
        import fcntl
        self.ioctl = fcntl.ioctl
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        fcntl.ioctl(self.fd, self.UI_SET_EVBIT, self.EV_KEY)
        for code in range(1, 120):  # ESC..D are what udev needs to call it a keyboard
            if code != 116:  # skip KEY_POWER so logind never treats this device as a power button
                fcntl.ioctl(self.fd, self.UI_SET_KEYBIT, code)
        setup = struct.pack("HHHH80sI", 0x06, 0x1209, 0xD1C7, 1, b"dictate virtual keyboard", 0)
        fcntl.ioctl(self.fd, self.UI_DEV_SETUP, setup)
        fcntl.ioctl(self.fd, self.UI_DEV_CREATE)
        self.created = time.monotonic()
        self.lock = threading.Lock()

    def shift_insert(self) -> None:
        """Press and release Shift+Insert in one write, so no real key event can land in between
        (a physical key released while our Shift is down would reach GNOME as Shift+key)."""
        with self.lock:
            time.sleep(max(0.0, 1.0 - (time.monotonic() - self.created)))  # let mutter adopt the device
            events = [(self.KEY_LEFTSHIFT, 1), (self.KEY_INSERT, 1), (self.KEY_INSERT, 0), (self.KEY_LEFTSHIFT, 0)]
            os.write(self.fd, b"".join(struct.pack("llHHi", 0, 0, self.EV_KEY, code, value)
                                       + struct.pack("llHHi", 0, 0, self.EV_SYN, 0, 0) for code, value in events))

    def close(self) -> None:
        try:
            self.ioctl(self.fd, self.UI_DEV_DESTROY)
            os.close(self.fd)
        except OSError:
            pass


class XTestKeyboard:
    """Presses Shift+Insert through the XTEST extension (X11 desktops; no /dev/uinput needed)."""

    def __init__(self):
        from Xlib import X, XK, display
        from Xlib.ext import xtest
        self.X, self.xtest = X, xtest
        self.d = display.Display()
        if not self.d.has_extension("XTEST"):
            raise OSError("the X server has no XTEST extension")
        self.shift = self.d.keysym_to_keycode(XK.string_to_keysym("Shift_L"))
        self.insert = self.d.keysym_to_keycode(XK.string_to_keysym("Insert"))
        self.lock = threading.Lock()

    def shift_insert(self) -> None:
        X = self.X
        with self.lock:
            for kind, code in ((X.KeyPress, self.shift), (X.KeyPress, self.insert),
                               (X.KeyRelease, self.insert), (X.KeyRelease, self.shift)):
                self.xtest.fake_input(self.d, kind, code)
            self.d.sync()

    def modifiers_held(self) -> bool:
        """Ctrl, Alt, Shift or Super down (our Shift+Insert would arrive as e.g. Ctrl+Shift+Insert)."""
        with self.lock:
            X = self.X
            return bool(self.d.screen().root.query_pointer().mask
                        & (X.ShiftMask | X.ControlMask | X.Mod1Mask | X.Mod4Mask))

    def close(self) -> None:
        self.d.close()


class SelectionOwner(threading.Thread):
    """Owns the X11 CLIPBOARD and PRIMARY selections on XWayland.

    mutter bridges X11 selections to Wayland apps without any focus requirement; it's the
    only focus-free way to set the clipboard on GNOME. Every Xlib call runs on this thread;
    other threads submit work through call(). Only UTF8_STRING is offered, because offering
    STRING as well makes mutter mangle accented characters (mutter #5057).
    """

    def __init__(self, bridged: bool = True):
        super().__init__(name="x11", daemon=True)
        self.bridged = bridged  # XWayland: wait until the compositor has picked up our offer
        self.jobs: queue.Queue = queue.Queue()
        self.wake_r, self.wake_w = os.pipe()
        self.connected = threading.Event()

    # --- called from other threads ---------------------------------------------------
    def call(self, fn, *args, timeout=3.0):
        if not self.connected.wait(timeout):
            log.warning("x11: not connected")
            return None
        done, box = threading.Event(), {}

        def job():
            try:
                box["value"] = fn(*args)
            except Exception as e:
                log.warning("x11: %s failed: %r", fn.__name__, e)
            finally:
                done.set()
        self.jobs.put(job)
        os.write(self.wake_w, b"x")
        done.wait(timeout)
        return box.get("value")

    def publish(self, text: str) -> bool:
        """Put text on CLIPBOARD and PRIMARY; True once mutter has taken over both offers."""
        return bool(self.call(self._publish, text.encode()))

    def counts(self):
        return self.call(lambda: (self.data_requests[self.CLIPBOARD], self.data_requests[self.PRIMARY]))

    def wait_served(self, base, timeout: float) -> bool:
        """Wait for an app to fetch the text after the paste keystroke."""
        return bool(self.call(self._wait_served, base, timeout, timeout=timeout + 2))

    def read_clipboard(self):
        return self.call(self._read_clipboard, timeout=5.0)

    def restore_clipboard(self, data: bytes) -> bool:
        return bool(self.call(self._restore_clipboard, data))

    # --- X thread ----------------------------------------------------------------------
    def run(self):
        delay = 1.0
        while True:
            try:
                self._connect()
                self.connected.set()
                delay = 1.0
                while True:
                    self._pump(lambda: False)  # returns when a job is queued
                    while True:
                        try:
                            job = self.jobs.get_nowait()
                        except queue.Empty:
                            break
                        job()
            except Exception as e:
                self.connected.clear()
                log.warning("x11: %r; reconnecting in %.0f s", e, delay)
                time.sleep(delay)
                delay = min(delay * 2, 30)

    def _connect(self):
        from Xlib import X, Xatom, display
        self.X, self.Xatom = X, Xatom
        self.d = display.Display()
        self.d.set_error_handler(lambda err, *_: log.debug("x11 error: %s", err))
        atom = self.d.intern_atom
        self.CLIPBOARD, self.PRIMARY = atom("CLIPBOARD"), Xatom.PRIMARY
        self.TARGETS, self.UTF8 = atom("TARGETS"), atom("UTF8_STRING")
        self.TIMESTAMP, self.INCR = atom("TIMESTAMP"), atom("INCR")
        self.PROP, self.CLOCK = atom("DICTATE_SELECTION"), atom("DICTATE_CLOCK")  # transfer / timestamp
        self.win = self.d.screen().root.create_window(-10, -10, 1, 1, 0, X.CopyFromParent,
                                                      event_mask=X.PropertyChangeMask)  # never mapped
        self.data = {self.CLIPBOARD: b"", self.PRIMARY: b""}
        self.data_requests = {self.CLIPBOARD: 0, self.PRIMARY: 0}
        self.owned: dict[int, int] = {}  # selection -> timestamp we took it with
        self.targets_pending: set[int] = set()
        self.prop_time = None
        self.prop_new = False
        self.notify_event = None
        self.d.flush()

    def _pump(self, until, deadline=None) -> bool:
        """Dispatch X events until until() holds. Without a deadline, also return on a new job."""
        while not until():
            while self.d.pending_events():
                self._dispatch(self.d.next_event())
                if until():
                    return True
            timeout = None if deadline is None else deadline - time.monotonic()
            if timeout is not None and timeout <= 0:
                return False
            fds = [self.d.fileno()] + ([self.wake_r] if deadline is None else [])
            ready, _, _ = select.select(fds, [], [], timeout)
            if self.wake_r in ready:
                os.read(self.wake_r, 512)
                return False
        return True

    def _dispatch(self, e):
        X = self.X
        if e.type == X.SelectionRequest:
            self._answer(e)
        elif e.type == X.SelectionClear and e.window.id == self.win.id:
            self.owned.pop(e.atom, None)
            self.targets_pending.discard(e.atom)
        elif e.type == X.PropertyNotify and e.window.id == self.win.id:
            if e.atom == self.CLOCK:
                self.prop_time = e.time
            elif e.atom == self.PROP:
                self.prop_new = self.prop_new or e.state == X.PropertyNewValue
        elif e.type == X.SelectionNotify and e.requestor.id == self.win.id:
            self.notify_event = e

    def _answer(self, e):
        from Xlib.protocol import event
        prop, ok = e.property or e.target, False  # obsolete requestors pass no property
        if e.selection in self.owned:
            if e.target == self.TARGETS:
                e.requestor.change_property(prop, self.Xatom.ATOM, 32, [self.TARGETS, self.UTF8, self.TIMESTAMP])
                self.targets_pending.discard(e.selection)
                ok = True
            elif e.target == self.UTF8:
                e.requestor.change_property(prop, self.UTF8, 8, self.data[e.selection])
                self.data_requests[e.selection] += 1
                ok = True
            elif e.target == self.TIMESTAMP:
                e.requestor.change_property(prop, self.Xatom.INTEGER, 32, [self.owned[e.selection]])
                ok = True
        e.requestor.send_event(event.SelectionNotify(time=e.time, requestor=e.requestor, selection=e.selection,
                                                     target=e.target, property=prop if ok else self.X.NONE))
        self.d.flush()

    def _server_time(self) -> int:
        """A real server timestamp (ICCCM forbids CurrentTime for taking selections)."""
        self.prop_time = None
        self.win.change_property(self.CLOCK, self.Xatom.STRING, 8, b"", self.X.PropModeAppend)
        self.d.flush()
        self._pump(lambda: self.prop_time is not None, time.monotonic() + 0.3)
        return self.prop_time or self.X.CurrentTime

    def _own(self, selection: int, data: bytes, when: int):
        self.data[selection] = data
        self.data_requests[selection] = 0
        self.win.set_selection_owner(selection, when)
        self.owned[selection] = when

    def _publish(self, data: bytes) -> bool:
        when = self._server_time()
        selections = (self.CLIPBOARD, self.PRIMARY)
        for selection in selections:
            self._own(selection, data, when)
        self.d.flush()
        mine = {s for s in selections if getattr(self.d.get_selection_owner(s), "id", 0) == self.win.id}
        if len(mine) != len(selections):
            return False
        if not self.bridged:  # plain X11: apps ask us directly when they paste
            return True
        # mutter fetches TARGETS from a new owner before Wayland apps see the new offer.
        self.targets_pending = set(selections)
        if not self._pump(lambda: not self.targets_pending, time.monotonic() + 0.4):
            return False
        # mutter's clipboard manager copies new CLIPBOARD text right away; let that request
        # pass so it isn't mistaken for the paste.
        self._pump(lambda: self.data_requests[self.CLIPBOARD] > 0, time.monotonic() + 0.15)
        return True

    def _wait_served(self, base, timeout: float) -> bool:
        current = lambda: (self.data_requests[self.CLIPBOARD], self.data_requests[self.PRIMARY])
        return self._pump(lambda: current() != base, time.monotonic() + timeout)

    def _read_clipboard(self, timeout=0.3):
        """The current CLIPBOARD text as bytes, or None (empty, not text, or owner not answering)."""
        if self.CLIPBOARD in self.owned:
            return self.data[self.CLIPBOARD]
        when = self._server_time()
        self.win.delete_property(self.PROP)
        self.notify_event = None
        self.win.convert_selection(self.CLIPBOARD, self.UTF8, self.PROP, when)
        self.d.flush()
        answered = lambda: self.notify_event is not None and self.notify_event.selection == self.CLIPBOARD
        if not self._pump(answered, time.monotonic() + timeout) or self.notify_event.property == self.X.NONE:
            return None
        prop = self.win.get_full_property(self.PROP, self.X.AnyPropertyType)
        if prop is None:
            return None
        if prop.property_type != self.INCR:
            self.win.delete_property(self.PROP)
            self.d.flush()
            return bytes(prop.value)
        # Large contents arrive incrementally (ICCCM INCR): delete, wait for a chunk, repeat.
        data, deadline = bytearray(), time.monotonic() + 3.0
        while True:
            self.prop_new = False
            self.win.delete_property(self.PROP)
            self.d.flush()
            if not self._pump(lambda: self.prop_new, deadline):
                return None
            chunk = self.win.get_full_property(self.PROP, self.X.AnyPropertyType)
            if chunk is None or not len(chunk.value):
                self.win.delete_property(self.PROP)
                self.d.flush()
                return bytes(data)
            data += bytes(chunk.value)

    def _restore_clipboard(self, data: bytes) -> bool:
        if self.CLIPBOARD not in self.owned:  # the user copied something meanwhile; keep it
            return False
        self._own(self.CLIPBOARD, data, self._server_time())
        self.d.flush()
        return True


class NotApproved(Exception):
    pass


class PortalShortcut(threading.Thread):
    """The push-to-talk key, via the xdg-desktop-portal GlobalShortcuts interface.

    GNOME grabs the key (apps never see it) and reports press and release. The first bind of
    a new shortcut id shows GNOME's "Add Keyboard Shortcuts" dialog; later binds are silent.
    """

    def __init__(self, cfg, emit):
        super().__init__(name="portal", daemon=True)
        self.cfg, self.emit = cfg, emit

    def run(self):
        delay = 1.0
        while True:
            started = time.monotonic()
            try:
                self._session()
            except NotApproved as e:
                self.emit("not_approved", str(e))
                return
            except Exception as e:
                log.warning("portal: %s", e)
            self.emit("key_lost")
            delay = 1.0 if time.monotonic() - started > 60 else min(delay * 2, 30)
            time.sleep(delay)

    def _session(self):
        from jeepney import DBusAddress, HeaderFields, MatchRule, new_method_call
        from jeepney.bus_messages import message_bus
        from jeepney.io.blocking import open_dbus_connection

        desktop = DBusAddress("/org/freedesktop/portal/desktop", bus_name="org.freedesktop.portal.Desktop")
        shortcuts = desktop.with_interface("org.freedesktop.portal.GlobalShortcuts")
        field_of = lambda msg, name: msg.header.fields.get(getattr(HeaderFields, name))
        restarted = lambda msg: field_of(msg, "member") == "NameOwnerChanged" and msg.body[1] != ""

        with open_dbus_connection("SESSION") as conn:
            def subscribe(q, arg0=None, **match):
                rule = MatchRule(type="signal", **match)
                if arg0 is not None:
                    rule.add_arg_condition(0, arg0)
                conn.filter(rule, queue=q)
                dbus_call(conn, message_bus.AddMatch(rule))

            # Must be the first portal call on this connection: it gives us an app id.
            registry = desktop.with_interface("org.freedesktop.host.portal.Registry")
            dbus_call(conn, new_method_call(registry, "Register", "sa{sv}", (APP_ID, {})))

            sender = conn.unique_name.lstrip(":").replace(".", "_")
            responses, signals = deque(), deque(maxlen=1000)
            subscribe(responses, interface="org.freedesktop.portal.Request", member="Response",
                      path_namespace=f"/org/freedesktop/portal/desktop/request/{sender}")
            for member in ("Activated", "Deactivated"):
                subscribe(signals, interface="org.freedesktop.portal.GlobalShortcuts", member=member,
                          path=desktop.object_path)
            for name in ("org.freedesktop.portal.Desktop", "org.freedesktop.impl.portal.desktop.gnome"):
                subscribe(signals, arg0=name, sender="org.freedesktop.DBus", interface="org.freedesktop.DBus",
                          member="NameOwnerChanged", path="/org/freedesktop/DBus")

            def request(method, signature, args, options=None):
                opts = dict(options or {}, handle_token=("s", "dictate_" + secrets.token_hex(6)))
                (handle,) = dbus_call(conn, new_method_call(shortcuts, method, signature, (*args, opts)))
                while True:  # may wait for a while if GNOME shows its dialog
                    try:
                        msg = conn.recv_until_filtered(responses, timeout=1.0)
                    except TimeoutError:
                        if any(restarted(m) for m in signals):
                            raise RuntimeError("the portal restarted")
                        continue
                    if field_of(msg, "path") == handle:
                        return msg.body

            code, results = request("CreateSession", "a{sv}", (),
                                    {"session_handle_token": ("s", "dictate_" + secrets.token_hex(6))})
            if code != 0:
                raise RuntimeError(f"CreateSession failed (response {code})")
            session = results["session_handle"][1]
            subscribe(signals, interface="org.freedesktop.portal.Session", member="Closed", path=session)

            asked = time.monotonic()
            wanted = {"description": ("s", "Hold to dictate"),
                      "preferred_trigger": ("s", keys.portal(keys.parse(self.cfg.trigger)))}
            code, results = request("BindShortcuts", "oa(sa{sv})sa{sv}",
                                    (session, [(self.cfg.shortcut_id, wanted)], ""))
            if code != 0:
                if time.monotonic() - asked > 1.5:  # a person answered the dialog
                    raise NotApproved("the keyboard shortcut was not approved")
                raise RuntimeError(f"BindShortcuts failed (response {code})")
            bound = dict(results.get("shortcuts", ("", []))[1])
            trigger = bound.get(self.cfg.shortcut_id, {}).get("trigger_description", ("s", "?"))[1]
            log.info("push-to-talk key bound: %s", trigger)
            self.emit("key_ready", trigger)

            while True:
                msg = conn.recv_until_filtered(signals)
                member = field_of(msg, "member")
                if member in ("Activated", "Deactivated"):
                    if msg.body[0] == session and msg.body[1] == self.cfg.shortcut_id:
                        self.emit("press" if member == "Activated" else "release", time.monotonic())
                elif member == "Closed":
                    raise RuntimeError("the portal closed our session")
                elif restarted(msg):
                    raise RuntimeError(f"{msg.body[0]} restarted")


class X11Hotkey(threading.Thread):
    """The push-to-talk key on an X11 desktop (e.g. Cinnamon on LMDE).

    A passive grab on the root window catches the press and keeps it from apps. X11 then gives
    us the whole keyboard until the key is released, which would swallow our own Shift+Insert,
    so the grab is handed back at once and the release is found by polling the key state.
    Auto-repeat is switched off for this one key (again whenever a keyboard is plugged in or
    comes back after a suspend), because every repeat would grab the keyboard once more.
    A combination (Ctrl+Alt+D) is grabbed with its modifiers; it ends when its key is released.
    A modifier key on its own (Right Ctrl) is still a modifier: pressing another key while it is
    held means a shortcut, so the dictation is cancelled (the shortcut works as usual).
    """

    def __init__(self, cfg, emit):
        super().__init__(name="x11-key", daemon=True)
        self.cfg, self.emit = cfg, emit

    def run(self):
        from Xlib import X, XK, display, error
        from Xlib.ext import xinput
        try:
            trigger = keys.parse(self.cfg.trigger)
            d = display.Display()
            root = d.screen().root
            code = d.keysym_to_keycode(XK.string_to_keysym(trigger.key))
            if not code:
                raise RuntimeError(f"no key on this keyboard produces {trigger.key}")
            catch = error.CatchError(error.BadAccess)
            for mods in (0, X.Mod2Mask, X.LockMask, X.Mod2Mask | X.LockMask):  # NumLock / CapsLock on or off
                root.grab_key(code, keys.x11_mask(trigger) | mods, True, X.GrabModeAsync, X.GrabModeAsync,
                              onerror=catch)
            d.change_keyboard_control(key=code, auto_repeat_mode=X.AutoRepeatModeOff)
            root.xinput_select_events([(xinput.AllDevices, xinput.HierarchyChangedMask)])
            d.sync()
            if catch.get_error():
                raise RuntimeError(f"another program already uses {self.cfg.trigger}")
        except Exception as e:
            self.emit("fatal", f"The dictation key could not be set up: {e}")
            return
        log.info("push-to-talk key grabbed: %s (X11)", trigger)
        self.emit("key_ready", self.cfg.trigger)
        held = lambda keymap: keymap[code // 8] & (1 << (code % 8))
        down, before = False, None  # keys already down when the trigger went down
        while True:
            if down and not d.pending_events():
                select.select([d.fileno()], [], [], 0.02)  # a repeat wakes us at once; else poll the key
                if d.pending_events():
                    continue
                keymap = d.query_keymap()
                if not held(keymap):
                    down = False
                    self.emit("release", time.monotonic())
                elif trigger.lone_modifier and any(k & ~b for k, b in zip(keymap, before)):
                    self.emit("cancel")  # another key went down: Right Ctrl was part of a shortcut
                    before = keymap
                continue
            e = d.next_event()
            if e.type == X.KeyPress and e.detail == code:
                d.ungrab_keyboard(X.CurrentTime)  # let our Shift+Insert reach the app while the key is held
                if down:  # a repeat: some keyboard still repeats the key
                    d.change_keyboard_control(key=code, auto_repeat_mode=X.AutoRepeatModeOff)
                d.flush()
                keymap = d.query_keymap()
                if not down and held(keymap):  # not a repeat, nor a press that is already over
                    down, before = True, keymap
                    self.emit("press", time.monotonic())
            elif getattr(e, "evtype", None) == xinput.HierarchyChanged:  # a keyboard came (back)
                d.change_keyboard_control(key=code, auto_repeat_mode=X.AutoRepeatModeOff)
                d.flush()


def microphone() -> str:
    """The default input device and its volume, for the log when only silence came in."""
    if not LINUX:
        try:
            import sounddevice as sd
            return sd.query_devices(kind="input")["name"]
        except Exception as e:
            return f"unknown ({e})"
    try:
        wpctl = lambda *args: subprocess.run(["wpctl", *args, "@DEFAULT_AUDIO_SOURCE@"], capture_output=True,
                                             text=True, timeout=2).stdout
        name = re.search(r'node\.description = "([^"]*)"', wpctl("inspect"))
        return f"{name.group(1) if name else '?'}, {wpctl('get-volume').strip() or 'volume unknown'}"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown (no wpctl)"


class Recorder:
    """16 kHz mono float32 from the default PipeWire source, via pw-record. One per key press."""

    def __init__(self, emit):
        self.emit = emit
        self.proc = None
        self.reader = None
        self.buf = bytearray()
        self.t_first = None

    def start(self) -> None:
        self.buf, self.t_first = bytearray(), None
        if shutil.which("pw-record"):
            command = ["pw-record", "--raw", "--rate=16000", "--channels=1", "--format=f32", "--latency=20ms",
                       "-P", '{ application.name = "Dictate", node.description = "Dictation" }', "-"]
        else:  # PulseAudio systems
            command = ["parecord", "--raw", "--format=float32le", "--rate=16000", "--channels=1",
                       "--latency-msec=20", "--client-name=Dictate"]
        self.proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=C_LOCALE)
        self.reader = threading.Thread(target=self._read, args=(self.proc, self.buf), name="audio", daemon=True)
        self.reader.start()

    def _read(self, proc, buf: bytearray) -> None:
        while chunk := proc.stdout.read1(65536):
            if self.t_first is None:
                self.t_first = time.monotonic()
                self.emit("first_audio", self.t_first)
            buf += chunk

    def seconds(self) -> float:
        return len(self.buf) / 4 / RATE

    def snapshot(self) -> np.ndarray:
        """Everything recorded so far (the recording keeps going)."""
        data = bytes(self.buf)
        return np.frombuffer(data[: len(data) // 4 * 4], dtype="<f4").copy()

    def stop(self) -> np.ndarray:
        proc, self.proc = self.proc, None
        if proc is None:
            return np.zeros(0, np.float32)
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        self.reader.join(timeout=1)
        return self.snapshot()


class PortAudioRecorder(Recorder):
    """The same, through PortAudio (sounddevice) on Windows and macOS. If the device can't record
    at 16 kHz itself, its own rate is converted by linear interpolation."""
    lock = threading.Lock()  # PortAudio is re-initialized between recordings to see new devices

    def start(self) -> None:
        import sounddevice as sd
        self.buf, self.t_first, self.carry, self.pos = bytearray(), None, np.zeros(0, np.float32), 0.0
        with self.lock:
            try:
                self.rate = RATE
                self.proc = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", latency="low",
                                           callback=self._callback)
            except sd.PortAudioError:
                self.rate = float(sd.query_devices(kind="input")["default_samplerate"])
                self.proc = sd.InputStream(samplerate=self.rate, channels=1, dtype="float32", latency="low",
                                           callback=self._callback)
            self.proc.start()

    def _callback(self, indata, frames, time_info, status) -> None:
        if self.t_first is None:
            self.t_first = time.monotonic()
            self.emit("first_audio", self.t_first)
        mono = indata[:, 0]
        self.buf += (mono if self.rate == RATE else self._resample(mono)).astype("<f4").tobytes()

    def _resample(self, x: np.ndarray) -> np.ndarray:
        x = np.concatenate([self.carry, x])
        step = self.rate / RATE
        n = int((len(x) - 1 - self.pos) // step) + 1 if len(x) - 1 >= self.pos else 0
        out = np.interp(self.pos + np.arange(n) * step, np.arange(len(x)), x)
        end = self.pos + n * step
        self.carry, self.pos = x[int(end):], end - int(end)
        return out

    def stop(self) -> np.ndarray:
        stream, self.proc = self.proc, None
        if stream is None:
            return np.zeros(0, np.float32)
        try:
            stream.stop()
            stream.close()
        except Exception as e:
            log.warning("closing the microphone stream failed: %s", e)
        threading.Thread(target=self.refresh, name="audio-devices", daemon=True).start()
        return self.snapshot()

    @classmethod
    def refresh(cls) -> None:
        """Re-read the device list, so a headset plugged in since start-up becomes the default."""
        import sounddevice as sd
        with cls.lock:
            try:
                sd._terminate()
                sd._initialize()
            except Exception as e:
                log.debug("PortAudio re-initialization failed: %s", e)


def new_recorder(emit) -> Recorder:
    return Recorder(emit) if LINUX else PortAudioRecorder(emit)


ROCM_LIBS = ("_rocm_sdk_core/lib/libamdhip64.so.7",  # what CTranslate2's ROCm build links against
             "_rocm_sdk_libraries/lib/libhipblas.so.3", "_rocm_sdk_libraries/lib/libhiprand.so.1")


def amd_gpu() -> tuple[str | None, str | None]:
    """The AMD GPU's target (e.g. gfx1031) and the one whose ROCm device code is installed (e.g. gfx1030)."""
    gpu = None
    for props in sorted(glob.glob("/sys/class/kfd/kfd/topology/nodes/*/properties")):
        try:
            found = re.search(r"^gfx_target_version (\d+)$", Path(props).read_text(), re.MULTILINE)
        except OSError:
            continue
        v = int(found.group(1)) if found else 0
        if v:  # e.g. 100301 = gfx1031 (major 10, minor 3, stepping 1); CPU nodes have 0
            gpu = f"gfx{v // 10000}{v // 100 % 100:x}{v % 100:x}"
            break
    site = sysconfig.get_paths()["purelib"]
    installed = sorted(p.name.split("-")[0].removeprefix("rocm_sdk_device_")
                       for p in Path(site).glob("rocm_sdk_device_gfx*.dist-info"))
    return gpu, (gpu if gpu in installed else installed[0] if installed else None)


def preload_gpu_libraries() -> tuple[str, list[str]]:
    """Load the pip-installed GPU libraries so ctranslate2 finds them without LD_LIBRARY_PATH:
    cuBLAS for its CUDA build (NVIDIA), the ROCm runtime for its ROCm build (AMD).
    Returns the platform ("cuda", "rocm", or "cpu" when neither is installed) and the libraries loaded."""
    if WINDOWS:
        from windows import preload_gpu_libraries as windows_preload
        return windows_preload()
    if MACOS:  # CTranslate2 has no GPU support on macOS
        return "cpu", []
    site = sysconfig.get_paths()["purelib"]
    if not Path(site, ROCM_LIBS[0]).exists():
        loaded = []
        for lib in ("libcublasLt.so.12", "libcublas.so.12"):  # Lt first: libcublas depends on it
            hits = glob.glob(f"{site}/nvidia/**/{lib}", recursive=True)
            if hits:
                ctypes.CDLL(hits[0], mode=ctypes.RTLD_GLOBAL)
                loaded.append(hits[0])
        return ("cuda" if loaded else "cpu"), loaded
    gpu, code = amd_gpu()
    if gpu and code and code != gpu:  # e.g. an RX 6700 XT (gfx1031) runs the gfx1030 code
        os.environ.setdefault("HSA_OVERRIDE_GFX_VERSION", f"{int(code[3:-2])}.{int(code[-2], 16)}.{int(code[-1], 16)}")
    # The default stream-ordered allocator (hipMallocAsync) hung on its first allocation on an
    # RX 6700 XT (ROCm 7.14, kernel 6.12); CTranslate2's caching allocator works.
    os.environ.setdefault("CT2_CUDA_ALLOCATOR", "cub_caching")
    loaded = []
    for lib in ROCM_LIBS:
        ctypes.CDLL(f"{site}/{lib}", mode=ctypes.RTLD_GLOBAL)
        loaded.append(f"{site}/{lib}")
    return "rocm", loaded


def release_memory() -> None:
    """Free a dropped model's memory for real: glibc keeps freed memory in the loading thread's
    arena, so switching models back and forth would otherwise grow the process (measured: base
    and tiny alternating on the worker thread went from 380 MB to 730 MB; with this, ~490 MB)."""
    gc.collect()
    if LINUX:
        try:
            ctypes.CDLL(None).malloc_trim(0)
        except (OSError, AttributeError):  # not glibc (e.g. musl)
            pass


def model_dir(name: str) -> str:
    path = str(MODELS_DIR / name)
    if WINDOWS and not path.isascii():  # e.g. C:\\Users\\Ján: hand CTranslate2 the 8.3 short path
        from windows import short_path
        return short_path(path)
    return path


class Transcriber:
    def __init__(self, cfg):
        self.cfg = cfg
        self.model = self.batched = None
        self.desc, self.name, self.on_cpu = "not loaded", None, False
        self.gpu_available = None  # known after the first load: a GPU this install can use
        self.ready = threading.Event()
        # hotwords are added to every 30 s window (initial_prompt only reaches the first one)
        self.hotwords = ", ".join(cfg.vocabulary) + "." if cfg.vocabulary else None

    def load(self, device: str = "auto", gpu_model: str | None = None, cpu_model: str | None = None) -> None:
        """Load the model for `device` ("auto", "gpu" or "cpu"): the GPU model in the best compute type
        the GPU supports (GTX 10xx cards have no fast float16), else the CPU model. The previous model
        is freed first, so two models never share the GPU's memory."""
        self.ready.clear()
        self.model = self.batched = None
        release_memory()
        gpu_model, cpu_model = gpu_model or self.cfg.model, cpu_model or self.cfg.fallback_model
        platform, _libs = preload_gpu_libraries()
        import ctranslate2
        from faster_whisper import BatchedInferencePipeline, WhisperModel
        self.gpu_available = platform != "cpu" and ctranslate2.get_cuda_device_count() > 0
        ladder = []
        if device != "cpu" and self.gpu_available:
            supported = ctranslate2.get_supported_compute_types("cuda")
            ladder += [("cuda", t, gpu_model) for t in ("float16", "int8_float16", "int8", "float32") if t in supported]
        supported = ctranslate2.get_supported_compute_types("cpu")
        ladder += [("cpu", t, cpu_model) for t in ("int8", "float32") if t in supported]
        wanted_gpu = device == "gpu" or (device == "auto" and self.gpu_available)
        for where, compute_type, name in ladder:
            if not models.installed(MODELS_DIR, name):
                log.warning("model %s is missing or incomplete", MODELS_DIR / name)
                continue
            started = time.monotonic()
            try:
                model = WhisperModel(model_dir(name), device=where, compute_type=compute_type,
                                     cpu_threads=models.cpu_threads() if where == "cpu" else 0)
                warmup = np.random.default_rng(0).standard_normal(RATE).astype(np.float32) * 0.01
                list(model.transcribe(warmup, language="en", beam_size=1, max_new_tokens=8,
                                      without_timestamps=True)[0])
            except Exception as e:
                log.warning("loading %s on %s/%s failed: %s", name, where, compute_type, e)
                continue
            self.model, self.batched = model, BatchedInferencePipeline(model)
            self.name, self.on_cpu = name, where == "cpu"
            self.desc = f"{name} on {platform if where == 'cuda' else where}/{compute_type}"
            log.info("model ready: %s (%.1f s)", self.desc, time.monotonic() - started)
            if where == "cpu" and wanted_gpu:
                notify("Dictation is running on the CPU",
                       f"The GPU could not be used, so the {name} model runs on the processor instead.")
            self.ready.set()
            return
        raise RuntimeError("no Whisper model could be loaded")

    def run(self, audio: np.ndarray, language: str, *, words=False, prompt=None, beam_size=None,
            hotwords=True) -> list:
        """One model pass; returns the segments (with word timings if words=True). Vocabulary
        hints are left out for very short audio, where whisper tends to just echo them."""
        peak = float(np.abs(audio).max()) if audio.size else 0.0
        if 1e-4 <= peak < 0.3:
            audio = audio * min(0.9 / peak, 10.0)  # lift quiet input
        batched = len(audio) > 30 * RATE and not words
        fn = self.batched.transcribe if batched else self.model.transcribe
        beam_size = beam_size or (self.cfg.cpu_beam_size if self.on_cpu else self.cfg.beam_size)
        opts = dict(language=language, beam_size=beam_size, vad_filter=True,
                    condition_on_previous_text=False, without_timestamps=not words, word_timestamps=words,
                    hotwords=self.hotwords if hotwords else None, initial_prompt=prompt)
        if batched:
            opts["batch_size"] = 8
        accepted = inspect.signature(fn).parameters
        segments, _info = fn(audio, **{k: v for k, v in opts.items() if k in accepted})
        return list(segments)

    def detect(self, audio: np.ndarray, allowed=AUTO_LANGUAGES) -> tuple[str, float]:
        """The most likely of the allowed languages (whisper alone might say Czech or Polish) and
        its share of their combined probability."""
        try:
            probs = dict(self.model.detect_language(audio, vad_filter=True)[2])
        except Exception as e:
            log.debug("language detection failed: %s", e)
            return allowed[0], 0.0
        best = max(allowed, key=lambda lang: probs.get(lang, 0.0))
        return best, probs.get(best, 0.0) / (sum(probs.get(lang, 0.0) for lang in allowed) or 1.0)


_FILLERS = {"en": r"(?:u+h+m*|u+m+|e+r+m+)",  # um, umm, uh, uhh, uhm, erm (not "uh-oh", "umbrella")
            "sk": r"(?:e+h*m+|e{3,}|h+m+)"}  # ehm, eee, hmm


@functools.lru_cache(maxsize=None)
def _filler_regexes(language: str):
    word = _FILLERS.get(language, _FILLERS["en"])
    f = word + r"(?![\w'’-])"
    return (re.compile(rf"(^|[.!?…]\s+)(?:{f},?\s*)+", re.IGNORECASE),  # "Um, uh, so" at a sentence start
            re.compile(rf",\s+{f},(?=\s)", re.IGNORECASE),  # "I, um, think"
            re.compile(rf"\s*(?<![\w'’-]){f},?", re.IGNORECASE),  # "I think um maybe"
            re.compile(rf"{word}[,.!?…]*", re.IGNORECASE))  # a whole word (live typing)


def _drop_fillers(text: str, language: str = "en") -> str:
    start, middle, anywhere, _ = _filler_regexes(language)
    cleaned = start.sub(lambda m: m.group(1) + "\0", text)  # \0 marks "capitalize next"
    cleaned = anywhere.sub("", middle.sub("", cleaned))
    cleaned = re.sub(r"\0\s*(\w)", lambda m: m.group(1).upper(), cleaned).replace("\0", "")
    if cleaned == text:
        return text
    cleaned = re.sub(r"\s+([,.!?…;:])", r"\1", cleaned)  # no space before punctuation
    cleaned = re.sub(r",(?=[.!?…])", "", cleaned)  # ",." -> "."
    cleaned = re.sub(r"^[\s,.;:]+", "", cleaned)
    return " ".join(cleaned.split())


def clean_text(text: str, cfg, language: str = "en") -> str:
    """One tidy line: no control characters (no Enter or ESC reaches a terminal), no um/uh."""
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
    if cfg.remove_fillers:
        text = _drop_fillers(text, language)
    if text and cfg.trailing_space:
        text += " "
    return text


class LiveText:
    """The text typed live for one dictation, built word by word: one line, no fillers."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.typed = ""
        self.capitalize = False

    def add(self, words, language: str, final=False) -> str:
        filler = _filler_regexes(language)[3]
        piece = ""
        for _start, _end, word in words:
            word = "".join(ch for ch in word if ch.isprintable()).strip()
            if not word:
                continue
            so_far = (self.typed + piece).rstrip()
            if self.cfg.remove_fillers and filler.fullmatch(word):
                self.capitalize = self.capitalize or not so_far or so_far[-1] in ".!?…"
                continue
            if self.capitalize or not so_far or so_far[-1] in ".!?…":  # sentence start
                word, self.capitalize = word[0].upper() + word[1:], False
            piece += (" " if so_far else "") + word
        if final and self.cfg.trailing_space and (self.typed + piece):
            piece += " "
        self.typed += piece
        return piece


def split_chunks(text: str, limit: int) -> list[str]:
    """Split at sentence ends so each paste stays below Claude Code's 800-character collapse."""
    if limit <= 0 or len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for sentence in re.findall(r"[^.!?…]*[.!?…]+\s*|[^.!?…]+\s*$", text):
        while len(sentence) > limit:  # one huge sentence: cut after a space
            cut = sentence.rfind(" ", 0, limit) + 1 or limit
            if current:
                chunks.append(current)
                current = ""
            chunks.append(sentence[:cut])
            sentence = sentence[cut:]
        if len(current) + len(sentence) > limit:
            chunks.append(current)
            current = ""
        current += sentence
    chunks.append(current)
    return [c for c in chunks if c]


def speech_seconds(audio: np.ndarray) -> float:
    """How long ago speech started in this audio (Silero VAD)."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    stamps = get_speech_timestamps(audio, VadOptions())
    return (len(audio) - stamps[0]["start"]) / RATE if stamps else 0.0


def silence_seconds(audio: np.ndarray) -> float:
    """How long the speaker has been quiet at the end of this audio (Silero VAD, which ends speech
    only after 2 s of silence and pads it by 0.4 s, so this jumps from 0 to 1.6 s)."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    stamps = get_speech_timestamps(audio, VadOptions())
    return (len(audio) - stamps[-1]["end"]) / RATE if stamps else len(audio) / RATE


def _norm(word: str) -> str:
    return re.sub(r"[^\w]", "", word.lower())


class Streamer:
    """Decides which words of a growing recording are settled enough to type (LocalAgreement).

    Each pass re-transcribes the current window: the audio since the last committed sentence, so
    whisper hears the whole sentence it is working on. A word is committed once two consecutive
    passes agree on it and on the word after it, so its punctuation is settled too; the last word
    of a sentence is also committed once the speaker pauses after it. Committed text that has
    left the window is fed back as the prompt, for continuity.
    """
    TRIM_AFTER = 15.0  # once the window is this long, drop it up to the last committed sentence
    FORCE_AFTER = 10.0  # nothing settled for this long: commit everything but the last 3 s
    PAUSE = 0.8  # silence after a sentence's last word that settles it without a next word

    def __init__(self):
        self.committed: list[tuple[float, float, str]] = []
        self.pending: list[tuple[float, float, str]] = []
        self.window_start = 0.0

    @property
    def commit_time(self) -> float:
        return self.committed[-1][1] if self.committed else 0.0

    def prompt(self) -> str | None:
        return "".join(w[2] for w in self.committed if w[1] <= self.window_start).strip()[-200:] or None

    @property
    def preview(self) -> str:
        return "".join(w[2] for w in self.committed + self.pending).strip()

    def words_after_commit(self, segments, offset: float) -> list[tuple[float, float, str]]:
        words = [(offset + w.start, offset + w.end, w.word) for s in segments for w in (s.words or [])]
        words = [w for w in words if w[0] > self.commit_time - 0.1]
        if words and self.committed and abs(words[0][0] - self.commit_time) < 1.0:
            tail = [_norm(w[2]) for w in self.committed[-5:]]
            for k in range(min(len(tail), len(words)), 0, -1):  # words heard again at the boundary
                if tail[-k:] == [_norm(w[2]) for w in words[:k]]:
                    return words[k:]
        return words

    def step(self, words, audio_seconds: float) -> list[tuple[float, float, str]]:
        """Feed one pass's words; returns the words that just became settled."""
        agree = 0
        while agree < min(len(words), len(self.pending)) and _norm(words[agree][2]) == _norm(self.pending[agree][2]):
            agree += 1
        n = max(agree - 1, 0)  # the last agreed word waits until the word after it agrees too
        last = words[-1][2].rstrip() if words else ""
        if (agree == len(words) > 0 and audio_seconds - words[-1][1] >= self.PAUSE
                and last.endswith((".", "?", "!")) and not last.endswith("..")):
            n = agree  # ...unless it ends a sentence and the speaker paused ("..." trails off instead)
        if audio_seconds - self.commit_time > self.FORCE_AFTER:
            n = max(n, sum(1 for w in words if w[1] < audio_seconds - 3.0))
        new, self.pending = words[:n], words[n:]
        self.committed += new
        if audio_seconds - self.window_start > self.TRIM_AFTER:
            # Not up to a sentence that nothing follows yet (a pause): whisper invents words on a
            # window of silence.
            settled = self.committed if self.pending else self.committed[:-1]
            ends = [w[1] for w in settled if w[1] > self.window_start and w[2].rstrip().endswith((".", "?", "!", "…"))]
            if ends:
                self.window_start = ends[-1]
            elif audio_seconds - self.window_start > self.TRIM_AFTER + 10 and self.committed:
                self.window_start = max(self.window_start, self.commit_time)  # no sentence end in sight
        return new


@dataclass
class Session:
    """One press of the key: its recording and everything decided while transcribing it."""
    id: int
    rec: Recorder | None
    mode: str  # "en", "sk" or "auto", as chosen when the key went down
    live: bool
    sleep_offset: float
    t_press: float = 0.0
    language: str | None = None  # None until auto-detect has decided
    text: LiveText | None = None
    streamer: Streamer = field(default_factory=Streamer)
    passed_at: float = 0.0  # seconds of audio at the last live pass
    live_ok: bool = True  # switched off for this session if a live pass fails
    dropped: bool = False
    audio: np.ndarray | None = None
    t_release: float = 0.0


@dataclass
class PasteItem:
    text: str
    session: Session
    final: bool


class Worker(threading.Thread):
    """Runs every model pass: live passes while the key is held, the final pass after release."""
    STEP = 0.4  # seconds of new audio between live passes
    # silence_seconds() above this: the speaker has been quiet for 2 s. Live passes then wait for
    # speech, because whisper invents words ("Thank you.", "Bye.") for windows of silence.
    QUIET = 1.0

    def __init__(self, cfg, transcriber: Transcriber, paster, cues: Cues, topbar):
        super().__init__(name="transcribe", daemon=True)
        self.cfg, self.transcriber, self.paster, self.cues, self.topbar = cfg, transcriber, paster, cues, topbar
        self.cond = threading.Condition()
        self.active: Session | None = None
        self.finished: deque = deque()
        self.busy: tuple[float, float] | None = None  # (since, seconds of audio) while a pass runs
        self.loading: float | None = None  # since when a model has been loading
        self.wanted: tuple | None = None  # (device, gpu model, cpu model) to load next
        self.choice: tuple = ("auto", None, None)  # what was loaded last (or is loading)

    def want(self, choice: tuple) -> None:
        """Load this model choice once nothing is being dictated; the latest request wins. All loading
        happens on this thread, between passes, so a pass never meets a half-swapped model."""
        with self.cond:
            self.wanted = choice
            self.cond.notify()

    def begin(self, session: Session) -> None:
        with self.cond:
            self.active = session
            self.cond.notify()

    def end(self, session: Session, audio: np.ndarray, t_release: float) -> None:
        session.audio, session.t_release = audio, t_release
        self.topbar.busy(+1)
        with self.cond:
            if self.active is session:
                self.active = None
            self.finished.append(session)
            self.cond.notify()

    def drop(self, session: Session) -> None:
        with self.cond:
            if self.active is session:
                self.active = None
            session.dropped = True
            typed = bool(session.text and session.text.typed)
        if typed:  # live words already went out: let the paster finish the session (clipboard)
            self.paster.put(PasteItem("", session, final=True))

    def run(self):
        while True:
            with self.cond:
                while not self.finished and not self._live_due() and not self._load_due():
                    self.cond.wait(0.1)
                if self._load_due():
                    choice, self.wanted = self.wanted, None
                    session = None
                else:
                    session, final = (self.finished.popleft(), True) if self.finished else (self.active, False)
            if session is None:
                self._load(choice)
            elif final:
                try:
                    if self.transcriber.ready.is_set():
                        self.busy = (time.monotonic(), len(session.audio) / RATE)
                        self._final(session)
                    else:  # the last load failed; the tray says why
                        log.info("no speech model is loaded; dictation dropped")
                        self.cues.play("error")
                        self.topbar.event("error", "No speech model is loaded (the tray icon says why).")
                        if session.text and session.text.typed:  # let the paster restore the clipboard
                            self.paster.put(PasteItem("", session, final=True))
                finally:
                    self.busy = None
                    self.topbar.busy(-1)
            else:
                self.busy = (time.monotonic(), session.rec.seconds())
                try:
                    self._live(session)
                except Exception as e:
                    log.warning("live pass failed: %s", e)
                    session.live_ok = False
                self.busy = None

    def _load_due(self) -> bool:
        """A load waits until nothing is being dictated, unless there is no model at all yet."""
        return self.wanted is not None and (not self.transcriber.ready.is_set()
                                            or (self.active is None and not self.finished))

    def _load(self, choice: tuple) -> None:
        self.choice, self.loading = choice, time.monotonic()
        self.topbar.update(loading=True)
        try:
            self.transcriber.load(*choice)
            self.topbar.update(loading=False, problem=None, model=self.transcriber.desc)
        except Exception as e:
            log.exception("model loading failed")
            self.topbar.update(loading=False, problem=f"The speech model could not be loaded: {e}", model=None)
            notify("Dictation can't transcribe", f"The speech model could not be loaded: {e}")
        finally:
            self.loading = None

    def _live_due(self) -> bool:
        s = self.active
        # On the CPU a preview-only pass (live typing off) would delay the final pass.
        return (s is not None and s.live_ok and self.transcriber.ready.is_set()
                and (s.live or not self.transcriber.on_cpu)
                and s.rec.seconds() - s.passed_at >= self.STEP)

    def _live(self, s: Session) -> None:
        audio = s.rec.snapshot()
        seconds = len(audio) / RATE
        s.passed_at = seconds
        if s.language is None:  # auto-detect: Slovak needs ~1.5 s of speech to be told from English
            speech = speech_seconds(audio)
            if speech < 1.5:
                return
            language, share = self.transcriber.detect(audio)
            if share < 0.9 and speech < 2.5:
                return
            s.language = language
            log.info("detected language: %s (%.0f%% sure after %.1f s of speech)", language, share * 100, speech)
            self.topbar.update(detected=language)
        st = s.streamer
        start = st.window_start
        window = audio[int(start * RATE):]
        if len(window) < RATE or silence_seconds(window[-30 * RATE:]) > self.QUIET:
            return
        segments = self.transcriber.run(window, s.language, words=True, prompt=st.prompt(),
                                        beam_size=None if s.live else 1, hotwords=len(window) >= 2 * RATE)
        new = st.step(st.words_after_commit(segments, start), seconds)
        self.topbar.update(preview=st.preview)
        if s.live and new:
            with self.cond:
                if s.dropped:
                    return
                piece = s.text.add(new, s.language)
                if piece:
                    self.paster.put(PasteItem(piece, s, final=False))

    def _final(self, s: Session) -> None:
        started = time.monotonic()
        typed_live = s.live and bool(s.text.typed)
        try:
            piece, problem = self._final_text(s, typed_live)
        except Exception:
            log.exception("transcription failed")
            piece, problem = "", "transcription error"
        seconds = len(s.audio) / RATE
        if problem:
            peak = float(np.abs(s.audio).max()) if s.audio.size else 0.0
            log.info("%s in %.1f s of audio (peak %.0f dBFS)", problem, seconds, 20 * np.log10(max(peak, 1e-9)))
            self.cues.play("error")
            self.topbar.event({"muted": "muted", "no speech": "nothing"}.get(problem, "error"), problem)
            if problem == "muted":
                log.info("default microphone: %s", microphone())
                notify("Microphone is muted", "Only silence was recorded. Check the headset's mute switch.")
        else:
            log.info("transcribed %.1f s of %s audio in %.2f s (%d chars%s)", seconds, s.language,
                     time.monotonic() - started, len(s.text.typed) if typed_live else len(piece),
                     ", typed live" if typed_live else "")
            log.debug("text: %r", s.text.typed if typed_live else piece)
        if piece or typed_live:
            self.paster.put(PasteItem(piece, s, final=True))

    def _final_text(self, s: Session, typed_live: bool) -> tuple[str, str | None]:
        audio = s.audio
        if not typed_live and (not audio.size or float(np.abs(audio).max()) < 1e-4):
            return "", "muted"
        if s.language is None:
            s.language = self.transcriber.detect(audio)[0] if s.mode == "auto" else s.mode
        if typed_live:  # only the part after the last typed word is left
            st = s.streamer
            start = st.window_start
            window = audio[int(start * RATE):]
            words = []
            if len(window) >= RATE // 4 and (st.pending or silence_seconds(window[-30 * RATE:]) <= self.QUIET):
                words = st.words_after_commit(self._run(window, s.language, words=True, prompt=st.prompt()), start)
            return s.text.add(words, s.language, final=True), None
        segments = self._run(audio, s.language)
        text = clean_text(" ".join(seg.text.strip() for seg in segments), self.cfg, s.language)
        return (text, None) if text else ("", "no speech")

    def _run(self, audio, language, **kw):
        """A model pass that survives a dead CUDA context (e.g. after suspend) by reloading once."""
        try:
            return self.transcriber.run(audio, language, **kw)
        except Exception as e:
            log.warning("transcription failed (%s); reloading the model", e)
            self.busy = None  # the watchdog times loads separately
            self._load(self.choice)
            self.busy = (time.monotonic(), len(audio) / RATE)
            return self.transcriber.run(audio, language, **kw)


class Watchdog(threading.Thread):
    """Restarts dictate when a model pass hangs, instead of leaving it "Transcribing…" forever.

    A pass normally takes 0.3-1.5 s on a GPU. Once one has run for 30 s plus a quarter of its
    audio's length (or a model load for 3 minutes), every thread's stack goes to the log (to find
    the cause) and the program starts over.
    """

    def __init__(self, worker: Worker):
        super().__init__(name="watchdog", daemon=True)
        self.worker = worker

    LOAD_LIMIT = 180  # loading a model: slow disks, a virus scanner reading model.bin, a 2-core CPU

    def run(self):
        while True:
            time.sleep(2)
            busy, loading, now = self.worker.busy, self.worker.loading, time.monotonic()
            if busy is not None and now - busy[0] >= 30 + busy[1] / 4:
                log.error("a model pass has run for %.0f s (%.1f s of audio); thread stacks follow",
                          now - busy[0], busy[1])
            elif loading is not None and now - loading >= self.LOAD_LIMIT:
                log.error("loading the model has taken %.0f s; thread stacks follow", now - loading)
            else:
                continue
            if sys.stderr is not None:  # the log file or the journal
                faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
            if time.time() - float(os.environ.get("DICTATE_RESTARTED", 0)) < 600:
                notify("Dictation is stuck", "It already restarted itself recently. Restart the computer if "
                       "it stays stuck.")
                return
            if loading is not None and self.worker.choice[0] != "cpu" and self.worker.transcriber.gpu_available:
                os.environ["DICTATE_FORCE_CPU"] = "1"  # for the restarted copy, until dictation is started anew
                notify("Dictation restarted on the processor", "The graphics card did not respond while the "
                       "speech model was loading, so the model now runs on the CPU.")
            else:
                notify("Dictation restarted", "Transcribing got stuck, so dictation started over. The last "
                       "dictation was lost.")
            os.environ["DICTATE_RESTARTED"] = str(time.time())
            restart_self()


class ModelSwitch:
    """Applies the model and device chosen in the menu: a missing model is downloaded first, in a
    child process (dictate.py --download NAME), while the current one keeps working."""

    def __init__(self, ui: UiState, worker: Worker, topbar):
        self.ui, self.worker, self.topbar = ui, worker, topbar
        self.downloading: set[str] = set()
        self.lock = threading.Lock()
        # After a GPU hang the CPU is used until the user picks a model or device again.
        self.held = (ui.device, ui.gpu_model, ui.cpu_model) if os.environ.get("DICTATE_FORCE_CPU") else None

    def on_gpu(self) -> bool:
        """Whether the choice in the menu means the GPU (as far as one can be used)."""
        return self.target((self.ui.device, self.ui.gpu_model, self.ui.cpu_model))[0]

    def target(self, choice: tuple) -> tuple[bool, str]:
        """What a choice means: (on the GPU?, the model that runs)."""
        device, gpu_model, cpu_model = choice
        on_gpu = device != "cpu" and self.worker.transcriber.gpu_available is not False
        return on_gpu, gpu_model if on_gpu else cpu_model

    def changed(self) -> None:
        choice = (self.ui.device, self.ui.gpu_model, self.ui.cpu_model)
        if self.held is not None:
            if choice == self.held:
                return
            self.held = None
        if self.target(choice) == self.target(self.worker.wanted or self.worker.choice):
            return  # e.g. a language change, or "auto" -> "gpu" with a GPU already in use
        name = self.target(choice)[1]
        if not models.installed(MODELS_DIR, name):
            if name in models.MODELS:
                self.download(name)
            else:
                notify("Speech model not found", f"There is no model called {name} in {MODELS_DIR}.")
            return
        self.worker.want(choice)

    def download(self, name: str) -> None:
        with self.lock:
            if name in self.downloading:
                return
            self.downloading.add(name)
        threading.Thread(target=self._download, args=(name,), name="download", daemon=True).start()

    def _download(self, name: str) -> None:
        notify(f"Downloading the {name} speech model", f"{models.size_label(name)}. Dictation keeps working "
               "with the current model meanwhile.")
        log.info("downloading the %s model", name)
        env = {**os.environ, "HF_HUB_DISABLE_PROGRESS_BARS": "1"}
        try:
            proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--download", name], env=env,
                                    stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
            expected = models.MODELS[name][1] * 1e6
            while proc.poll() is None:
                done = models.partial_bytes(MODELS_DIR, name) / expected
                self.topbar.update(download=f"{name} {min(99, int(done * 100))} %")
                time.sleep(1)
            ok = proc.returncode == 0 and models.installed(MODELS_DIR, name)
        except OSError as e:
            log.error("cannot start the download: %s", e)
            ok = False
        finally:
            with self.lock:
                self.downloading.discard(name)
            self.topbar.update(download=None)
        if ok:
            log.info("the %s model is downloaded", name)
            self.changed()
        else:
            notify(f"The {name} model could not be downloaded", "Check the internet connection. The log has "
                   "the details.")


def restart_self() -> None:
    """Start this program over with the same arguments (systemd keeps seeing the same PID on Linux)."""
    for handler in logging.getLogger().handlers:
        handler.flush()
    if not WINDOWS:
        os.execv(sys.executable, sys.orig_argv)
    # Windows' execv doesn't quote arguments with spaces and changes the PID anyway: start a new copy,
    # which waits for our single-instance lock, then leave.
    from windows import release_single_instance
    release_single_instance()
    subprocess.Popen([sys.executable, *sys.orig_argv[1:]], close_fds=True,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    os._exit(0)


class Paster(threading.Thread):
    """Types text into the focused app (clipboard + Shift+Insert) and restores the clipboard."""

    def __init__(self, cfg, selection: SelectionOwner, keyboard: VirtualKeyboard | None, no_keyboard=None):
        super().__init__(name="paste", daemon=True)
        self.cfg, self.sel, self.kbd = cfg, selection, keyboard
        self.no_keyboard = no_keyboard or "/dev/uinput is not available"  # why kbd is None
        self.jobs: queue.Queue = queue.Queue()
        self.on_inject = lambda: None  # set by the controller (our keystrokes stop the key's auto-repeat)
        self.on_done = lambda kind, text: None  # "typed" (the whole text) or "copied" (why), for the panel
        self.saved: dict[int, bytes | None] = {}  # session id -> clipboard text from before it
        self.diverted: dict[int, tuple[str, str]] = {}  # session id -> (why not typed, text so far)
        self.live_started: set[int] = set()

    def put(self, item: PasteItem) -> None:
        self.jobs.put(item)

    def run(self):
        while True:
            item = self.jobs.get()
            try:
                self.paste(item)
            except Exception:
                log.exception("paste failed")

    def _problem(self, item: PasteItem) -> str | None:
        s = item.session
        if self.kbd is None:
            return self.no_keyboard
        if screen_locked():
            return "the screen is locked"
        if sleep_offset() - s.sleep_offset > 2:
            return "the computer was asleep"
        if item.final and s.t_release and time.monotonic() - s.t_release > STALE_SECONDS:
            return "transcribing took too long"
        return None

    def paste(self, item: PasteItem) -> None:
        s = item.session
        problem = None if s.id in self.diverted else self._problem(item)
        if problem:
            self.diverted[s.id] = (problem, "")
        if s.id in self.diverted:
            why, text = self.diverted[s.id]
            self.diverted[s.id] = (why, text + item.text)
            if item.final:
                self._give_up(s, *self.diverted.pop(s.id))
            return
        if s.id not in self.saved:
            self.saved[s.id] = self.sel.read_clipboard() if self.cfg.restore_clipboard else None
        chunks = split_chunks(item.text, self.cfg.paste_chunk_chars) if item.text else []
        if chunks and not self._modifiers_up(item.final):
            self.diverted[s.id] = ("a modifier key stayed pressed", "".join(chunks))
            if item.final:
                self._give_up(s, *self.diverted.pop(s.id))
            return
        for i, chunk in enumerate(chunks):
            if not self.sel.publish(chunk):
                self.diverted[s.id] = ("the clipboard handoff could not be confirmed", "".join(chunks[i:]))
                if item.final:
                    self._give_up(s, *self.diverted.pop(s.id))
                return
            base = self.sel.counts()
            self.kbd.shift_insert()
            self.on_inject()
            if not self.sel.wait_served(base, 1.5 if item.final else 0.8):
                log.info("no app fetched the pasted text")
            if not item.final and s.id not in self.live_started:
                self.live_started.add(s.id)
                log.info("first live words typed %.1f s after the key went down", time.monotonic() - s.t_press)
            if i + 1 < len(chunks):
                time.sleep(0.15)
        if not item.final:
            return
        if s.t_release:
            log.info("typed; release -> done %.2f s", time.monotonic() - s.t_release)
        self.on_done("typed", s.text.typed if s.text and s.text.typed else item.text)
        old = self.saved.pop(s.id, None)
        if old:  # nothing to restore if the clipboard was empty or held non-text (e.g. an image)
            time.sleep(0.3)
            if self.sel.restore_clipboard(old):
                log.info("restored the previous clipboard (%d bytes)", len(old))
            else:
                log.info("clipboard changed meanwhile; not restoring")

    def _modifiers_up(self, final: bool) -> bool:
        """Wait while Ctrl/Alt/Shift/Super are held: with a Ctrl+Alt+D key (or Right Ctrl on X11) our
        Shift+Insert would arrive as Ctrl+Alt+Shift+Insert. Live words wait as long as it takes."""
        held = getattr(self.kbd, "modifiers_held", None)
        if held is None or not held():
            return True
        log.info("waiting for Ctrl/Alt/Shift to be released before typing")
        deadline = time.monotonic() + (STALE_SECONDS if final else self.cfg.max_seconds)
        while time.monotonic() < deadline:
            time.sleep(0.05)
            if not held():
                return True
        return False

    def _give_up(self, s: Session, why: str, text: str) -> None:
        self.saved.pop(s.id, None)
        if not text.strip():
            return
        log.info("left %d chars on the clipboard: %s", len(text), why)
        self.sel.publish(text)
        self.on_done("copied", why)
        notify("Dictation copied to the clipboard", f"It was not typed because {why}. Paste it with Ctrl+V.")


def open_text_file(path: Path) -> None:
    """Open a settings file in a text editor (.toml often has no app associated with it)."""
    command = (["notepad.exe", str(path)] if WINDOWS else ["open", "-t", str(path)] if MACOS
               else ["gio", "open", str(path)])
    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        log.warning("cannot open %s: %s", path, e)


HERE = Path(__file__).resolve().parent
NO_WINDOW = 0x08000000 if WINDOWS else 0  # CREATE_NO_WINDOW: child processes without a console window


def open_settings_window():
    """The big settings window (bigui.py settings), a process of its own."""
    try:
        return subprocess.Popen([sys.executable, str(HERE / "bigui.py"), "settings"], creationflags=NO_WINDOW,
                                stdin=subprocess.DEVNULL, start_new_session=not WINDOWS)
    except OSError as e:
        log.warning("cannot open the settings window: %s", e)
        return None


class StateWatcher(threading.Thread):
    """Applies what the big settings window changes (it saves state.json) and runs its commands."""

    def __init__(self, ui: UiState, emit):
        super().__init__(name="state", daemon=True)
        self.ui, self.emit = ui, emit
        self.mtime = self._mtime()

    @staticmethod
    def _mtime() -> int:
        try:
            return STATE_PATH.stat().st_mtime_ns
        except OSError:
            return 0

    def run(self):
        command = RUNTIME_DIR / "command"
        while True:
            time.sleep(0.5)
            mtime = self._mtime()
            if mtime != self.mtime:
                self.mtime = mtime
                self.ui.reload()  # our own saves come back here too: then nothing has changed
            try:
                text = command.read_text(encoding="utf-8").strip()
                command.unlink()
            except OSError:
                continue
            if text == "quit":
                self.emit("quit")


class PanelProcess:
    """The big status panel (bigui.py panel): one JSON message per line on its stdin. It quits when
    dictation ends (its stdin closes); if it crashes it is started again, at most every 30 s."""

    def __init__(self, ui: UiState):
        self.ui, self.proc, self.started = ui, None, -60.0
        self.lock = threading.Lock()

    def send(self, msg: dict) -> None:
        with self.lock:
            if not self.ui.big_panel:
                self._stop()
                return
            msg = {**msg, "scale": self.ui.ui_scale, "colors": self.ui.ui_colors, "position": self.ui.panel_position}
            if self.proc is None or self.proc.poll() is not None:
                if time.monotonic() - self.started < 30:
                    return
                self.started = time.monotonic()
                try:
                    self.proc = subprocess.Popen([sys.executable, str(HERE / "bigui.py"), "panel"], text=True,
                                                 encoding="utf-8", stdin=subprocess.PIPE, creationflags=NO_WINDOW)
                except OSError as e:
                    log.warning("cannot start the big status panel: %s", e)
                    self.proc = None
                    return
            try:
                self.proc.stdin.write(json.dumps(msg) + "\n")
                self.proc.stdin.flush()
            except (OSError, ValueError):
                self.proc = None

    def _stop(self) -> None:
        if self.proc is not None:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            self.proc = None


class Quiet:
    """Stands in for the top-bar icon in the test modes."""

    def update(self, **changes):
        pass

    def busy(self, delta):
        pass

    def event(self, kind, text=""):
        pass


class TopBar:
    """The top-bar (tray) icon: shows ready / recording / working / problem plus the language; its
    menu switches language, model, device, live typing, sounds and large text. It also feeds the
    big status panel and status.json (read by the big settings window)."""
    ICONS = {"ready": "audio-input-microphone-symbolic", "recording": "media-record-symbolic",
             "working": "content-loading-symbolic", "problem": "microphone-disabled-symbolic"}

    def __init__(self, ui: UiState):
        sys.path.insert(0, str(HERE))
        from tray import MenuItem, TrayIcon
        self.ui, self.MenuItem = ui, MenuItem
        self.tray = TrayIcon("dictate", "Dictate", self.menu, self.clicked,
                             on_activate=self.open_settings if ui.big_settings else None)
        self.lock = threading.Lock()
        self.state = {"loading": True, "recording": False, "busy": 0, "problem": None, "detected": None,
                      "preview": "", "model": None, "download": None, "key": None}
        self.on_quit = lambda: None  # set by the daemon: ends the program cleanly
        self.switch: ModelSwitch | None = None  # set by the daemon: applies model and device choices
        self.panel = PanelProcess(ui)
        self.panel_ongoing = False  # the panel shows "Listening" or "Transcribing" (no outcome yet)
        self.settings_window = None
        self.status_text = ""
        ui.listeners.append(self.changed)

    def open_settings(self) -> None:
        if self.settings_window is None or self.settings_window.poll() is not None:
            self.settings_window = open_settings_window()

    def event(self, kind: str, text: str = "") -> None:
        """An outcome for the big panel: "typed" (text), "copied" (why), "muted", "nothing", "error"."""
        paste = "Cmd+V" if MACOS else "Ctrl+V"
        title, accent, seconds = {
            "typed": ("✓ Typed", False, 3), "copied": (f"Not typed ({text}). It is on the clipboard: paste it with "
                                                     f"{paste}.", True, 8),
            "muted": ("Only silence was recorded: is the microphone muted?", True, 8),
            "nothing": ("Nothing was heard.", False, 3), "error": (f"Dictation failed: {text}", True, 8),
        }.get(kind, (text, False, 3))
        self.panel_ongoing = False
        self.panel.send({"show": True, "title": title, "text": text if kind == "typed" else "", "accent": accent,
                         "hide_after": seconds})

    def start(self) -> None:
        self.tray.start()
        self.push()

    def update(self, **changes) -> None:
        with self.lock:
            self.state.update(changes)
        if {"download", "model", "key"} & set(changes):  # the menu shows them
            self.tray.refresh_menu()
        self.push()

    def busy(self, delta: int) -> None:
        with self.lock:
            self.state["busy"] += delta
        self.push()

    def changed(self) -> None:
        if (self.tray.on_activate is not None) != self.ui.big_settings:  # a click opens the window, or the menu
            self.tray.set_activate(self.open_settings if self.ui.big_settings else None)
        self.tray.refresh_menu()
        self.push()

    MODEL_IDS = {name: 400 + i for i, name in enumerate(models.MODELS)}  # 499: a model of your own

    def model_key(self) -> str:
        """The menu choice the model submenu changes: the GPU's model, or the CPU's."""
        return "gpu_model" if self.switch is None or self.switch.on_gpu() else "cpu_model"

    def menu(self) -> list:
        M, ui = self.MenuItem, self.ui
        with self.lock:
            downloading = self.state["download"] or ""
        current = getattr(ui, self.model_key())
        model_items = []
        for name, item_id in self.MODEL_IDS.items():
            if downloading.startswith(name + " "):
                note = f" – downloading {downloading.split(' ', 1)[1]}"
            elif not models.installed(MODELS_DIR, name):
                note = f" – download {models.size_label(name)}"
            else:
                note = ""
            model_items.append(M(item_id, f"{name} ({models.MODELS[name][2]}){note}", "radio", name == current))
        if current not in self.MODEL_IDS:
            model_items.append(M(499, current, "radio", True))
        gpu = self.switch is None or self.switch.worker.transcriber.gpu_available is not False
        on_gpu = self.model_key() == "gpu_model"
        run_on = [M(410, "Graphics card (GPU)" if gpu else "Graphics card (none usable)", "radio", on_gpu, enabled=gpu),
                  M(411, "Processor (CPU)", "radio", not on_gpu)]
        return [M(1, f"Hold {self.key_label()} to dictate, or tap it to start and stop", enabled=False),
                M(2, kind="separator"),
                M(10, LANGUAGES["en"], "radio", ui.language == "en"),
                M(11, LANGUAGES["sk"], "radio", ui.language == "sk"),
                M(12, LANGUAGES["auto"], "radio", ui.language == "auto"),
                M(3, kind="separator"),
                M(20, "Type while speaking", "check", ui.live),
                M(21, "Sounds", "check", ui.sounds),
                M(4, kind="separator"),
                M(40, f"Speech model: {current}", children=model_items),
                M(41, f"Run on: {'graphics card' if on_gpu else 'processor'}", children=run_on),
                M(42, "Large text", children=[
                    M(420, "Big status panel while dictating", "check", ui.big_panel),
                    M(421, "Clicking the icon opens the big settings window", "check", ui.big_settings),
                    M(422, "Text size and colours…")]),
                M(5, kind="separator"),
                M(32, "Settings window…"),
                M(30, "Open settings file"),
                M(31, "Stop dictation")]

    def key_label(self) -> str:
        """The key as the desktop reported it when it bound the shortcut, else from the settings."""
        with self.lock:
            key = self.state["key"] or self.ui.trigger
        try:
            return keys.label(keys.parse(key), sys.platform)
        except ValueError:
            return key  # e.g. GNOME's own description of the shortcut the user approved

    def clicked(self, item_id: int) -> None:
        if item_id in (10, 11, 12):
            self.ui.set(language=("en", "sk", "auto")[item_id - 10])
        elif item_id == 20:
            self.ui.set(live=not self.ui.live)
        elif item_id == 21:
            self.ui.set(sounds=not self.ui.sounds)
        elif item_id in self.MODEL_IDS.values():
            name = next(n for n, i in self.MODEL_IDS.items() if i == item_id)
            self.ui.set(**{self.model_key(): name})
        elif item_id in (410, 411):
            self.ui.set(device="gpu" if item_id == 410 else "cpu")
        elif item_id == 420:
            self.ui.set(big_panel=not self.ui.big_panel)
        elif item_id == 421:
            self.ui.set(big_settings=not self.ui.big_settings)
        elif item_id in (32, 422):
            self.open_settings()
        elif item_id == 30:
            open_text_file(CONFIG_PATH)
        elif item_id == 31:
            if "INVOCATION_ID" in os.environ:  # running as the systemd service
                subprocess.Popen(["systemctl", "--user", "stop", "dictate.service"])
            else:  # started directly (an autostart entry, Windows, macOS)
                self.on_quit()

    def push(self) -> None:
        with self.lock:
            s = dict(self.state)
        language = self.ui.language
        if s["recording"]:  # a press while a model loads still records
            icon, tip = "recording", "Listening…"
        elif s["problem"]:
            icon, tip = "problem", s["problem"]
        elif s["loading"]:
            icon, tip = "working", "Loading the speech model…"
        elif s["busy"]:
            icon, tip = "working", "Transcribing…"
        else:
            icon, tip = "ready", f"Hold {self.key_label()} to dictate, or tap it to start and stop"
        code = language.upper()
        if s["recording"]:
            if language == "auto" and s["detected"]:
                code = f"AUTO·{s['detected'].upper()}"
            label = f"● {code}"
            preview = s["preview"]  # what it hears right now, before it's settled enough to type
            if preview:
                label += f"  {'…' if len(preview) > 30 else ''}{preview[-30:]}"
        else:
            label = code
        tooltip = f"{tip}\nLanguage: {LANGUAGES[language]}" + (f"\nModel: {s['model']}" if s["model"] else "")
        if s["download"]:
            tooltip += f"\nDownloading {s['download']}"
        self.tray.set(icon=self.ICONS[icon], label=label, tooltip=tooltip)
        self.push_panel(s, code)
        self.write_status({"pid": os.getpid(), "tip": tip, "model": s["model"], "download": s["download"],
                           "gpu_available": self.switch.worker.transcriber.gpu_available if self.switch else None,
                           "key": self.key_label()})

    def push_panel(self, s: dict, code: str) -> None:
        """The big panel shows what is going on; event() shows how it ended."""
        if s["recording"]:
            note = " (the speech model is still loading)" if s["loading"] else ""
            message = {"show": True, "title": f"● Listening — {code}{note}", "text": s["preview"], "accent": True}
        elif s["busy"]:
            message = {"show": True, "title": "Transcribing…", "text": s["preview"]}
        elif self.panel_ongoing:  # ended without an outcome (e.g. a recording too short to use)
            message = {"show": True, "title": "Transcribing…", "text": s["preview"], "hide_after": 2}
        else:
            return
        self.panel_ongoing = "hide_after" not in message
        if message != getattr(self, "panel_last", None):
            self.panel_last = message
            self.panel.send(message)

    def write_status(self, status: dict) -> None:
        """status.json: what the big settings window shows about the running dictation."""
        text = json.dumps(status)
        if text == self.status_text:
            return
        self.status_text = text
        try:
            tmp = RUNTIME_DIR / "status.tmp"
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(RUNTIME_DIR / "status.json")
        except OSError as e:
            log.debug("cannot write status.json: %s", e)


class Controller:
    """Push-to-talk state machine; runs on the main thread.

    Hold the key to dictate while it is down, or tap it to dictate until the next press.

    GNOME repeats Activated while the key is held and drops Deactivated if a modifier changes
    mid-hold, so a missing release is inferred once repeats stop (only after repeats have
    actually been observed, and not after we typed during the hold, which ends the repeat).
    """
    TAP = 0.4  # released this soon after the press: a tap

    def __init__(self, cfg, ui: UiState, cues: Cues, worker: Worker, topbar):
        self.cfg, self.ui, self.cues, self.worker, self.topbar = cfg, ui, cues, worker, topbar
        self.events: queue.Queue = queue.Queue()
        self.repeat_on, self.delay, self.interval = keyboard_repeat()
        self.gap = max(0.6, 10 * self.interval)  # no repeat for this long: the key is up
        # IDLE, HOLD, LATCHED (tapped: recording until the next press), TAIL (released, recording the
        # tail), WAIT_RELEASE
        self.state = "IDLE"
        self.session: Session | None = None
        self.ids = itertools.count(1)
        self.t_press = self.last = self.t_release = 0.0
        self.reps = 0
        self.repeats_seen = False
        self.stopping = False  # the press that ended a tapped dictation is still down
        self.last_injection = 0.0

    def emit(self, kind: str, value=None) -> None:
        self.events.put((kind, value))

    def mark_injection(self) -> None:
        self.last_injection = time.monotonic()

    def run(self) -> int:
        while True:
            try:
                kind, value = self.events.get(timeout=None if self.state == "IDLE" else 0.03)
            except queue.Empty:
                kind = value = None
            if kind is not None:
                code = self.handle(kind, value)
                if code is not None:
                    return code
            self.tick(time.monotonic())

    def handle(self, kind: str, value):
        if kind == "press":
            t = value
            if self.state == "IDLE":
                self._start(t)
            elif self.state == "LATCHED":  # the press after a tap stops the dictation
                self.state, self.t_release, self.last, self.stopping = "TAIL", t, t, True
                self.cues.play("stop")
            elif self.state == "TAIL":
                if self.stopping:  # that press is still down (repeating)
                    self.last = t
                elif t - self.t_release < 0.12:  # key bounce: keep the same recording
                    self.state, self.last = "HOLD", t
                else:
                    self._finish()
                    self._start(t)
            elif self.state == "HOLD":
                limit = self.gap if self.reps else self.delay + 0.25
                if self.repeat_on and t - self.last > limit and self.last_injection < self.t_press:
                    log.info("key release was not reported; stopping")  # this is a new press
                    self._finish()
                    self.state = "WAIT_RELEASE"
                else:
                    self.reps += 1
                    self.repeats_seen = True
                self.last = t
            elif self.state == "WAIT_RELEASE":
                self.last = t
        elif kind == "release":
            if self.state == "HOLD" and not self.reps and value - self.t_press < self.TAP:
                self.state = "LATCHED"  # a tap: keep dictating until the next press
                log.info("tapped: dictating until the next press")
            elif self.state == "HOLD":
                self.state, self.t_release = "TAIL", value
                self.cues.play("stop")
            elif self.state == "TAIL":
                self.stopping = False
            elif self.state == "WAIT_RELEASE":
                self.state = "IDLE"
        elif kind == "first_audio":
            if self.state in ("HOLD", "LATCHED"):
                self.cues.play("start")
            log.debug("microphone live %.0f ms after the key press", (value - self.t_press) * 1000)
        elif kind == "key_ready":  # value: the key, as the desktop or the config names it
            self.topbar.update(problem=None, key=value)
        elif kind == "key_lost":
            if self.state in ("HOLD", "LATCHED", "TAIL"):
                self._finish()
            self.state = "IDLE"
            self.topbar.update(problem="Waiting for the keyboard shortcut…")
        elif kind == "not_approved":
            notify("Dictation shortcut was not approved",
                   "Run 'systemctl --user restart dictate' to see GNOME's dialog again.")
            return 3
        elif kind == "fatal":
            notify("Dictation stopped", str(value))
            return 1
        elif kind == "quit":
            if self.session:
                self._discard()
            return 0
        elif kind == "cancel":  # the key is a modifier (Right Ctrl) and was used in a shortcut
            if self.state == "HOLD":
                log.info("another key went down with the dictation key: cancelled")
                self._discard()
                self.state = "WAIT_RELEASE"
        return None

    def tick(self, now: float) -> None:
        if self.state in ("HOLD", "LATCHED", "TAIL") and sleep_offset() - self.session.sleep_offset > 2:
            log.info("the computer slept during a recording; discarding it")
            self._discard()
            self.state = "IDLE"
        elif self.state == "TAIL" and now - self.t_release >= self.cfg.tail_ms / 1000:
            self._finish()
            self.state, self.stopping = ("WAIT_RELEASE" if self.stopping else "IDLE"), False
        elif self.state == "LATCHED" and now - self.t_press > self.cfg.max_seconds:
            log.info("maximum recording length reached")
            self.t_release = now
            self.cues.play("stop")
            self._finish()
            self.state = "IDLE"
        elif self.state == "HOLD":
            silent = now - self.last
            typed_during_hold = self.last_injection > self.t_press
            if self.repeat_on and self.repeats_seen and not typed_during_hold and (
                    (self.reps and silent > self.gap) or (not self.reps and silent > self.delay + 0.4)):
                log.info("key release was not reported; stopping")
                self.t_release = self.last + (self.interval if self.reps else self.delay)
                self._finish(trim_at=self.t_release + self.cfg.tail_ms / 1000)
                self.state = "IDLE"
            elif now - self.t_press > self.cfg.max_seconds:
                log.info("maximum recording length reached")
                self.t_release = now
                self._finish()
                self.state = "WAIT_RELEASE"
        elif self.state == "WAIT_RELEASE" and now - self.last > max(self.gap, 1.0):
            self.state = "IDLE"

    def _start(self, t: float) -> None:
        rec = new_recorder(self.emit)
        try:
            rec.start()
        except Exception as e:  # e.g. no pw-record, or no microphone (PortAudio)
            log.error("cannot start recording: %s", e)
            self.cues.play("error")
            return
        mode = self.ui.language
        self.session = Session(next(self.ids), rec, mode, self.ui.live, sleep_offset(), t_press=t,
                               language=None if mode == "auto" else mode, text=LiveText(self.cfg))
        self.state, self.t_press, self.last, self.reps = "HOLD", t, t, 0
        self.worker.begin(self.session)
        self.topbar.update(recording=True, detected=None, preview="")

    def _finish(self, trim_at: float | None = None) -> None:
        s, self.session = self.session, None
        if s is None:
            return
        t_first = s.rec.t_first
        audio = s.rec.stop()
        if trim_at is not None and t_first is not None:
            audio = audio[: max(0, int((trim_at - t_first) * RATE))]
        self.topbar.update(recording=False)
        if len(audio) < 0.3 * RATE:
            log.info("ignored a %.2f s recording", len(audio) / RATE)
            self.worker.drop(s)
            return
        t_release = self.t_release if self.t_release >= self.t_press else time.monotonic()
        self.worker.end(s, audio, t_release)

    def _discard(self) -> None:
        s, self.session = self.session, None
        if s is not None:
            s.rec.stop()
            self.worker.drop(s)
            self.topbar.update(recording=False)


def backend(cfg) -> str:
    if WINDOWS:
        return "windows"
    if MACOS:
        return "macos"
    if cfg.backend in ("wayland", "x11"):
        return cfg.backend
    return "wayland" if os.environ.get("XDG_SESSION_TYPE") == "wayland" else "x11"


def make_io(cfg):
    """For this desktop: the clipboard owner, the keyboard that presses the paste keys (None, with
    the reason, if it can't be created) and the thread class that reports the push-to-talk key."""
    kind = backend(cfg)
    if kind in ("windows", "macos"):
        platform = __import__(kind)
        selection, make_keyboard, hotkey = platform.Clipboard(), platform.Keyboard, platform.Hotkey
    elif kind == "wayland":
        selection, make_keyboard, hotkey = SelectionOwner(bridged=True), VirtualKeyboard, PortalShortcut
    else:
        selection, make_keyboard, hotkey = SelectionOwner(bridged=False), XTestKeyboard, X11Hotkey
    selection.start()
    try:
        return selection, make_keyboard(), None, hotkey
    except OSError as e:
        return selection, None, str(e), hotkey


def run_daemon(cfg) -> int:
    lock = single_instance()  # noqa: F841 (held for the life of the process)
    if lock is None:  # started again from the app menu: show the settings instead
        print("dictate is already running; opening its settings window")
        open_settings_window()
        return 0
    ui = UiState(cfg)
    topbar = TopBar(ui)
    topbar.start()
    cues = Cues(lambda: ui.sounds, cfg.sound_volume)
    trigger = ui.trigger  # the menu's choice wins over config.toml's starting value
    cfg.trigger, cfg.shortcut_id = trigger, shortcut_id(cfg, trigger)
    selection, keyboard, no_keyboard, Hotkey = make_io(cfg)
    if keyboard is None:
        log.error("cannot create the virtual keyboard: %s", no_keyboard)
        notify("Dictation can't type", f"{no_keyboard}; text will only be copied.")
    transcriber = Transcriber(cfg)
    paster = Paster(cfg, selection, keyboard, no_keyboard)
    paster.start()
    worker = Worker(cfg, transcriber, paster, cues, topbar)
    # After the GPU hung while loading (see Watchdog), stay on the CPU until dictation is started anew.
    worker.want(("cpu" if os.environ.get("DICTATE_FORCE_CPU") else ui.device, ui.gpu_model, ui.cpu_model))
    worker.start()
    Watchdog(worker).start()
    switch = ModelSwitch(ui, worker, topbar)
    topbar.switch = switch
    ui.listeners.append(switch.changed)
    ctl = Controller(cfg, ui, cues, worker, topbar)
    paster.on_inject = ctl.mark_injection
    paster.on_done = topbar.event
    topbar.on_quit = lambda: ctl.emit("quit")

    def key_changed():  # chosen in the settings window: the key's thread is set up anew
        if ui.trigger != trigger:
            log.info("the dictation key is now %s: restarting", ui.trigger)
            restart_self()
    ui.listeners.append(key_changed)
    StateWatcher(ui, ctl.emit).start()
    Hotkey(cfg, ctl.emit).start()
    try:
        return ctl.run()
    finally:
        if ctl.session:
            ctl.session.rec.stop()
        if keyboard:
            keyboard.close()
        (RUNTIME_DIR / "status.json").unlink(missing_ok=True)


def run_check(cfg) -> int:
    ok = True

    def report(name, good, detail="", required=True):
        nonlocal ok
        ok = ok and (bool(good) or not required)
        print(f"{'ok  ' if good else 'FAIL' if required else 'info'} {name}: {detail}", flush=True)

    report("python", True, f"{sys.version.split()[0]} ({sys.executable})")
    report("config", True, f"{CONFIG_PATH} ({'found' if CONFIG_PATH.exists() else 'defaults'})")
    ui = UiState(cfg)
    report("menu choices", True, f"language={ui.language}, live typing={ui.live}, sounds={ui.sounds}")
    try:
        platform, libs = preload_gpu_libraries()
    except OSError as e:
        report("GPU libraries", False, str(e))
        return 1
    if platform == "rocm":
        gpu, code = amd_gpu()
        override = os.environ.get("HSA_OVERRIDE_GFX_VERSION")
        report("ROCm libraries", True, ", ".join(Path(p).name for p in libs))
        report("AMD GPU", bool(gpu and code), f"{gpu or 'not found'}, ROCm device code for {code or '?'}"
               + (f" (HSA_OVERRIDE_GFX_VERSION={override})" if override else ""))
        kfd = os.access("/dev/kfd", os.R_OK | os.W_OK)
        report("/dev/kfd", kfd, "accessible" if kfd else "no access (README: Troubleshooting, AMD)")
    elif platform == "cuda":
        report("cuBLAS", len(libs) == 2, ", ".join(Path(p).name for p in libs))
    else:
        report("GPU libraries", True, "none installed: the model runs on the CPU")
    import ctranslate2
    count = ctranslate2.get_cuda_device_count()
    report("GPU devices", count > 0, f"{count} ({platform}, ctranslate2 {ctranslate2.__version__})",
           required=platform != "cpu")
    on_gpu = ui.device != "cpu" and platform != "cpu" and count > 0
    for kind, name in (("GPU", ui.gpu_model), ("CPU", ui.cpu_model)):
        report(f"{kind} model {name}", models.installed(MODELS_DIR, name), str(MODELS_DIR / name),
               required=on_gpu == (kind == "GPU"))
    have = sorted(p.name for p in MODELS_DIR.glob("*") if models.installed(MODELS_DIR, p.name)) if MODELS_DIR.exists() else []
    report("downloaded models", bool(have), ", ".join(have) or "none", required=False)
    if LINUX:
        check_linux(cfg, report)
    else:
        __import__(backend(cfg)).check(cfg, report)
    return 0 if ok else 1


def check_linux(cfg, report) -> None:
    wayland = backend(cfg) == "wayland"
    report("backend", True, f"{backend(cfg)} (session type {os.environ.get('XDG_SESSION_TYPE', '?')})")
    recorder = shutil.which("pw-record") or shutil.which("parecord")
    report("recorder", recorder is not None, recorder or "install pipewire-bin or pulseaudio-utils")
    if wayland:
        report("/dev/uinput", os.access("/dev/uinput", os.W_OK), "writable" if os.access("/dev/uinput", os.W_OK) else "not writable")
    try:
        from Xlib import display
        d = display.Display()
        detail = f"{d.get_display_name()}, vendor {d.display.info.vendor}"
        if wayland:
            report("XWayland", True, detail)
        else:  # X11 types with XTEST
            report("X11", d.has_extension("XTEST"), detail + (", XTEST" if d.has_extension("XTEST") else ", no XTEST"))
        d.close()
    except Exception as e:
        report("XWayland" if wayland else "X11", False, repr(e))
    try:
        from jeepney import DBusAddress, Introspectable
        from jeepney.bus_messages import message_bus
        from jeepney.io.blocking import open_dbus_connection
        with open_dbus_connection("SESSION") as conn:
            tray_host = dbus_call(conn, message_bus.NameHasOwner("org.kde.StatusNotifierWatcher"))[0]
            if wayland:
                portal = DBusAddress("/org/freedesktop/portal/desktop", bus_name="org.freedesktop.portal.Desktop")
                xml = dbus_call(conn, Introspectable(portal.object_path, portal.bus_name).Introspect())[0]
                for iface in ("org.freedesktop.host.portal.Registry", "org.freedesktop.portal.GlobalShortcuts"):
                    report(f"portal {iface.rsplit('.', 1)[1]}", f'"{iface}"' in xml, "present" if f'"{iface}"' in xml else "missing")
        report("top-bar icon host", tray_host, "present" if tray_host
               else "missing; on GNOME, enable the AppIndicator extension to get the menu")
    except Exception as e:
        report("D-Bus", False, repr(e))
    desktop = Path.home() / f".local/share/applications/{APP_ID}.desktop"
    report("desktop file", desktop.exists(), str(desktop))
    if wayland:  # on X11 the key's auto-repeat is switched off while dictate runs
        repeat_on, delay, interval = keyboard_repeat()
        report("key repeat", True, f"{'on' if repeat_on else 'off'}, delay {delay * 1000:.0f} ms, interval {interval * 1000:.0f} ms")


def run_selftest(cfg, seconds: float, language: str) -> int:
    rec = new_recorder(lambda kind, value=None: None)
    print(f"Recording for {seconds:.0f} s: speak now...", flush=True)
    t_start = time.monotonic()
    rec.start()
    time.sleep(seconds)
    audio = rec.stop()
    transcriber = Transcriber(cfg)
    ui = UiState(cfg)
    started = time.monotonic()
    transcriber.load(ui.device, ui.gpu_model, ui.cpu_model)
    load_time = time.monotonic() - started
    started = time.monotonic()
    if language == "auto":
        language = transcriber.detect(audio)[0]
    segments = transcriber.run(audio, language)
    text = clean_text(" ".join(s.text.strip() for s in segments), cfg, language)
    asr = time.monotonic() - started
    live = f"{(rec.t_first - t_start) * 1000:.0f} ms" if rec.t_first else "never"
    db = lambda x: f"{20 * np.log10(max(x, 1e-9)):.0f} dBFS"
    peak, rms = (float(np.abs(audio).max()), float(np.sqrt(np.mean(audio ** 2)))) if audio.size else (0.0, 0.0)
    print(f"model: {transcriber.desc} (loaded in {load_time:.1f} s); language: {language}")
    print(f"microphone live after {live}; {len(audio) / RATE:.1f} s recorded (peak {db(peak)}, rms {db(rms)}); "
          f"transcribed in {asr:.2f} s")
    print(f"text: {text!r}" if text else "problem: no speech heard")
    return 0 if text else 1


def run_paste_test(cfg, text: str) -> int:
    lock = lock_or_exit()  # noqa: F841
    selection = SelectionOwner()
    selection.start()
    keyboard = VirtualKeyboard()
    paster = Paster(cfg, selection, keyboard)
    for i in range(5, 0, -1):
        print(f"Pasting into the focused window in {i}...", flush=True)
        time.sleep(1)
    session = Session(0, None, "en", False, sleep_offset(), language="en", t_release=time.monotonic())
    paster.paste(PasteItem(clean_text(text, cfg), session, final=True))
    time.sleep(1.0)  # let mutter copy the restored clipboard before we exit
    keyboard.close()
    return 0


def run_portal_test(cfg, seconds: float) -> int:
    lock = lock_or_exit()  # noqa: F841
    events: queue.Queue = queue.Queue()
    PortalShortcut(cfg, lambda kind, value=None: events.put((kind, value, time.monotonic()))).start()
    print(f"Binding {cfg.trigger!r}; click Add if GNOME asks. Then press, hold and release the key "
          f"(listening for {seconds:.0f} s).", flush=True)
    end, last = time.monotonic() + seconds, None
    while time.monotonic() < end:
        try:
            kind, value, t = events.get(timeout=0.2)
        except queue.Empty:
            continue
        gap = f"+{(t - last) * 1000:.0f} ms" if last is not None else ""
        print(f"{kind:13} {gap:>10}  {value if kind in ('key_ready', 'not_approved') else ''}", flush=True)
        last = t
        if kind == "not_approved":
            return 3
    return 0


def ask(question: str, options: list[tuple[str, str]], default: str, env: str | None = None) -> str:
    """A multiple-choice question in the terminal; Enter takes the default (the recommendation).
    The environment variable `env`, or having no terminal, answers it without asking."""
    keys = [key for key, _ in options]
    preset = os.environ.get(env or "", "").strip()
    if preset in keys:
        print(f"{question} {preset} ({env})")
        return preset
    if preset:
        print(f"{env}={preset!r} is not one of: {', '.join(keys)}")
    if not sys.stdin or not sys.stdin.isatty():
        print(f"{question} {default}")
        return default
    print(f"\n{question}")
    for i, (key, label) in enumerate(options, 1):
        print(f"  {i}) {label}" + ("   <- recommended" if key == default else ""))
    while True:
        answer = input(f"Type 1-{len(options)} and Enter, or just Enter for {keys.index(default) + 1}: ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return keys[int(answer) - 1]
        if answer in keys:
            return answer
        print("Please type one of the numbers, or just press Enter.")


def ensure_config(cpu_only: bool) -> None:
    """Create config.toml from config.example.toml if there is none; keep an existing one."""
    if CONFIG_PATH.exists():
        print(f"Keeping your settings file {CONFIG_PATH}")
        return
    example = Path(__file__).resolve().parent / "config.example.toml"
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = example.read_text(encoding="utf-8") if example.exists() else ""
    if cpu_only:  # a CPU can't keep up with repeated live passes
        text += ("\n# No usable GPU found at install time: the model runs on the CPU and live typing "
                 "starts off.\nlive_typing = false\n")
    CONFIG_PATH.write_text(text, encoding="utf-8")
    print(f"Created the settings file {CONFIG_PATH}")


def previous_setup() -> bool:
    """Whether choices were saved before (an earlier --setup or the menu), so an update can keep them."""
    try:
        return "trigger" in json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False


def ask_key(hw) -> str:
    """The push-to-talk key: suggested for this keyboard, or any key or combination typed in."""
    if MACOS:  # MacBooks have no numpad; Left Option still types @ # { } on Slovak layouts
        options = [("Alt_R", "Right Option (⌥)"), ("Super_R", "Right Command (⌘)"),
                   ("KP_Delete", "numpad . (a keyboard with a numpad)")]
        default = "Alt_R"
    elif LINUX and os.environ.get("XDG_SESSION_TYPE") == "wayland":  # the desktop's dialog can change it
        options, default = [("KP_Delete", "numpad Del / .   (keyboards with a numpad)")], "KP_Delete"
    else:
        options = [("KP_Delete", "numpad Del / .   (keyboards with a numpad)"),
                   ("Control_R", "Right Ctrl        (laptops; it still works in shortcuts)")]
        default = "Control_R" if hw.laptop else "KP_Delete"
    options.append(("other", "another key or a combination, typed in (e.g. F13, Ctrl+Alt+D)"))
    question = "Which key do you hold to dictate?" + (" (On Wayland the desktop then shows its own dialog "
                                                      "to approve or change it.)" if LINUX else "")
    preset = os.environ.get("DICTATE_KEY", "").strip()
    if preset and valid_trigger(preset):
        print(f"{question} {keys.parse(preset)} (DICTATE_KEY)")
        return str(keys.parse(preset))
    choice = ask(question, options, default)
    if choice != "other":
        return choice
    while True:
        text = input("Type the key or combination (Enter for the suggestion): ").strip()
        if not text:
            return default
        try:
            return str(keys.parse(text))
        except ValueError as e:
            print(f"  {e}")


def run_setup(cfg, gpu: str) -> int:
    """The installers' shared part: questions with recommendations for this computer, the model
    downloads, the settings file and the menu choices. Ends by loading the model once."""
    if gpu == "auto":  # what the installed GPU libraries say
        gpu = {"cuda": "nvidia", "rocm": "amd"}.get(preload_gpu_libraries()[0], "none")
    hw = models.probe(None if gpu == "none" else gpu)
    print(f"This computer: {hw.describe()}")
    ui = UiState(cfg)
    if previous_setup():
        current = (f"language {LANGUAGES[ui.language]}, model {ui.gpu_model if gpu != 'none' else ui.cpu_model}, "
                   f"key {keys.label(keys.parse(ui.trigger), sys.platform)}")
        if ask(f"Your current choices: {current}.", [("keep", "keep them"), ("change", "choose again")],
               "keep", "DICTATE_KEEP") == "keep" and not any(os.environ.get(v) for v in
                                                             ("DICTATE_LANGUAGE", "DICTATE_MODEL", "DICTATE_KEY")):
            ensure_config(cpu_only=gpu == "none")
            return finish_setup(ui.gpu_model if gpu != "none" else None, ui.cpu_model)
    language = ask("Which language will you dictate?", [("en", "English"), ("sk", "Slovak (Slovenčina)"),
                   ("auto", "Both: English or Slovak, detected each time")], "en", "DICTATE_LANGUAGE")
    device, gpu_model, cpu_model = models.recommend(hw, language)
    where = "on the graphics card" if device == "gpu" else "on the processor (no usable graphics card)"
    options = [(name, f"{name:<15}{models.size_label(name):>8}   {info}") for name, (_, _, info) in models.MODELS.items()]
    chosen = ask(f"Which speech model? It runs {where}; bigger models are more accurate but slower.",
                 options, gpu_model or cpu_model, "DICTATE_MODEL")
    if device == "gpu":
        gpu_model = chosen
    else:
        cpu_model = chosen
    trigger = ask_key(hw)
    large = ask("Large text, for low vision? (Size and colours can be changed later in the settings window.)",
                [("off", "no"), ("panel", "a big status panel while dictating"),
                 ("both", "the big panel, and a big settings window when you click the tray icon")], "off",
                "DICTATE_LARGE_UI")
    ensure_config(cpu_only=device == "cpu")
    ui = UiState(load_config())
    ui.set(language=language, cpu_model=cpu_model, trigger=trigger, big_panel=large != "off",
           big_settings=large == "both", **({"gpu_model": gpu_model} if gpu_model else {}))
    return finish_setup(gpu_model, cpu_model)


def finish_setup(gpu_model: str | None, cpu_model: str) -> int:
    for name in dict.fromkeys(m for m in (gpu_model, cpu_model) if m):
        if models.installed(MODELS_DIR, name):
            print(f"The {name} model is already downloaded")
        elif name in models.MODELS:
            print(f"Downloading the {name} model ({models.size_label(name)})…", flush=True)
            models.download(MODELS_DIR, name)
    print("\nLoading the model once to check it:", flush=True)
    return subprocess.run([sys.executable, str(Path(__file__).resolve()), "--check-model"]).returncode


def run_download(names: list[str]) -> int:
    for name in names:
        if name not in models.MODELS:
            print(f"Unknown model {name!r}; available: {', '.join(models.MODELS)}")
            return 2
        if models.installed(MODELS_DIR, name):
            print(f"{name}: already downloaded")
            continue
        print(f"Downloading {name} ({models.size_label(name)})…", flush=True)
        models.download(MODELS_DIR, name)
    return 0


def run_check_model(cfg) -> int:
    """Load the chosen model the way dictation does and time one pass of a short dictation's size."""
    ui = UiState(cfg)
    tr = Transcriber(cfg)
    started = time.monotonic()
    try:
        tr.load(ui.device, ui.gpu_model, ui.cpu_model)
    except Exception as e:
        print(f"FAIL model: {e}")
        return 1
    loaded = time.monotonic() - started
    noise = np.random.default_rng(1).standard_normal(5 * RATE).astype(np.float32) * 0.01
    started = time.monotonic()
    list(tr.model.transcribe(noise, language="en", vad_filter=False, without_timestamps=True, max_new_tokens=24,
                             beam_size=cfg.cpu_beam_size if tr.on_cpu else cfg.beam_size)[0])
    print(f"ok   model: {tr.desc}, loaded in {loaded:.1f} s; a pass for a short dictation takes about "
          f"{time.monotonic() - started:.1f} s")
    if tr.on_cpu and ui.device != "cpu" and tr.gpu_available:
        print("note the GPU could not be used, so the model runs on the CPU (the log has the reason)")
    return 0


def open_log():
    """dictate.log for appending (UTF-8, line by line); the previous one is kept once it passes 5 MB."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        if LOG_PATH.stat().st_size > 5_000_000:
            LOG_PATH.replace(LOG_PATH.with_name(LOG_PATH.name + ".1"))
    except OSError:
        pass
    return open(LOG_PATH, "a", encoding="utf-8", buffering=1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Hold numpad Del to dictate (push-to-talk speech-to-text).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="check GPU, models, microphone tools, X11, portal")
    mode.add_argument("--selftest", type=float, metavar="SECONDS", help="record and transcribe, print timings")
    mode.add_argument("--paste-test", metavar="TEXT", help="paste TEXT into the focused window after 5 s")
    mode.add_argument("--portal-test", type=float, nargs="?", const=30, metavar="SECONDS",
                      help="bind the key and print press/release events")
    mode.add_argument("--setup", action="store_true", help="choose language and model, download it (installers)")
    mode.add_argument("--download", nargs="+", metavar="MODEL", help=f"download models: {', '.join(models.MODELS)}")
    mode.add_argument("--check-model", action="store_true", help="load the chosen model and time one pass")
    parser.add_argument("--gpu", choices=("auto", "nvidia", "amd", "none"), default="auto",
                        help="the GPU the installer found, for --setup")
    parser.add_argument("--language", choices=sorted(LANGUAGES), default="en", help="language for --selftest")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging (includes transcripts)")
    args = parser.parse_args()

    if sys.stderr is None:  # pythonw.exe on Windows has no console: log to a file
        sys.stdout = sys.stderr = open_log()
    if args.setup or args.download:  # set before anything imports huggingface_hub
        os.environ["HF_HUB_OFFLINE"] = "0"
        os.environ["HF_HUB_VERBOSITY"] = "error"  # not "unauthenticated requests" warnings
    under_systemd = "INVOCATION_ID" in os.environ
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s" if under_systemd
                        else "%(asctime)s.%(msecs)03d %(levelname)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("faster_whisper", "httpx", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.ERROR if noisy == "huggingface_hub" else logging.WARNING)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    cfg = load_config()
    global APP_ID
    APP_ID = cfg.app_id
    if args.check:
        return run_check(cfg)
    if args.selftest is not None:
        return run_selftest(cfg, args.selftest, args.language)
    if args.paste_test is not None:
        return run_paste_test(cfg, args.paste_test)
    if args.portal_test is not None:
        return run_portal_test(cfg, args.portal_test)
    if args.setup:
        return run_setup(cfg, args.gpu)
    if args.download:
        return run_download(args.download)
    if args.check_model:
        return run_check_model(cfg)
    return run_daemon(cfg)


if __name__ == "__main__":
    sys.exit(main())

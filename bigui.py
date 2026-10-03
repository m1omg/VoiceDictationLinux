"""Large-text mode for dictate (for low vision): a big status panel while you dictate, and a big
settings window. Both are Tk windows whose text Pillow draws, at any size, smooth and in a
high-contrast colour scheme (the private Python's Tk on Linux has no smooth fonts of its own).
Each runs as its own process:

    python bigui.py panel      started by dictate; reads one JSON object per line on stdin
    python bigui.py settings   the settings window; it changes state.json, which dictate watches

All timing uses timers (Tk's after), never screen frames.
"""
from __future__ import annotations

import base64
import functools
import io
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
WINDOWS, MACOS = sys.platform == "win32", sys.platform == "darwin"
SCHEMES = {  # name: (text, background, accent for the recording dot and problems)
    "yellow-on-black": ("#ffff00", "#000000", "#ff6060"),
    "white-on-black": ("#ffffff", "#000000", "#ff6060"),
    "black-on-white": ("#000000", "#ffffff", "#c00000"),
    "black-on-yellow": ("#000000", "#ffff00", "#b00000"),
}
SCHEME_LABELS = {"yellow-on-black": "Yellow on black", "white-on-black": "White on black",
                 "black-on-white": "Black on white", "black-on-yellow": "Black on yellow"}
SCALES = (1.0, 1.5, 2.0, 2.5, 3.0)
BASE_PX = 22  # text height at size 1x (the panel uses 1.25 times this)


def dpi_aware() -> None:
    """Windows: draw at the screen's real resolution instead of being scaled up blurry."""
    if WINDOWS:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def font_file() -> str | None:
    """A bold system font that has every Slovak letter."""
    if WINDOWS:
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        candidates = [fonts / "segoeuib.ttf", fonts / "arialbd.ttf"]
    elif MACOS:
        candidates = [Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"), Path("/Library/Fonts/Arial Bold.ttf"),
                      Path("/System/Library/Fonts/Helvetica.ttc")]
    else:
        candidates = []
        if shutil.which("fc-match"):
            out = subprocess.run(["fc-match", "-f", "%{file}", "sans-serif:bold"], capture_output=True, text=True)
            candidates.append(Path(out.stdout.strip()))
        candidates += [Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
                       Path("/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf"),
                       Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf")]
    return next((str(p) for p in candidates if p.is_file()), None)


@functools.cache
def font(px: int):
    path = font_file()
    try:
        return ImageFont.truetype(path, px) if path else ImageFont.load_default(size=px)
    except OSError:
        return ImageFont.load_default(size=px)


def wrap(text: str, f, width: int) -> list[str]:
    """Split text into lines no wider than width; a word longer than a line (a web address) is cut."""
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and f.getlength(candidate) > width:
            lines.append(line)
            candidate = word
        while f.getlength(candidate) > width and len(candidate) > 1:  # one huge word
            cut = len(candidate)
            while cut > 1 and f.getlength(candidate[:cut]) > width:
                cut -= 1
            lines.append(candidate[:cut])
            candidate = candidate[cut:]
        line = candidate
    if line:
        lines.append(line)
    return lines


def photo(image: Image.Image) -> tk.PhotoImage:
    buf = io.BytesIO()
    image.save(buf, "PNG", compress_level=1)
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()))


def draw_mark(draw: ImageDraw.ImageDraw, mark: str, x: int, y: int, size: int, colour: str, bg: str) -> None:
    """A choice's mark as a shape, so it shows in every font (many lack ● ○ ☑ ☐ ▸ ✓): "radio:on" a
    filled ring, "radio:off" an empty one, "check:on" a ticked box, "check:off" an empty box,
    "dot" a filled circle, "tick" a tick, "" a pointer (a plain button)."""
    w = max(2, size // 8)
    box = (x, y, x + size, y + size)
    if mark.startswith("radio"):
        draw.ellipse(box, outline=colour, width=w)
        if mark.endswith(":on"):
            inset = size // 4
            draw.ellipse((x + inset, y + inset, x + size - inset, y + size - inset), fill=colour)
    elif mark.startswith("check"):
        draw.rectangle(box, outline=colour, width=w)
        if mark.endswith(":on"):
            draw_mark(draw, "tick", x + size // 6, y + size // 6, size * 2 // 3, colour, bg)
    elif mark == "dot":
        draw.ellipse(box, fill=colour)
    elif mark == "tick":
        draw.line([(x, y + size * 0.55), (x + size * 0.38, y + size * 0.9), (x + size, y + size * 0.1)],
                  fill=colour, width=max(2, size // 5), joint="curve")
    else:
        draw.polygon([(x + size * 0.2, y + size * 0.1), (x + size * 0.9, y + size / 2), (x + size * 0.2, y + size * 0.9)],
                     fill=colour)


def text_image(lines: list[tuple[str, str]], width: int, px: int, bg: str, pad: int,
               mark: str | None = None) -> Image.Image:
    """Lines of (text, colour) drawn left-aligned on a width x (as needed) image; with a mark, it is
    drawn before the first line (and every line is indented past it)."""
    f = font(px)
    step = int(px * 1.3)
    image = Image.new("RGB", (width, pad * 2 + step * max(1, len(lines))), bg)
    draw = ImageDraw.Draw(image)
    indent = mark_width(px) if mark is not None else 0
    for i, (text, colour) in enumerate(lines):
        draw.text((pad + indent, pad + i * step), text, font=f, fill=colour)
    if mark is not None and lines:
        size = int(px * 0.75)
        ascent = f.getbbox("Hx")
        draw_mark(draw, mark, pad, pad + (ascent[1] + ascent[3]) // 2 - size // 2, size, lines[0][1], bg)
    return image


def framed(image: Image.Image, border: int, colour: str) -> Image.Image:
    """The image inside a frame border pixels wide."""
    out = Image.new("RGB", (image.width + 2 * border, image.height + 2 * border), colour)
    out.paste(image, (border, border))
    return out


def mark_width(px: int) -> int:
    """The room a mark takes before the text."""
    return int(px * 1.2)


_monitor: tuple[float, tuple[int, int, int, int]] | None = None


def primary_monitor(root: tk.Tk) -> tuple[int, int, int, int]:
    """x, y, width, height of the primary monitor's usable area (above the Windows taskbar and the
    macOS Dock), asked again every few seconds (a monitor can be plugged in or out)."""
    global _monitor
    now = time.monotonic()
    if _monitor is None or now - _monitor[0] > 5:
        _monitor = (now, _find_primary_monitor(root))
    return _monitor[1]


def _find_primary_monitor(root: tk.Tk) -> tuple[int, int, int, int]:
    if WINDOWS:
        try:
            import ctypes
            from ctypes import wintypes

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                            ("dwFlags", wintypes.DWORD)]
            user32 = ctypes.windll.user32
            user32.MonitorFromPoint.restype = ctypes.c_void_p
            user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
            user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
            info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
            user32.GetMonitorInfoW(user32.MonitorFromPoint(wintypes.POINT(0, 0), 1), ctypes.byref(info))
            r = info.rcWork
            return r.left, r.top, r.right - r.left, r.bottom - r.top
        except Exception:
            pass
    elif MACOS:
        try:
            from AppKit import NSScreen
            screen = NSScreen.screens()[0]  # the one with the menu bar
            full, usable = screen.frame(), screen.visibleFrame()  # (AppKit counts y from the bottom)
            top = full.size.height - (usable.origin.y + usable.size.height)
            return int(usable.origin.x), int(top), int(usable.size.width), int(usable.size.height)
        except Exception:
            pass
    else:
        try:
            from Xlib import display
            d = display.Display()
            monitors = d.screen().root.xrandr_get_monitors().monitors
            m = next((m for m in monitors if m.primary), monitors[0])
            d.close()
            return m.x, m.y, m.width_in_pixels, m.height_in_pixels
        except Exception:
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


# --- the status panel ------------------------------------------------------------------------
def panel_image(msg: dict, width: int, px: int) -> Image.Image:
    """The panel's picture for a message: the title (with its mark), then the last three lines of
    what was heard, in a frame. width and px (the text height) are in pixels."""
    fg, bg, accent = SCHEMES.get(msg.get("colors"), SCHEMES["yellow-on-black"])
    pad = px // 2
    f = font(px)
    room = width - 2 * pad - (mark_width(px) if msg.get("mark") else 0)
    lines = [(line, accent if msg.get("accent") else fg) for line in wrap(msg.get("title", ""), f, room)]
    words = wrap(msg.get("text", ""), f, room)
    if len(words) > 3:  # the last three lines: what was just said
        words = ["…" + words[-3]] + words[-2:]
    lines += [(line, fg) for line in words]
    return framed(text_image(lines, width, px, bg, pad, msg.get("mark")), max(2, px // 8), fg)


class Panel:
    """A large always-on-top strip that never takes the keyboard focus (Linux and Windows; macOS:
    MacPanel). Messages, one JSON object per line:
    {"show": true, "title": "Listening — EN", "mark": "dot", "text": "words heard so far", "accent": true,
     "hide_after": 3, "scale": 2.0, "colors": "yellow-on-black", "position": "bottom"} or {"show": false}."""

    def __init__(self):
        dpi_aware()
        self.root = root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        self.label = tk.Label(root, bd=0, highlightthickness=0)
        self.label.pack()
        self.image = None
        self.mapped = False
        self.hide_job = None
        self.messages: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        if WINDOWS:  # mapped once, now and off-screen, then only moved: a Tk window activates when shown
            root.geometry("1x1+-32000+-32000")
            windows_no_activate(root)
            self.mapped = True
        root.after(50, self._poll)

    def _read(self):
        for line in sys.stdin:
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                pass
        self.messages.put(None)  # dictate has ended

    def _poll(self):
        try:
            while True:
                msg = self.messages.get_nowait()
                if msg is None:
                    self.root.destroy()
                    return
                try:
                    self.apply(msg)
                except Exception as e:  # one bad message must not stop the panel
                    print(f"panel: {e!r}", file=sys.stderr)
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def apply(self, msg: dict) -> None:
        if self.hide_job:
            self.root.after_cancel(self.hide_job)
            self.hide_job = None
        if not msg.get("show"):
            self.hide()
            return
        x0, y0, sw, sh = primary_monitor(self.root)
        px = int(BASE_PX * 1.25 * float(msg.get("scale", 2.0)) * self.root.winfo_fpixels("1i") / 96)
        framed = panel_image(msg, int(sw * 0.8), px)
        self.image = photo(framed)
        self.label.configure(image=self.image)
        x = x0 + (sw - framed.width) // 2
        y = y0 + (sh - framed.height - sh // 20 if msg.get("position", "bottom") == "bottom" else sh // 20)
        self.show(x, y, framed.width, framed.height)
        if msg.get("hide_after"):
            self.hide_job = self.root.after(int(float(msg["hide_after"]) * 1000), self.hide)

    def show(self, x: int, y: int, w: int, h: int) -> None:
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        if not self.mapped:
            self.mapped = True
            self.root.deiconify()  # override-redirect: the window manager never focuses it
            x11_click_through(self.root)
        if not WINDOWS:  # (there it is topmost already; raising it could activate it)
            self.root.lift()

    def hide(self) -> None:
        self.hide_job = None
        if WINDOWS:  # moved away: it is never shown again, which could activate it
            self.root.geometry("+-32000+-32000")
        elif self.mapped:
            self.root.withdraw()
            self.mapped = False


def windows_no_activate(root: tk.Tk) -> None:
    """Map the panel, give the focus back to the window that had it, and make the panel a topmost
    tool window that never takes the focus and lets clicks through, opaque."""
    import ctypes
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetLayeredWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_ubyte, ctypes.c_uint32]
    previous = user32.GetForegroundWindow()
    root.deiconify()
    root.update()
    hwnd = int(root.wm_frame(), 16)
    style = user32.GetWindowLongPtrW(hwnd, -20)  # GWL_EXSTYLE
    # WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_TRANSPARENT | WS_EX_LAYERED
    user32.SetWindowLongPtrW(hwnd, -20, style | 0x08000000 | 0x80 | 0x8 | 0x20 | 0x80000)
    user32.SetLayeredWindowAttributes(hwnd, 0, 255, 2)  # LWA_ALPHA 255: opaque (layered: clicks go through)
    if previous and user32.GetForegroundWindow() != previous:
        user32.SetForegroundWindow(previous)  # mapping it took the focus: give it back


class MacPanel:
    """macOS: the panel as an AppKit panel that never activates. (A Tk window brings its app to the
    front whenever it is shown, which takes the focus from the app being dictated into.) It is
    drawn with twice the pixels on a Retina screen, so the text stays sharp."""

    def __init__(self):
        import AppKit
        from PyObjCTools import AppHelper
        self.AppKit, self.AppHelper = AppKit, AppHelper
        self.app = AppKit.NSApplication.sharedApplication()
        self.app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)  # no Dock icon
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (10, 10)), AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered, False)
        panel.setLevel_(AppKit.NSStatusWindowLevel)  # above other windows and the Dock
        panel.setIgnoresMouseEvents_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                                     | AppKit.NSWindowCollectionBehaviorStationary
                                     | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)
        self.view = AppKit.NSImageView.alloc().initWithFrame_(((0, 0), (10, 10)))
        self.view.setImageScaling_(AppKit.NSImageScaleAxesIndependently)
        panel.setContentView_(self.view)
        self.panel = panel
        self.generation = 0  # which message a delayed hide belongs to
        threading.Thread(target=self._read, daemon=True).start()

    def run(self) -> None:
        self.app.run()

    def _read(self):
        for line in sys.stdin:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            self.AppHelper.callAfter(self._apply, msg)
        self.AppHelper.callAfter(self.app.terminate_, None)  # dictate has ended

    def _apply(self, msg: dict) -> None:
        try:
            self.apply(msg)
        except Exception as e:  # one bad message must not stop the panel
            print(f"panel: {e!r}", file=sys.stderr)

    def apply(self, msg: dict) -> None:
        AppKit = self.AppKit
        self.generation += 1
        if not msg.get("show"):
            self.panel.orderOut_(None)
            return
        screen = AppKit.NSScreen.screens()[0]  # the one with the menu bar
        usable, pixels = screen.visibleFrame(), screen.backingScaleFactor()  # (above the Dock)
        px = int(BASE_PX * 1.25 * float(msg.get("scale", 2.0)) * pixels)
        image = panel_image(msg, int(usable.size.width * 0.8 * pixels), px)
        buf = io.BytesIO()
        image.save(buf, "PNG", compress_level=1)
        data = buf.getvalue()
        picture = AppKit.NSImage.alloc().initWithData_(AppKit.NSData.dataWithBytes_length_(data, len(data)))
        w, h = image.width / pixels, image.height / pixels
        picture.setSize_((w, h))
        self.view.setImage_(picture)
        x = usable.origin.x + (usable.size.width - w) / 2
        gap = usable.size.height / 20
        y = (usable.origin.y + gap if msg.get("position", "bottom") == "bottom"
             else usable.origin.y + usable.size.height - h - gap)  # (AppKit counts y from the bottom)
        self.panel.setFrame_display_(((x, y), (w, h)), True)
        self.panel.orderFrontRegardless()  # shown without activating the app
        if msg.get("hide_after"):
            self.AppHelper.callLater(float(msg["hide_after"]), self._hide, self.generation)

    def _hide(self, generation: int) -> None:
        if generation == self.generation:  # nothing newer was shown meanwhile
            self.panel.orderOut_(None)


def x11_click_through(root: tk.Tk) -> None:
    """Clicks go through the panel to the window under it (an empty input shape)."""
    try:
        from Xlib import display
        from Xlib.ext import shape
        root.update_idletasks()
        d = display.Display()
        window = d.create_resource_object("window", int(root.wm_frame(), 16))
        window.shape_rectangles(shape.SO.Set, shape.SK.Input, 0, 0, 0, [])
        d.sync()
        d.close()
    except Exception:
        pass


# --- the settings window -----------------------------------------------------------------------
MODIFIER_OF = {"Control_L": "ctrl", "Control_R": "ctrl", "Alt_L": "alt", "Alt_R": "alt", "Shift_L": "shift",
               "Shift_R": "shift", "Super_L": "super", "Super_R": "super"}
SOLO_KEYS = {"Fn", "ISO_Level3_Shift"}  # held like modifiers, but they can be the key only on their own
# (fn with another key is how Mac keyboards type F1-F12 and forward Delete: that key counts.)
WINDOWS_EXTENDED = 0x40000  # in a Tk key event's state on Windows: an extended key (numpad Enter, not Enter)


def signature(rows: list[tuple]) -> list[tuple]:
    """What a list of rows shows (without the buttons' actions): rebuilt only when this changes."""
    return [row[:5] if row[0] == "button" else row for row in rows]


def sentence(text: str) -> str:
    return text if text[-1:] in (".", "…", "!", "?") else text + "."


class Settings:
    """Every choice of the tray menu as large buttons, driven fully by the keyboard: Tab or the
    arrow keys move, Space or Enter choose, Esc closes. The buttons are pictures with a thick
    frame around the focused one, so they look the same on every system."""

    def __init__(self):
        sys.path.insert(0, str(HERE))
        import dictate
        import keys
        import models
        self.d, self.keys, self.models = dictate, keys, models
        self.cfg = dictate.load_config()
        self.ui = dictate.UiState(self.cfg)
        self.state_mtime = self._mtime()
        self.capturing = False
        self.message = ""  # at the top (e.g. "Dictation is stopping")
        self.key_message = ""  # in the key section (the capture prompt and its outcome)
        self.focus_id = None  # the button to focus after the next rebuild
        self.last_focus_id = None  # the button that had the focus last
        dpi_aware()
        self.root = root = tk.Tk()
        root.title("Dictate settings")
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.bind("<Escape>", self._escape)
        root.bind("<KeyPress>", self._key, add=True)
        root.bind("<KeyRelease>", self._key_up, add=True)
        self.canvas = tk.Canvas(root, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.frame = tk.Frame(self.canvas, bd=0)
        self.canvas.create_window(0, 0, window=self.frame, anchor="nw")
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            root.bind_all(sequence, self._wheel)
        self.images: list = []
        self.buttons: list[tk.Label] = []
        self.shown: list = []  # signature() of the rows on screen
        self.down: dict[int, str | None] = {}  # during a key capture: the keys held down (code: name)
        self.lone: str | None = None  # a modifier pressed on its own: the key, if it is released so
        self.focus_target = None
        self.build()
        root.after(1000, self._watch)
        root.lift()
        # Take the keyboard focus once the window is up, onto the first choice (a focus on the window
        # itself would leave the arrow keys and Space with nothing to act on).
        root.after(200, lambda: (self.focus_target or root).focus_force())

    # --- reading and writing the choices ---
    def _mtime(self) -> float:
        try:
            return self.d.STATE_PATH.stat().st_mtime
        except OSError:
            return 0.0

    def status(self) -> dict:
        """What the running dictation reports (in dictate.STATUS_PATH), or {} if it isn't running."""
        try:
            import psutil
            status = json.loads(self.d.STATUS_PATH.read_text(encoding="utf-8"))
            return status if psutil.pid_exists(status["pid"]) else {}
        except (OSError, ValueError, KeyError, TypeError, ImportError):
            return {}

    def set(self, **changes) -> None:
        self.ui.set(**changes)
        self.state_mtime = self._mtime()
        self.build()

    def _watch(self):
        """Show what changed elsewhere: a choice made in the tray menu, what dictation is doing, a
        finished download."""
        if not self.capturing:
            if self._mtime() != self.state_mtime:
                self.state_mtime = self._mtime()
                self.ui = self.d.UiState(self.cfg)
            if signature(self.rows()) != self.shown:
                self.build()
        self.root.after(1000, self._watch)

    # --- layout ---
    def rows(self) -> list[tuple]:
        """("heading" or "text", text), or ("button", id, label, mark, enabled, action[, colour scheme]):
        mark is "radio:on", "check:off", ... or "" for a plain button; the id stays the same across
        rebuilds, so the keyboard focus stays where it was."""
        ui, models, status = self.ui, self.models, self.status()
        on = lambda b: "on" if b else "off"  # noqa: E731
        gpu_ok = status.get("gpu_available") is not False
        on_gpu = ui.device != "cpu" and gpu_ok
        model_key = "gpu_model" if on_gpu else "cpu_model"
        current = getattr(ui, model_key)
        rows = [("heading", "Dictate settings")]
        if status:
            tip = (status.get("tip") or "").strip()
            rows.append(("text", "Dictation is running." + (f" {sentence(tip)}" if tip else "")
                         + (f" Model: {status['model']}." if status.get("model") else "")))
        else:
            rows.append(("text", "Dictation is not running."))
        if self.message:
            rows.append(("text", self.message))
        rows.append(("heading", "Language"))
        for code, name in self.d.LANGUAGES.items():
            rows.append(("button", f"lang:{code}", name, f"radio:{on(ui.language == code)}", True,
                         lambda c=code: self.set(language=c)))
        rows.append(("heading", "Typing"))
        rows.append(("button", "live", "Type while speaking", f"check:{on(ui.live)}", True,
                     lambda: self.set(live=not ui.live)))
        rows.append(("button", "sounds", "Sounds", f"check:{on(ui.sounds)}", True, lambda: self.set(sounds=not ui.sounds)))
        rows.append(("heading", f"Speech model (runs on the {'graphics card' if on_gpu else 'processor'})"))
        downloading = status.get("download") or ""
        for name, (_repo, _mb, info) in models.MODELS.items():
            if downloading.startswith(name + " "):
                note = f", downloading {downloading.split(' ', 1)[1]}"
            elif not models.installed(self.d.MODELS_DIR, name):
                note = f", download {models.size_label(name)}"
            else:
                note = ""
            rows.append(("button", f"model:{name}", f"{name}: {info}{note}", f"radio:{on(current == name)}", True,
                         lambda n=name: self.set(**{model_key: n})))
        rows.append(("heading", "Run on"))
        rows.append(("button", "device:gpu", "Graphics card (GPU)" + ("" if gpu_ok else ": none can be used"),
                     f"radio:{on(on_gpu)}", gpu_ok, lambda: self.set(device="gpu")))
        rows.append(("button", "device:cpu", "Processor (CPU)", f"radio:{on(not on_gpu)}", True,
                     lambda: self.set(device="cpu")))
        rows.append(("heading", "Dictation key"))
        if self.key_message:
            rows.append(("text", self.key_message))
        label = self.keys.label(self.keys.parse(ui.trigger), sys.platform)
        rows.append(("button", "key:change", f"Hold: {label}. Change it…", "", True, self.capture))
        if ui.trigger != "KP_Delete":
            rows.append(("button", "key:default", "Use numpad Del again", "", True, lambda: self.set_key("KP_Delete")))
        rows.append(("heading", "Large text"))
        rows.append(("button", "panel", "Big status panel while dictating", f"check:{on(ui.big_panel)}", True,
                     lambda: self.set(big_panel=not ui.big_panel)))
        if not MACOS:  # (the menu bar item always opens its menu there)
            rows.append(("button", "click", "Clicking the tray icon opens this window", f"check:{on(ui.big_settings)}",
                         True, lambda: self.set(big_settings=not ui.big_settings)))
        for where in ("bottom", "top"):
            rows.append(("button", f"pos:{where}", f"Panel at the {where} of the screen",
                         f"radio:{on(ui.panel_position == where)}", True, lambda w=where: self.set(panel_position=w)))
        for scale in SCALES:
            rows.append(("button", f"size:{scale:g}", f"Size {scale:g}×", f"radio:{on(abs(ui.ui_scale - scale) < 0.01)}",
                         True, lambda s=scale: self.set(ui_scale=s)))
        for scheme, name in SCHEME_LABELS.items():
            rows.append(("button", f"colours:{scheme}", name, f"radio:{on(ui.ui_colors == scheme)}", True,
                         lambda c=scheme: self.set(ui_colors=c), scheme))
        rows.append(("heading", ""))
        rows.append(("button", "open", "Open the settings file", "", True, lambda: self.d.open_text_file(self.d.CONFIG_PATH)))
        if status:
            rows.append(("button", "stop", "Stop dictation", "", True, self.stop))
        rows.append(("button", "close", "Close this window", "", True, self.root.destroy))
        return rows

    def build(self) -> None:
        focus_id = self.focus_id or self.last_focus_id
        self.focus_id = None
        for child in self.frame.winfo_children():
            child.destroy()
        self.images.clear()
        self.buttons.clear()
        fg, bg, accent = SCHEMES.get(self.ui.ui_colors, SCHEMES["yellow-on-black"])
        px = int(BASE_PX * self.ui.ui_scale * self.root.winfo_fpixels("1i") / 96)
        _x0, _y0, sw, sh = primary_monitor(self.root)
        width = int(min(sw * 0.9, px * 30))
        pad, ring = px // 3, max(3, px // 6)
        self.root.configure(bg=bg)
        self.canvas.configure(bg=bg)
        self.frame.configure(bg=bg)
        rows = self.rows()
        self.shown = signature(rows)
        for row in rows:
            if row[0] in ("heading", "text"):
                kind, text = row
                size = int(px * 1.2) if kind == "heading" else px
                lines = [(line, fg) for line in wrap(text, font(size), width - 2 * pad)] or [("", fg)]
                image = photo(text_image(lines, width, size, bg, pad // 2 if kind == "text" else pad))
                widget = tk.Label(self.frame, image=image, bd=0, bg=bg, highlightthickness=0)
                self.images.append(image)
            else:
                _, row_id, label, mark, enabled, action, *scheme = row
                own_fg, own_bg = SCHEMES[scheme[0]][:2] if scheme else (fg, bg)
                colour = own_fg if enabled else mix(own_fg, own_bg)  # greyed out
                inner = width - 2 * ring
                lines = wrap(label, font(px), inner - 2 * pad - mark_width(px))
                image = text_image([(line, colour) for line in lines], inner, px, own_bg, pad, mark)
                images = (photo(framed(image, ring, bg)), photo(framed(image, ring, accent)))  # (focused: framed)
                widget = tk.Label(self.frame, image=images[0], bd=0, bg=bg, highlightthickness=0, takefocus=1,
                                  cursor="hand2")
                widget.row_id, widget.images, widget.action = row_id, images, action if enabled else None
                widget.bind("<FocusIn>", self._focus_in)
                widget.bind("<FocusOut>", lambda e: e.widget.configure(image=e.widget.images[0]))
                widget.bind("<Button-1>", self._click)
                for key in ("<Return>", "<KP_Enter>", "<space>"):  # "break": the key stops here, so it
                    widget.bind(key, self._press)  # can't start a key capture
                widget.bind("<Down>", lambda e: self._move(e.widget, 1))
                widget.bind("<Up>", lambda e: self._move(e.widget, -1))
                self.buttons.append(widget)
                self.images.extend(images)
            widget.pack(anchor="w", padx=0, pady=max(1, ring // 2))
        self.root.update_idletasks()
        height = min(self.frame.winfo_reqheight(), int(sh * 0.9))
        self.canvas.configure(width=self.frame.winfo_reqwidth(), height=height,
                              scrollregion=(0, 0, self.frame.winfo_reqwidth(), self.frame.winfo_reqheight()))
        if self.capturing:  # keys go to the capture, not to a button (Space or Enter would press it)
            self.canvas.focus_set()
            self._scroll_to(None, next((b for b in self.buttons if b.row_id == "key:change"), None))
            return
        target = next((b for b in self.buttons if b.row_id == focus_id), self.buttons[0] if self.buttons else None)
        self.focus_target = target
        if target is not None:
            target.focus_set()

    def _focus_in(self, event) -> None:
        event.widget.configure(image=event.widget.images[1])
        self.last_focus_id = event.widget.row_id
        self._scroll_to(event)

    def _click(self, event) -> None:
        event.widget.focus_set()
        self._press(event)

    @staticmethod
    def _press(event) -> str:
        if event.widget.action is not None:
            event.widget.action()
        return "break"

    def _move(self, widget, step: int) -> str:
        """Up and Down move to the button above or below (not round from the last to the first)."""
        i = self.buttons.index(widget) + step if widget in self.buttons else -1
        if 0 <= i < len(self.buttons):
            self.buttons[i].focus_set()
        return "break"

    def _scroll_to(self, event, widget=None) -> None:
        """Keep the focused button (and the message just above it) in view."""
        widget = widget or (event.widget if event is not None else None)
        if widget is None:
            return
        total = max(1, self.frame.winfo_reqheight())
        top, bottom = widget.winfo_y(), widget.winfo_y() + widget.winfo_height()
        view_top, view_bottom = (f * total for f in self.canvas.yview())
        if top < view_top:  # scrolling up: leave a third of the view above it (its heading, a message)
            self.canvas.yview_moveto(max(0.0, top - (view_bottom - view_top) / 3) / total)
        elif bottom > view_bottom:
            self.canvas.yview_moveto((bottom - (view_bottom - view_top)) / total)

    def _wheel(self, event) -> None:
        delta = -1 if getattr(event, "num", 0) == 4 or getattr(event, "delta", 0) > 0 else 1
        self.canvas.yview_scroll(delta * 3, "units")

    # --- actions ---
    def stop(self) -> None:
        try:  # the systemd service (else it would start dictation again): stopped through systemd
            service = not WINDOWS and not MACOS and "dictate.service" in subprocess.run(
                ["systemctl", "--user", "list-units", "--state=active", "dictate.service"],
                capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):  # no systemd (Devuan, Void, Alpine)
            service = False
        if service:
            subprocess.Popen(["systemctl", "--user", "stop", "dictate.service"])
        else:
            self.d.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            self.d.COMMAND_PATH.write_text("quit\n", encoding="utf-8")
        self.message = "Dictation is stopping."
        self.root.after(1500, self.build)

    def capture(self) -> None:
        self.capturing, self.down, self.lone = True, {}, None
        self.key_message = "Press the new dictation key, or a key combination like Ctrl+Alt+D, now. Esc cancels."
        self.build()

    def set_key(self, trigger: str) -> None:
        self.capturing = False
        wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland" and not WINDOWS and not MACOS
        label = self.keys.label(self.keys.parse(trigger), sys.platform)
        self.key_message = (f"New key: {label}. Dictation restarts to use it"
                            + (", and your desktop asks you to approve it." if wayland else "."))
        self.focus_id = "key:change"
        self.set(trigger=trigger)

    def key_name(self, event) -> str | None:
        """The key of a Tk key event as a key name of keys.py. The key's code is used where a held
        modifier changes the character (AltGr on Windows, Option on macOS) or Tk names the key
        differently (the numpad on Windows, fn and Command on macOS)."""
        if MACOS:
            return self.keys.from_mac_keycode(event.keycode >> 24)  # (Tk keeps the Mac keycode in the top byte)
        if WINDOWS:
            name = self.keys.from_windows_vk(event.keycode, bool(event.state & WINDOWS_EXTENDED))
            if name:
                return name
        return self.keys.from_tk(event.keysym)

    def _key(self, event) -> str | None:
        if not self.capturing:
            return None
        code = event.keycode >> 24 if MACOS else event.keycode
        if code in self.down:  # still held: a repeat (Windows repeats held modifiers too)
            return "break"
        name = self.down[code] = self.key_name(event)
        if name in MODIFIER_OF or name in SOLO_KEYS:  # it is the key itself if it is released on its own
            self.lone = name if len(self.down) == 1 else None
            return "break"
        self.lone = None
        held = list(self.down.values())
        try:
            if "ISO_Level3_Shift" in held:  # (it changes the key's character: đ, not d)
                raise ValueError("AltGr can be the dictation key only on its own")
            if name is None:
                raise ValueError(f"{event.keysym} can't be the dictation key")
            trigger = self.keys.parse(str(self.keys.Trigger(frozenset(MODIFIER_OF[n] for n in held if n in MODIFIER_OF),
                                                            name)))
        except ValueError as e:
            self.key_message = f"{sentence(str(e)[0].upper() + str(e)[1:])} Try another key or combination; Esc cancels."
            self.build()
            return "break"
        self.set_key(str(trigger))
        return "break"

    def _key_up(self, event) -> str | None:
        if not self.capturing:
            return None
        self.down.pop(event.keycode >> 24 if MACOS else event.keycode, None)
        if self.lone is not None and not self.down:  # a modifier pressed and released on its own
            name, self.lone = self.lone, None
            try:
                self.set_key(str(self.keys.parse(name)))
            except ValueError as e:
                self.key_message = f"{sentence(str(e)[0].upper() + str(e)[1:])} Try another key; Esc cancels."
                self.build()
        return "break"

    def _escape(self, _event) -> None:
        if self.capturing:
            self.capturing, self.key_message, self.focus_id = False, "The key was not changed.", "key:change"
            self.build()
        else:
            self.root.destroy()


def mix(a: str, b: str) -> str:
    """Halfway between two #rrggbb colours (greyed-out text)."""
    ca, cb = (tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in (a, b))
    return "#" + "".join(f"{(x + y) // 2:02x}" for x, y in zip(ca, cb))


def main() -> int:
    what = sys.argv[1] if len(sys.argv) > 1 else "settings"
    if what == "panel":
        if MACOS:
            MacPanel().run()
        else:
            Panel().root.mainloop()
    elif what == "settings":
        settings = Settings()
        if MACOS:
            settings.root.after(100, settings.root.focus_force)
        settings.root.mainloop()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The tray icon on Windows and macOS (pystray), with the interface of tray.TrayIcon: set(icon,
label, tooltip), refresh_menu(), set_activate(), start(), and the same MenuItem trees.

The icon is drawn with Pillow: a ring, filled while recording, with the language code inside (the
Windows tray and the macOS menu bar show no text label). macOS draws menu-bar icons as templates
(shape only), so the state shows in the shape as well as in the colour.
"""
from __future__ import annotations

import logging
import sys
import threading

import pystray
from PIL import Image, ImageChops, ImageDraw, ImageFont

from tray import MenuItem, walk  # noqa: F401 (MenuItem: the same items as on Linux)

log = logging.getLogger("dictate")
MACOS = sys.platform == "darwin"
COLOURS = {"ready": "#2b6cb0", "recording": "#d00000", "working": "#c27c00", "problem": "#606060"}


def draw_icon(state: str, code: str, size: int = 64) -> Image.Image:
    """state: ready, recording, working or problem (the names in dictate's TopBar.ICONS)."""
    colour = "#000000" if MACOS else COLOURS.get(state, COLOURS["ready"])
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    w = max(3, size // 12)
    box = (w // 2, w // 2, size - 1 - w // 2, size - 1 - w // 2)
    if state == "recording":
        d.ellipse(box, fill=colour)
    else:
        d.ellipse(box, outline=colour, width=w)
    if state == "working":
        d.pieslice(box, 270, 90, fill=colour)
    if state == "problem":
        d.line((size * 0.2, size * 0.8, size * 0.8, size * 0.2), fill=colour, width=w)
    text = code[:2]
    try:
        font = ImageFont.load_default(size=int(size * 0.42))
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    left, top, right, bottom = d.textbbox((0, 0), text, font=font)
    xy = ((size - (right - left)) / 2 - left, (size - (bottom - top)) / 2 - top)
    if state == "recording":  # the letters cut out of the filled disk
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).text(xy, text, font=font, fill=255)
        image.putalpha(ImageChops.subtract(image.getchannel("A"), mask))
    elif state != "problem":
        d.text(xy, text, font=font, fill=colour)
    return image


class TrayIcon:
    needs_main_thread = MACOS  # AppKit: the menu bar item belongs to the main thread

    def __init__(self, item_id: str, title: str, build_menu, on_click, on_activate=None):
        self.title, self.build_menu, self.on_click, self.on_activate = title, build_menu, on_click, on_activate
        self.state, self.code, self.tooltip = "ready", "EN", title
        if MACOS:  # a menu bar item only: no Dock icon and no menu bar of our own
            import AppKit
            AppKit.NSApplication.sharedApplication().setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self.icon = pystray.Icon(item_id, draw_icon("ready", "EN"), title, menu=self._menu())

    # --- called from any thread ---
    def set(self, icon=None, label=None, tooltip=None) -> None:
        state = {"audio-input-microphone-symbolic": "ready", "media-record-symbolic": "recording",
                 "content-loading-symbolic": "working", "microphone-disabled-symbolic": "problem"}.get(icon, self.state)
        code = (label or self.code).replace("●", "").strip().split()[0] if (label or self.code).strip() else self.code
        code = code.split("·")[-1]  # "AUTO·SK" -> "SK"
        changed_icon = (state, code) != (self.state, self.code)
        self.state, self.code = state, code
        if tooltip is not None:
            self.tooltip = tooltip
        self._later(lambda: self._apply(changed_icon))

    def refresh_menu(self) -> None:
        self._later(lambda: setattr(self.icon, "menu", self._menu()))

    def set_activate(self, on_activate) -> None:
        self.on_activate = on_activate
        self.refresh_menu()

    def notify(self, summary: str, body: str = "") -> None:
        """A notification (Windows: a balloon from the tray icon)."""
        try:
            self.icon.notify(body or summary, summary)
        except Exception as e:
            log.debug("notification failed: %s", e)

    def start(self) -> None:
        if not self.needs_main_thread:
            threading.Thread(target=self.icon.run, name="tray", daemon=True).start()

    def run(self) -> None:
        """macOS: run the menu bar item on the main thread until stop()."""
        self.icon.run()

    def stop(self) -> None:
        self._later(self.icon.stop)  # macOS: AppKit's loop is stopped from its own (main) thread

    # --- the icon's own thread ---
    def _later(self, fn) -> None:
        if MACOS:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(fn)
        else:
            fn()

    def _apply(self, changed_icon: bool) -> None:
        try:
            if changed_icon:
                self.icon.icon = draw_icon(self.state, self.code)
            self.icon.title = self.tooltip[:127]  # Windows' limit
        except Exception as e:
            log.debug("tray update failed: %s", e)

    def _menu(self) -> pystray.Menu:
        items = self._convert(self.build_menu())
        if self.on_activate is not None:  # a left click opens the settings window (Windows)
            items.insert(0, pystray.MenuItem("Settings window", lambda: self.on_activate(), default=True, visible=False))
        return pystray.Menu(*items)

    def _convert(self, items) -> list:
        out = []
        for item in items:
            if item.kind == "separator":
                out.append(pystray.Menu.SEPARATOR)
            elif item.children:
                out.append(pystray.MenuItem(item.label, pystray.Menu(*self._convert(item.children)), enabled=item.enabled))
            else:
                checked = (lambda c: (lambda _item: c))(item.checked) if item.kind in ("radio", "check") else None
                out.append(pystray.MenuItem(item.label, self._action(item.id), checked=checked,
                                            radio=item.kind == "radio", enabled=item.enabled))
        return out

    def _action(self, item_id: int):
        def act(_icon, _item):
            try:
                self.on_click(item_id)
            except Exception:
                log.exception("tray: menu action %s failed", item_id)
        return act

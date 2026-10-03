"""A small top-bar icon (StatusNotifierItem + DBusMenu) served with jeepney.

GNOME shows it through the AppIndicator extension. It covers what dictate needs: a themed icon,
a short text label next to it, a tooltip, and a menu of plain, radio and checkbox items with one
level of submenus. All D-Bus traffic runs on this thread; other threads call set() and refresh_menu().
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field

from jeepney import (DBusAddress, HeaderFields, MatchRule, MessageType, new_error, new_method_call,
                     new_method_return, new_signal)
from jeepney.bus_messages import message_bus
from jeepney.io.blocking import open_dbus_connection

log = logging.getLogger("dictate")

ITEM_PATH, MENU_PATH = "/StatusNotifierItem", "/MenuBar"
ITEM_IFACE, MENU_IFACE = "org.kde.StatusNotifierItem", "com.canonical.dbusmenu"
WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER = DBusAddress("/StatusNotifierWatcher", WATCHER_NAME, WATCHER_NAME)

_ITEM_XML = """<node><interface name="org.kde.StatusNotifierItem">
<property name="Category" type="s" access="read"/><property name="Id" type="s" access="read"/>
<property name="Title" type="s" access="read"/><property name="Status" type="s" access="read"/>
<property name="WindowId" type="i" access="read"/><property name="IconName" type="s" access="read"/>
<property name="IconPixmap" type="a(iiay)" access="read"/><property name="OverlayIconName" type="s" access="read"/>
<property name="AttentionIconName" type="s" access="read"/><property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
<property name="ItemIsMenu" type="b" access="read"/><property name="Menu" type="o" access="read"/>
<property name="IconThemePath" type="s" access="read"/><property name="XAyatanaLabel" type="s" access="read"/>
<property name="XAyatanaLabelGuide" type="s" access="read"/>
<method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
<signal name="NewIcon"/><signal name="NewToolTip"/><signal name="NewStatus"><arg type="s"/></signal>
<signal name="XAyatanaNewLabel"><arg type="s"/><arg type="s"/></signal>
</interface></node>"""

_MENU_XML = """<node><interface name="com.canonical.dbusmenu">
<property name="Version" type="u" access="read"/><property name="TextDirection" type="s" access="read"/>
<property name="Status" type="s" access="read"/><property name="IconThemePath" type="as" access="read"/>
<method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/>
<arg type="as" direction="in"/><arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method>
<method name="GetGroupProperties"><arg type="ai" direction="in"/><arg type="as" direction="in"/>
<arg type="a(ia{sv})" direction="out"/></method>
<method name="GetProperty"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
<method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/>
<arg type="u" direction="in"/></method>
<method name="EventGroup"><arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/></method>
<method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
<method name="AboutToShowGroup"><arg type="ai" direction="in"/><arg type="ai" direction="out"/>
<arg type="ai" direction="out"/></method>
<signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
<signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})"/><arg type="a(ias)"/></signal>
</interface></node>"""


@dataclass
class MenuItem:
    id: int  # unique in the whole menu, and stable
    label: str = ""
    kind: str = "normal"  # normal | separator | radio | check
    checked: bool = False
    enabled: bool = True
    children: list = field(default_factory=list)  # a submenu

    def props(self) -> dict:
        if self.kind == "separator":
            return {"type": ("s", "separator")}
        props = {"label": ("s", self.label.replace("_", "__")),  # a single _ marks a mnemonic
                 "enabled": ("b", self.enabled), "visible": ("b", True)}
        if self.kind in ("radio", "check"):
            props["toggle-type"] = ("s", "radio" if self.kind == "radio" else "checkmark")
            props["toggle-state"] = ("i", int(self.checked))
        if self.children:  # KDE learns that an item has a submenu only from this
            props["children-display"] = ("s", "submenu")
        return props


def walk(items):
    """Every item, submenus included."""
    for item in items:
        yield item
        yield from walk(item.children)


class _Reregister(Exception):
    """Leave the bus and register anew (hosts read ItemIsMenu only when an item appears)."""


class TrayIcon(threading.Thread):
    def __init__(self, item_id: str, title: str, build_menu, on_click, on_activate=None):
        super().__init__(name="tray", daemon=True)
        self.item_id, self.title = item_id, title
        self.build_menu, self.on_click = build_menu, on_click  # build_menu() -> list[MenuItem]
        self.on_activate = on_activate  # a primary click opens this instead of the menu (when set)
        self.icon, self.label, self.tooltip = "audio-input-microphone-symbolic", "", ""
        self.updates: queue.Queue = queue.Queue()
        self.revision = 1

    # --- called from other threads ---------------------------------------------------
    def set(self, icon=None, label=None, tooltip=None) -> None:
        self.updates.put((icon, label, tooltip))

    def refresh_menu(self) -> None:
        self.updates.put(None)

    def set_activate(self, on_activate) -> None:
        """Switch between "a click opens the menu" and "a click calls on_activate"."""
        self.on_activate = on_activate
        self.updates.put("reregister")

    # --- tray thread -------------------------------------------------------------------
    def run(self):
        delay = 1.0
        while True:
            started = time.monotonic()
            try:
                self._serve()
            except _Reregister:
                time.sleep(0.2)
                continue
            except Exception as e:
                log.warning("tray: %s", e)
            delay = 1.0 if time.monotonic() - started > 60 else min(delay * 2, 30)
            time.sleep(delay)

    @staticmethod
    def _call(conn, msg):
        reply = conn.send_and_get_reply(msg, timeout=10)
        if reply.header.message_type == MessageType.error:
            raise RuntimeError(f"{reply.header.fields.get(HeaderFields.error_name)}: {reply.body}")
        return reply.body

    def _serve(self):
        with open_dbus_connection("SESSION") as conn:
            self.registrations = getattr(self, "registrations", 0) + 1
            name = f"org.kde.StatusNotifierItem-{os.getpid()}-{self.registrations}"
            self._call(conn, message_bus.RequestName(name, 4))  # 4 = don't queue
            rule = MatchRule(type="signal", sender="org.freedesktop.DBus", interface="org.freedesktop.DBus",
                             member="NameOwnerChanged", path="/org/freedesktop/DBus")
            rule.add_arg_condition(0, WATCHER_NAME)
            self._call(conn, message_bus.AddMatch(rule))
            # From here on only send()/receive(): send_and_get_reply() would drop incoming calls.
            register = new_method_call(WATCHER, "RegisterStatusNotifierItem", "s", (name,))
            conn.send(register)
            while True:
                self._apply_updates(conn)
                try:
                    msg = conn.receive(timeout=0.1)
                except TimeoutError:
                    continue
                kind = msg.header.message_type
                if kind == MessageType.method_call:
                    self._dispatch(conn, msg)
                elif kind == MessageType.signal and msg.body[0] == WATCHER_NAME and msg.body[2]:
                    conn.send(register)  # the AppIndicator extension (re)started
                elif kind == MessageType.error:
                    log.warning("tray: %s: %s", msg.header.fields.get(HeaderFields.error_name), msg.body)

    def _apply_updates(self, conn):
        item = DBusAddress(ITEM_PATH, interface=ITEM_IFACE)
        while True:
            try:
                update = self.updates.get_nowait()
            except queue.Empty:
                return
            if update == "reregister":
                raise _Reregister()
            if update is None:
                self.revision += 1
                conn.send(new_signal(DBusAddress(MENU_PATH, interface=MENU_IFACE), "LayoutUpdated", "ui",
                                     (self.revision, 0)))
                continue
            icon, label, tooltip = update
            if icon is not None and icon != self.icon:
                self.icon = icon
                conn.send(new_signal(item, "NewIcon"))
            if label is not None and label != self.label:
                self.label = label
                conn.send(new_signal(item, "XAyatanaNewLabel", "ss", (label, label)))
            if tooltip is not None and tooltip != self.tooltip:
                self.tooltip = tooltip
                conn.send(new_signal(item, "NewToolTip"))

    def _dispatch(self, conn, msg):
        fields = msg.header.fields
        path, iface, member = fields.get(HeaderFields.path), fields.get(HeaderFields.interface), fields.get(HeaderFields.member)
        try:
            reply = self._handle(path, iface, member, msg)
        except Exception as e:
            log.warning("tray: %s.%s failed: %r", iface, member, e)
            reply = new_error(msg, "org.freedesktop.DBus.Error.Failed", "s", (str(e),))
        if reply is None:
            reply = new_error(msg, "org.freedesktop.DBus.Error.UnknownMethod", "s", (f"{iface}.{member}",))
        conn.send(reply)

    def _handle(self, path, iface, member, msg):
        if iface == "org.freedesktop.DBus.Introspectable" and member == "Introspect":
            xml = {ITEM_PATH: _ITEM_XML, MENU_PATH: _MENU_XML}.get(path, "<node/>")
            return new_method_return(msg, "s", (xml,))
        if iface == "org.freedesktop.DBus.Peer" and member == "Ping":
            return new_method_return(msg)
        if iface == "org.freedesktop.DBus.Properties" and member in ("Get", "GetAll"):
            props = self._item_props() if path == ITEM_PATH else self._menu_props() if path == MENU_PATH else {}
            if member == "GetAll":
                return new_method_return(msg, "a{sv}", (props,))
            if msg.body[1] in props:
                return new_method_return(msg, "v", (props[msg.body[1]],))
            return new_error(msg, "org.freedesktop.DBus.Error.UnknownProperty", "s", (msg.body[1],))
        if path == ITEM_PATH and member == "Activate" and self.on_activate:
            self._clicked(None)
            return new_method_return(msg)
        if path == ITEM_PATH and member in ("Activate", "SecondaryActivate", "ContextMenu", "Scroll",
                                            "ProvideXdgActivationToken"):
            return new_method_return(msg)
        if path != MENU_PATH:
            return None
        items = self.build_menu()
        if member == "GetLayout":
            parent, depth = msg.body[0], msg.body[1]

            def node(item, depth):  # depth -1: everything below
                children = [] if depth == 0 else [("(ia{sv}av)", node(c, depth - 1)) for c in item.children]
                return item.id, item.props(), children
            if parent == 0:
                layout = (0, {"children-display": ("s", "submenu")},
                          [] if depth == 0 else [("(ia{sv}av)", node(i, depth - 1)) for i in items])
            else:
                item = next((i for i in walk(items) if i.id == parent), None)
                layout = node(item, depth) if item else (parent, {}, [])
            return new_method_return(msg, "u(ia{sv}av)", (self.revision, layout))
        if member == "GetGroupProperties":
            ids = set(msg.body[0])
            return new_method_return(msg, "a(ia{sv})", ([(i.id, i.props()) for i in walk(items)
                                                         if not ids or i.id in ids],))
        if member == "GetProperty":
            item = next((i for i in walk(items) if i.id == msg.body[0]), None)
            return new_method_return(msg, "v", ((item.props() if item else {}).get(msg.body[1], ("s", "")),))
        if member == "Event":
            if msg.body[1] == "clicked":
                self._clicked(msg.body[0])
            return new_method_return(msg)
        if member == "EventGroup":
            for item_id, event_id, _data, _timestamp in msg.body[0]:
                if event_id == "clicked":
                    self._clicked(item_id)
            return new_method_return(msg, "ai", ([],))
        if member == "AboutToShow":
            return new_method_return(msg, "b", (False,))
        if member == "AboutToShowGroup":
            return new_method_return(msg, "aiai", ([], []))
        return None

    def _clicked(self, item_id: int | None) -> None:
        """A menu item was clicked (None: the icon itself, when on_activate is set)."""
        try:
            if item_id is None:
                self.on_activate()
            elif not any(i.id == item_id and i.children for i in walk(self.build_menu())):  # not a submenu
                self.on_click(item_id)
        except Exception:
            log.exception("tray: menu action %s failed", item_id)

    def _item_props(self) -> dict:
        return {
            "Category": ("s", "ApplicationStatus"), "Id": ("s", self.item_id), "Title": ("s", self.title),
            "Status": ("s", "Active"), "WindowId": ("i", 0), "IconName": ("s", self.icon),
            "IconPixmap": ("a(iiay)", []), "OverlayIconName": ("s", ""), "AttentionIconName": ("s", ""),
            "ToolTip": ("(sa(iiay)ss)", ("", [], self.title, self.tooltip)),
            "ItemIsMenu": ("b", self.on_activate is None), "Menu": ("o", MENU_PATH), "IconThemePath": ("s", ""),
            "XAyatanaLabel": ("s", self.label), "XAyatanaLabelGuide": ("s", self.label),
        }

    @staticmethod
    def _menu_props() -> dict:
        return {"Version": ("u", 3), "TextDirection": ("s", "ltr"), "Status": ("s", "normal"),
                "IconThemePath": ("as", [])}

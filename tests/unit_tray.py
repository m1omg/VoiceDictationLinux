"""The top-bar menu over D-Bus, as GNOME's AppIndicator extension and KDE read it: the nested layout
(GetLayout with recursion depth -1 and 1), properties of submenu items, and clicks. Also checks
dictate's real menu: unique ids, the Main model and Run on submenus.

Run it on a private bus, so no icon appears in your top bar:
    dbus-run-session -- ~/.local/share/dictate/venv/bin/python tests/unit_tray.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("DICTATE_DIR", str(ROOT))
os.environ.update(XDG_STATE_HOME=tempfile.mkdtemp(), XDG_CONFIG_HOME=tempfile.mkdtemp())
from _common import load_dictate  # noqa: E402
from jeepney import DBusAddress, new_method_call  # noqa: E402
from jeepney.bus_messages import message_bus  # noqa: E402
from jeepney.io.blocking import open_dbus_connection  # noqa: E402

conn = open_dbus_connection("SESSION")


def call(address, method, signature=None, body=()):
    reply = conn.send_and_get_reply(new_method_call(address, method, signature, body), timeout=5)
    return reply.body


if call(message_bus, "NameHasOwner", "s", ("org.kde.StatusNotifierWatcher",))[0]:
    sys.exit("A tray host is running on this bus: run the test with dbus-run-session (see the top of the file).")

d = load_dictate()
sys.path.insert(0, str(d.Path(d.__file__).parent))
from tray import MenuItem, TrayIcon, walk  # noqa: E402

clicks = []
M = MenuItem
menu = [M(1, "Header_with_underscore", enabled=False), M(2, kind="separator"),
        M(40, "Main model: base", children=[M(400, "tiny", "radio"), M(401, "base", "radio", True)]),
        M(41, "Run on", children=[M(410, "GPU", "radio", enabled=False), M(411, "CPU", "radio", True)]),
        M(31, "Stop")]
tray = TrayIcon("dictate-test", "Dictate test", lambda: menu, clicks.append)
tray.start()
name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
deadline = time.monotonic() + 5
while not call(message_bus, "NameHasOwner", "s", (name,))[0] and time.monotonic() < deadline:
    time.sleep(0.05)
dbusmenu = DBusAddress("/MenuBar", name, "com.canonical.dbusmenu")
checks = []

_rev, (root_id, root_props, children) = call(dbusmenu, "GetLayout", "iias", (0, -1, []))
top = [c[1] for c in children]
checks.append(("root has the five items", [t[0] for t in top] == [1, 2, 40, 41, 31]))
model = next(t for t in top if t[0] == 40)
checks.append(("submenu marked as one", model[1].get("children-display") == ("s", "submenu")))
checks.append(("submenu children in the full layout", [c[1][0] for c in model[2]] == [400, 401]))
checks.append(("underscore escaped", top[0][1]["label"] == ("s", "Header__with__underscore")))
_rev, (pid, props, kids) = call(dbusmenu, "GetLayout", "iias", (41, 1, []))
checks.append(("GetLayout of a submenu (depth 1)", pid == 41 and [k[1][0] for k in kids] == [410, 411]))
_rev, (_, _, shallow) = call(dbusmenu, "GetLayout", "iias", (0, 1, []))
checks.append(("depth 1 leaves out grandchildren", all(c[1][2] == [] for c in shallow)))
(groups,) = call(dbusmenu, "GetGroupProperties", "aias", ([410, 401], []))
by_id = dict(groups)
checks.append(("properties of submenu items", by_id[410]["enabled"] == ("b", False)
               and by_id[401]["toggle-state"] == ("i", 1)))
call(dbusmenu, "Event", "isvu", (401, "clicked", ("s", ""), 0))
call(dbusmenu, "Event", "isvu", (40, "clicked", ("s", ""), 0))  # a submenu itself: nothing to do
time.sleep(0.2)
checks.append(("clicks reach the program, submenus excluded", clicks == [401]))

ui = d.UiState(d.load_config())
topbar = d.TopBar(ui)
real = topbar.menu()
ids = [i.id for i in walk(real)]
checks.append(("dictate's menu: ids unique", len(ids) == len(set(ids))))
labels = {i.id: i.label for i in real}
checks.append(("dictate's menu: model and device submenus", labels[40].startswith("Main model") and labels[41].startswith("Run on")
               and len([i for i in next(i for i in real if i.id == 40).children if i.kind == "radio"]) == len(d.models.MODELS)))

for label, good in checks:
    print(f"{'ok  ' if good else 'FAIL'} {label}")
sys.exit(0 if all(good for _, good in checks) else 1)

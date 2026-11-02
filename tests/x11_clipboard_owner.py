"""For tests/x11_clipboard.py: a stand-in app that owns one selection with several formats until another
client takes it.  usage: x11_clipboard_owner.py <selection name> <target=file> ...  (prints "ready")"""
import sys

from Xlib import X, Xatom, display
from Xlib.protocol import event

d = display.Display()
sel = d.intern_atom(sys.argv[1])
offer = {}
for spec in sys.argv[2:]:
    name, path = spec.split("=", 1)
    atom = d.intern_atom(name)
    offer[atom] = open(path, "rb").read()
TARGETS, TIMESTAMP = d.intern_atom("TARGETS"), d.intern_atom("TIMESTAMP")
win = d.screen().root.create_window(-10, -10, 1, 1, 0, X.CopyFromParent, event_mask=X.PropertyChangeMask)
win.change_property(Xatom.WM_NAME, Xatom.STRING, 8, b"x")
d.flush()
when = None
while when is None:
    e = d.next_event()
    if e.type == X.PropertyNotify:
        when = e.time
win.set_selection_owner(sel, when)
d.flush()
assert d.get_selection_owner(sel).id == win.id
print("ready", flush=True)
while True:
    e = d.next_event()
    if e.type == X.SelectionClear:
        break
    if e.type != X.SelectionRequest:
        continue
    prop, ok = e.property or e.target, True
    if e.target == TARGETS:
        e.requestor.change_property(prop, Xatom.ATOM, 32, [TARGETS, TIMESTAMP] + list(offer))
    elif e.target == TIMESTAMP:
        e.requestor.change_property(prop, Xatom.INTEGER, 32, [when])
    elif e.target in offer:
        kind = Xatom.STRING if d.get_atom_name(e.target) == "STRING" else e.target
        e.requestor.change_property(prop, kind, 8, offer[e.target])
    else:
        ok = False
    e.requestor.send_event(event.SelectionNotify(time=e.time, requestor=e.requestor, selection=e.selection,
                                                 target=e.target, property=prop if ok else X.NONE))
    d.flush()

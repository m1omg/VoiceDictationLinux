"""The clipboard kept around a dictation, on the real X server: SelectionOwner's snapshot of every format
(a 3 MB image, formatted text, copied files) and its restore, sent in parts where it is big (INCR). It
works on SECONDARY and a private selection standing in for CLIPBOARD and PRIMARY, so your own clipboard
and selection are never touched. Needs X11 (or XWayland) and xclip.

    ~/.local/share/dictate/venv/bin/python tests/x11_clipboard.py
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
work = Path(tempfile.mkdtemp(prefix="dictate-clip-"))
sys.path.insert(0, str(HERE.parent))
import dictate as d  # noqa: E402

PY = sys.executable
checks = []


def check(name, got, want):
    checks.append((name, got == want))
    print(f"{'ok  ' if got == want else 'FAIL'} {name}" + ("" if got == want else f": got {got!r:.200}, want {want!r:.200}"),
          flush=True)


class TestOwner(d.SelectionOwner):
    def _connect(self):
        super()._connect()
        self.CLIPBOARD = self.d.intern_atom("SECONDARY")
        self.PRIMARY = self.d.intern_atom("DICTATE_TEST_PRIMARY")
        self.offer = {self.CLIPBOARD: {}, self.PRIMARY: {}}
        self.data_requests = {self.CLIPBOARD: 0, self.PRIMARY: 0}


started = []  # stand-in apps, ended at the end


def owner(selection, files: dict):
    """Start the stand-in app owning `selection` with these formats ({name: bytes})."""
    args = []
    for i, (name, data) in enumerate(files.items()):
        path = work / f"fmt{i}"
        path.write_bytes(data)
        args.append(f"{name}={path}")
    p = subprocess.Popen([PY, "-I", str(HERE / "x11_clipboard_owner.py"), selection, *args], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "ready"
    started.append(p)
    return p


def xclip_out(target=None):
    cmd = ["xclip", "-selection", "secondary", "-o"] + (["-t", target] if target else [])
    return subprocess.run(cmd, capture_output=True, timeout=10).stdout


so = TestOwner(bridged=False)
so.start()
assert so.connected.wait(5)
reader = TestOwner(bridged=False)  # a second client, to read the private PRIMARY stand-in
reader.start()
assert reader.connected.wait(5)


def read_primary():
    item = reader.call(lambda: reader._fetch(reader.PRIMARY, reader.UTF8, time.monotonic() + 1.0))
    return bytes(item[2]) if item else None


# 1. a big image (xclip sends it in parts, INCR), taken back in parts too
image = os.urandom(3 * 1024 * 1024 + 123)
(work / "img").write_bytes(image)
x = subprocess.Popen(["xclip", "-selection", "secondary", "-t", "image/png", "-i", str(work / "img")],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(0.5)
began = time.monotonic()
snap = so.snapshot()
took = time.monotonic() - began
names = {so._atom_name(a): len(v[2]) for a, v in snap[so.CLIPBOARD].items()}
check("image: snapshot keeps image/png, all of it", names, {"image/png": len(image)})
print(f"     (snapshot of 3 MB: {took * 1000:.0f} ms)")
check("image: dictated text goes on the selection", so.publish("diktát č. 1"), True)
check("... and is what a paste gets", xclip_out().decode(), "diktát č. 1")
x.wait(timeout=5)
put_back = so.restore(snap)
check("image: restored", [(n, f) for n, f, _ in put_back], [("SECONDARY", 1)])
check("... TARGETS lists image/png", b"image/png" in xclip_out("TARGETS"), True)
began = time.monotonic()
got = xclip_out("image/png")
check("... the image comes back byte for byte (sent in parts)", got == image, True)
print(f"     (paste of 3 MB: {(time.monotonic() - began) * 1000:.0f} ms)")

# 2. formatted text, copied files and plain text in several formats
html, files_ = "<b>Ahoj</b> čšž".encode(), b"copy\nfile:///home/x/a%20b.txt"
p = owner("SECONDARY", {"UTF8_STRING": "Ahoj čšž ťúä".encode(), "text/html": html,
                       "x-special/gnome-copied-files": files_, "STRING": "Ahoj".encode("latin-1")})
snap = so.snapshot()
got = {so._atom_name(a): bytes(v[2]) for a, v in snap[so.CLIPBOARD].items()}
check("formats: all four kept", sorted(got), sorted(["UTF8_STRING", "text/html", "x-special/gnome-copied-files", "STRING"]))
so.publish("nový diktát")
p.wait(timeout=5)
so.restore(snap)
check("... UTF8_STRING back", xclip_out("UTF8_STRING").decode(), "Ahoj čšž ťúä")
check("... text/html back", xclip_out("text/html"), html)
check("... copied files back", xclip_out("x-special/gnome-copied-files"), files_)
check("... STRING back", xclip_out("STRING"), b"Ahoj")

# 3. the user copies something while the dictation is typed: theirs stays
snap = so.snapshot()
so.publish("ďalší diktát")
x = subprocess.Popen(["xclip", "-selection", "secondary", "-i"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
x.communicate("skopírované medzitým".encode(), timeout=5)
time.sleep(0.3)
check("copied meanwhile: the clipboard not restored (the selection is, to what it was)",
      [n for n, _f, _s in so.restore(snap)], ["DICTATE_TEST_PRIMARY"])
check("... the new copy stays", xclip_out().decode(), "skopírované medzitým")
subprocess.run(["xclip", "-selection", "secondary", "-i", "/dev/null"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# 4. PRIMARY (middle-click) is kept and restored too
p = owner("DICTATE_TEST_PRIMARY", {"UTF8_STRING": "vybraný text".encode()})
q = owner("SECONDARY", {"UTF8_STRING": "v schránke".encode()})
snap = so.snapshot()
so.publish("diktát s výberom")
p.wait(timeout=5)
q.wait(timeout=5)
check("primary: the dictation replaced it", read_primary(), "diktát s výberom".encode())
put_back = so.restore(snap)
check("primary: both restored", sorted(n for n, _f, _s in put_back), ["DICTATE_TEST_PRIMARY", "SECONDARY"])
check("... the selection back", read_primary(), "vybraný text".encode())
check("... the clipboard back", xclip_out().decode(), "v schránke")

# 5. a snapshot of what we own already (the last restore) is that same content
snap2 = so.snapshot()
check("owned already: the snapshot is what we offer", {so._atom_name(a): bytes(v[2]) for a, v in snap2[so.CLIPBOARD].items()},
      {"UTF8_STRING": "v schránke".encode()})

for p in started:
    p.kill()
subprocess.run(["xclip", "-selection", "secondary", "-i", "/dev/null"], stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL)  # (xclip's own copy ends when the selection is taken)
so.call(lambda: (so.win.set_selection_owner(so.CLIPBOARD, so.X.CurrentTime), so.d.flush()))
print("passed" if all(ok for _n, ok in checks) else "FAILED", flush=True)
sys.exit(0 if all(ok for _n, ok in checks) else 1)

"""On Windows: the clipboard with delayed rendering and the paste keys, end to end. A small window
with a text field takes the focus, the text goes on the clipboard, Shift+Insert is sent, and the
field must contain the text, while the clipboard learns the paste happened. Your earlier clipboard
text is put back. It opens a window and takes the focus: run it when you are not typing.

    %LOCALAPPDATA%\\dictate\\venv\\Scripts\\python.exe tests\\windows_paste.py

Reference (GitHub's windows-latest runner): every check ok.
"""
import ctypes
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import windows as w  # noqa: E402

TEXT = "Dobrý deň, ľudia: čšťžýáíé ôä — dictated."
checks = []
clipboard = w.Clipboard()
clipboard.start()
earlier = clipboard.read_clipboard()

# The pasting app, as another program would read it: through the clipboard API.
ok = clipboard.publish(TEXT)
checks.append(("text offered (delayed rendering)", ok))
base = clipboard.counts()
got = {}


def reader():
    u, k = w.user32(), w.kernel32()
    for _ in range(50):
        if u.OpenClipboard(None):
            break
        time.sleep(0.02)
    handle = u.GetClipboardData(w.CF_UNICODETEXT)
    got["text"] = ctypes.wstring_at(k.GlobalLock(handle)) if handle else None
    k.GlobalUnlock(handle)
    u.CloseClipboard()


threading.Thread(target=reader).start()
checks.append(("a reader's request is noticed", clipboard.wait_served(base, 3)))
time.sleep(0.2)
checks.append(("the reader gets the text, accents intact", got.get("text") == TEXT))

# Shift+Insert into a focused text field.
root = tk.Tk()
root.geometry("600x80+200+200")
entry = tk.Entry(root, width=80)
entry.pack()
root.update()
root.focus_force()
entry.focus_set()
root.update()
time.sleep(0.5)
second = "Second text, typed with Shift+Insert."
clipboard.publish(second)
base = clipboard.counts()
w.Keyboard().shift_insert()
deadline = time.monotonic() + 3
while time.monotonic() < deadline and entry.get() != second:
    root.update()
    time.sleep(0.05)
checks.append(("Shift+Insert pasted into the focused field", entry.get() == second))
checks.append(("and the paste was noticed", clipboard.wait_served(base, 1)))
root.destroy()

if earlier:
    checks.append(("earlier clipboard put back", clipboard.restore_clipboard(earlier)))
for name, good in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}", flush=True)
sys.exit(0 if all(good for _, good in checks) else 1)

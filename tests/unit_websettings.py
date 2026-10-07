"""The settings page for screen readers (websettings.py), driven over HTTP like the page's own script
does, without a browser: only this computer, with the secret address and the right Host header, may
use it; choosing a radio button or a check box changes state.json; a new key is captured from the
browser's key codes; the page follows the interface language; and it ends once nobody uses it.
Settings go to a temporary folder; nothing opens on your screen.

    python3 tests/unit_websettings.py        (any Python 3.11+ with numpy and Pillow)
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="dictate-test-"))
os.environ.update(XDG_STATE_HOME=str(TMP / "state"), XDG_CONFIG_HOME=str(TMP / "config"), XDG_RUNTIME_DIR=str(TMP),
                  LOCALAPPDATA=str(TMP), HOME=str(TMP), USERPROFILE=str(TMP))
import dictate as d  # noqa: E402
import keys  # noqa: E402
import websettings  # noqa: E402

checks = []


def check(name, got, want):
    checks.append((name, got == want, got, want))


assert d.STATE_PATH.is_relative_to(TMP), d.STATE_PATH
server = websettings.Server(websettings.WebSettings())
threading.Thread(target=server.serve_forever, daemon=True).start()
port, token = server.server_address[1], server.token


def request(method, path, body=None, host=None, kind="application/json"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers.update({"Content-Type": kind, "Content-Length": str(len(data))})
    conn.request(method, path, body=data, headers=headers)
    reply = conn.getresponse()
    content = reply.read()
    conn.close()
    return reply.status, dict(reply.getheaders()), content


def state():
    return json.loads(d.STATE_PATH.read_text(encoding="utf-8")) if d.STATE_PATH.exists() else {}


def page_state():
    return json.loads(request("GET", f"/{token}/state")[2])


def choose(row_id):
    return json.loads(request("POST", f"/{token}/choose", {"id": row_id})[2])


# --- only this computer, with the secret address ---
status, headers, body = request("GET", f"/{token}/")
check("the page", (status, headers.get("Content-Type")), (200, "text/html; charset=utf-8"))
nonce = headers.get("Content-Security-Policy", "").split("'nonce-")[1].split("'")[0] if "nonce-" in headers.get(
    "Content-Security-Policy", "") else None
check("only its own script and style may run (a fresh nonce)", bool(nonce) and f'<script nonce="{nonce}">' in body.decode(),
      True)
check("nothing cached", headers.get("Cache-Control"), "no-store")
check("another site's name for 127.0.0.1 is refused (DNS rebinding)",
      request("GET", f"/{token}/state", host=f"evil.example:{port}")[0], 404)
check("the address without the secret is refused", request("GET", "/state")[0], 404)
check("a wrong secret is refused", request("GET", f"/{token[:-2]}xx/state")[0], 404)
before = state()
check("a change sent as a plain form (as another site could) is refused",
      request("POST", f"/{token}/choose", {"id": "lang:sk"}, kind="text/plain")[0], 404)
check("and changed nothing", state(), before)

# --- the rows: the same choices as the big settings window ---
s = page_state()
ids = [row[1] for row in s["rows"] if row[0] == "button"]
check("the same choices as the big window, without its 'open in the browser' button",
      ("lang:sk" in ids, "model:large-v3-turbo" in ids, "popup:auto" in ids, "login" in ids, "web" in ids),
      (True, True, True, True, False))
check("radio buttons and check boxes say which they are", {r[1]: r[3] for r in s["rows"] if r[0] == "button"
                                                            and r[1] in ("lang:en", "sounds")},
      {"lang:en": "radio:on", "sounds": "check:on"})
check("every radio group has a name for the screen reader",
      sorted({r[1].split(":")[0] for r in s["rows"] if r[0] == "button" and r[3].startswith("radio")} - set(s["groups"])),
      [])

# --- choosing ---
s = choose("lang:sk")
check("choosing Slovak saves it", (state().get("language"), [r[3] for r in s["rows"] if r[0] == "button"
                                                              and r[1] == "lang:sk"]), ("sk", ["radio:on"]))
choose("sounds")
check("a check box toggles", state().get("sounds"), False)
choose("live")
check("Type while speaking off", state().get("live"), False)
s = choose("instant")
check("a greyed-out choice can't be chosen", (state().get("instant", False),
                                             [r[4] for r in s["rows"] if r[0] == "button" and r[1] == "instant"]),
      (False, [False]))
choose("popup:large")
check("the big panel from the pop-up choices", state().get("big_panel"), True)
choose("close")
check("Close is the page's own business (nothing happens here)", page_state()["capturing"], False)

# --- a new key, from the browser's key codes ---
s = choose("key:change")
check("Change it…: waits for a key, and says so", (s["capturing"], "Press the new dictation key" in s["say"]),
      (True, True))
s = json.loads(request("POST", f"/{token}/key", {"code": "KeyD", "key": "d", "mods": ["ctrl", "alt"]})[2])
check("Ctrl+Alt+D", (state().get("trigger"), s["capturing"]), ("Ctrl+Alt+D", False))
choose("key:change")
request("POST", f"/{token}/key", {"code": "ControlRight", "key": "Control", "mods": []})
check("Right Ctrl on its own", state().get("trigger"), "Control_R")
choose("key:change")
s = json.loads(request("POST", f"/{token}/key", {"code": "KeyA", "key": "a", "mods": []})[2])
check("a letter alone is refused, with the reason, and it keeps waiting",
      (state().get("trigger"), s["capturing"], "could no longer be typed" in s["say"]), ("Control_R", True, True))
s = json.loads(request("POST", f"/{token}/cancel", {})[2])
check("Esc: the key stays", (s["capturing"], "The key was not changed." in s["say"], state().get("trigger")),
      (False, True, "Control_R"))
request("POST", f"/{token}/key", {"code": "KeyX", "key": "x", "mods": ["ctrl"]})
check("keys are only taken while it waits for one", state().get("trigger"), "Control_R")
check("browser key codes", [keys.from_browser(c, k) for c, k in [("NumpadDecimal", "Delete"), ("NumpadDecimal", ","),
                                                                    ("F13", "F13"), ("AltRight", "AltGraph"),
                                                                    ("Digit1", "!"), ("MetaRight", "Meta"),
                                                                    ("IntlBackslash", "<")]],
      ["KP_Delete", "KP_Delete", "F13", "ISO_Level3_Shift", "1", "Super_R", None])

# --- the interface language ---
s = choose("uilang:sk")
check("in Slovak: the page's language and title", (s["lang"], s["title"]), ("sk", "Nastavenia diktovania"))
check("and its rows", "Písanie" in [r[1] for r in s["rows"] if r[0] == "heading"], True)
s = choose("uilang:en")
check("back in English", (s["lang"], s["title"]), ("en", "Dictate settings"))

# --- a choice made elsewhere (the tray menu) shows on the page ---
time.sleep(0.05)
d.UiState(d.load_config()).set(language="auto")
check("a change from the tray shows on the next refresh",
      [r[3] for r in page_state()["rows"] if r[0] == "button" and r[1] == "lang:auto"], ["radio:on"])

# --- it ends by itself, and a second start reopens the page that is running ---
check("in use: it keeps running", server.idle(), False)
server.last_seen = time.monotonic() - websettings.IDLE_LIMIT - 1
check("30 s without the page asking: it ends", server.idle(), True)
(d.RUNTIME_DIR / "dictate-web.json").write_text(json.dumps({"pid": os.getpid(), "url": server.url}), encoding="utf-8")
check("a second start finds the page that is open", websettings.running_page(), server.url)
server.shutdown()
server.server_close()
check("and starts its own when that one has ended", websettings.running_page(), None)

for name, good, got, want in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}" + ("" if good else f": got {got!r}, want {want!r}"))
sys.exit(0 if all(c[1] for c in checks) else 1)

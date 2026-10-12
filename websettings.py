"""The settings as a page in the web browser, for screen readers (Orca, NVDA, Narrator, VoiceOver):
the same choices as the big settings window (bigui.SettingsModel.rows()), as real headings, radio
buttons, check boxes and buttons. A small web server on 127.0.0.1 serves it to this computer only,
under a random address, and ends about 30 s after the page was closed.

    python websettings.py      opens the page (again, if it is open already)
"""
from __future__ import annotations

import html
import http.server
import json
import os
import secrets
import socketserver
import sys
import threading
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bigui  # noqa: E402
import i18n  # noqa: E402
from i18n import t  # noqa: E402

IDLE_LIMIT = 30  # seconds without a request (the page asks every second while it is open): then it ends
FIRST_LIMIT = 120  # the browser never came


class WebSettings(bigui.SettingsModel):
    web = True


def state(model: WebSettings) -> dict:
    """What the page shows: the rows without their actions, and the texts the page itself needs."""
    model.reload()
    rows = [list(row[:5]) + ([row[6]] if len(row) > 6 else []) if row[0] == "button" else list(row)
            for row in model.rows()]
    ui = model.ui
    large = ui.big_settings or ui.big_panel  # someone who uses large text: the chosen size and colours
    fg, bg, accent = bigui.SCHEMES.get(ui.ui_colors, bigui.SCHEMES["yellow-on-black"])
    return {"rows": rows, "lang": i18n.language, "title": t("Dictate settings"), "capturing": model.capturing,
            "say": " ".join(m for m in (model.message, model.key_message) if m),
            "px": round(bigui.BASE_PX * ui.ui_scale) if large else 18, "colors": [fg, bg, accent] if large else None,
            "schemes": {name: list(colours[:2]) for name, colours in bigui.SCHEMES.items()},
            "groups": {"lang": t("Language you dictate in"), "uilang": "Menu language · Jazyk ponúk",
                       "model": t("Speech model"), "device": t("Run on"), "popup": t("Status pop-up while dictating"),
                       "pos": t("Where the pop-up shows"), "size": t("Text size"), "colours": t("Colours")},
            "texts": {"capture": t("Press the new dictation key, or a key combination like Ctrl+Alt+D, now. Esc cancels."),
                      "closed": t("This page has ended. Open the settings again from the Dictate menu."),
                      "close_tab": t("The browser keeps this tab open: close it with Ctrl+W (Cmd+W on a Mac).")}}


def browser_key(model: WebSettings, data: dict) -> None:
    """A key pressed on the page during a key capture: {"code": KeyboardEvent.code, "key": .key,
    "mods": ["ctrl", ...]}. The page sends a modifier only when it was pressed and released on its
    own (then it is the key itself, with no "mods")."""
    keys = model.keys
    name = keys.from_browser(str(data.get("code", "")), str(data.get("key", "")))
    mods = frozenset(m for m in data.get("mods", ()) if m in ("ctrl", "alt", "shift", "super"))
    try:
        if name is None:
            raise ValueError(t("{key} can't be the dictation key", key=data.get("key") or data.get("code") or "?"))
        trigger = keys.parse(str(keys.Trigger(mods, name)) if mods else name)
    except ValueError as e:
        model.key_problem(str(e))
        return
    model.set_key(str(trigger))


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def __init__(self, model: WebSettings):
        super().__init__(("127.0.0.1", 0), Handler)
        self.model, self.lock = model, threading.Lock()
        self.token = secrets.token_urlsafe(18)
        self.url = f"http://127.0.0.1:{self.server_address[1]}/{self.token}/"
        self.started, self.last_seen = time.monotonic(), None

    def idle(self) -> bool:
        now = time.monotonic()
        return now - self.started > FIRST_LIMIT if self.last_seen is None else now - self.last_seen > IDLE_LIMIT


class Handler(http.server.BaseHTTPRequestHandler):
    server: Server

    def log_message(self, *_args):  # (no log of every request)
        pass

    def _allowed(self) -> str | None:
        """The path after the secret, if this request may be answered: the right host (a web page
        elsewhere that renamed itself to 127.0.0.1 is refused) and the secret address."""
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_address[1]}":
            return None
        prefix = f"/{self.server.token}/"
        if not self.path.startswith(prefix):
            return None
        self.server.last_seen = time.monotonic()
        return self.path[len(prefix):]

    def _send(self, code: int, body: bytes, kind: str, nonce: str = "") -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'nonce-{nonce}'; "
                         f"style-src 'nonce-{nonce}'; connect-src 'self'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: dict) -> None:
        self._send(200, json.dumps(data).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        path = self._allowed()
        if path is None:
            return self._send(404, b"", "text/plain")
        with self.server.lock:
            current = state(self.server.model)
        if path == "":
            nonce = secrets.token_urlsafe(12)
            return self._send(200, page(current, nonce).encode(), "text/html; charset=utf-8", nonce)
        if path == "state":
            return self._json(current)
        return self._send(404, b"", "text/plain")

    def do_POST(self):
        path = self._allowed()
        # A page on another site can't send JSON here without asking first (which is never answered).
        if path is None or not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._send(404, b"", "text/plain")
        try:
            data = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 10000)) or b"{}")
        except ValueError:
            return self._send(400, b"", "text/plain")
        model = self.server.model
        with self.server.lock:
            model.reload()
            if path == "choose":
                row = next((r for r in model.rows() if r[0] == "button" and r[1] == data.get("id")), None)
                if row is not None and row[4] and row[1] != "close":
                    row[5]()
            elif path == "key" and model.capturing:
                browser_key(model, data)
            elif path == "cancel" and model.capturing:
                model.cancel_capture()
            current = state(model)
        self._json(current)


def page(current: dict, nonce: str) -> str:
    """The page: it draws the rows itself (the same code that later updates them in place, so the
    focus and the screen reader's place stay where they were)."""
    data = json.dumps(current).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="{current['lang']}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(current['title'])}</title>
<style nonce="{nonce}">
:root {{ color-scheme: light dark; }}
body {{ font: 18px/1.5 system-ui, sans-serif; margin: 0 auto; padding: 1em; max-width: 46em; }}
h1 {{ font-size: 1.6em; margin: 0 0 .5em; }}
h2 {{ font-size: 1.25em; margin: 1.4em 0 .4em; }}
.choice {{ display: flex; align-items: center; gap: .6em; padding: .25em 0; }}
input[type=radio], input[type=checkbox] {{ width: 1.3em; height: 1.3em; margin: 0; flex: none; }}
button {{ font: inherit; padding: .35em .8em; margin: .3em 0; display: block; }}
:focus-visible {{ outline: .2em solid currentColor; outline-offset: .15em; }}
.sample {{ padding: 0 .4em; border: 1px solid currentColor; }}
.hidden {{ position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }}
[aria-disabled=true], :disabled + label {{ opacity: .6; }}
</style>
</head>
<body>
<main id="main"><noscript>This page needs JavaScript.</noscript></main>
<div id="say" class="hidden" aria-live="polite"></div>
<script nonce="{nonce}">
"use strict";
let current = {data};
let shape = "";
const main = document.getElementById("main"), say = document.getElementById("say");

function el(tag, attrs, text) {{
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {{}})) if (v !== null && v !== false) node.setAttribute(k, v === true ? "" : v);
  if (text !== undefined) node.textContent = text;
  return node;
}}

function style(s) {{
  document.documentElement.lang = s.lang;
  document.title = s.title;
  document.body.style.fontSize = s.px + "px";
  document.body.style.color = s.colors ? s.colors[0] : "";
  document.body.style.background = s.colors ? s.colors[1] : "";
}}

function draw(s) {{
  const focused = document.activeElement && document.activeElement.id;
  main.replaceChildren();
  let heading = null, group = null, headings = 0, first = true;
  for (const row of s.rows) {{
    if (row[0] === "heading") {{
      group = null;
      if (!row[1]) {{ heading = null; main.append(el("hr")); continue; }}
      heading = "h" + (++headings);
      main.append(el(first ? "h1" : "h2", {{id: heading, "data-row": "text"}}, row[1]));
      first = false;
    }} else if (row[0] === "text") {{
      group = null;
      main.append(el("p", {{"data-row": "text"}}, row[1]));
    }} else {{
      const [, id, label, mark, enabled, scheme] = row;
      if (mark.startsWith("radio")) {{
        const name = id.split(":")[0];
        if (!group || group.dataset.name !== name) {{
          group = el("div", {{role: "radiogroup", "aria-label": s.groups[name] || null, "data-name": name}});
          main.append(group);
        }}
        const box = el("div", {{class: "choice"}});
        box.append(el("input", {{type: "radio", name, id, checked: mark.endsWith(":on"), disabled: !enabled}}));
        const text = el("label", {{for: id}}, label);
        if (scheme && s.schemes[scheme]) {{ text.classList.add("sample"); text.style.color = s.schemes[scheme][0]; text.style.background = s.schemes[scheme][1]; }}
        box.append(text);
        group.append(box);
      }} else if (mark.startsWith("check")) {{
        group = null;
        const box = el("div", {{class: "choice"}});
        box.append(el("input", {{type: "checkbox", id, checked: mark.endsWith(":on"), disabled: !enabled}}));
        box.append(el("label", {{for: id}}, label));
        main.append(box);
      }} else {{
        group = null;
        main.append(el("button", {{type: "button", id, disabled: !enabled}}, label));
      }}
    }}
  }}
  if (focused && document.getElementById(focused)) document.getElementById(focused).focus();
}}

function update(s) {{
  // Only what changed, in place: a page drawn anew would lose the screen reader's place.
  const texts = main.querySelectorAll("[data-row=text]");
  let i = 0;
  for (const row of s.rows) {{
    if (row[0] === "heading" && !row[1]) continue;
    if (row[0] !== "button") {{ if (texts[i] && texts[i].textContent !== row[1]) texts[i].textContent = row[1]; i++; continue; }}
    const [, id, label, mark, enabled] = row;
    const node = document.getElementById(id);
    if (!node) continue;
    const labelNode = node.tagName === "BUTTON" ? node : document.querySelector(`label[for="${{CSS.escape(id)}}"]`);
    if (labelNode && labelNode.textContent !== label) labelNode.textContent = label;
    if (node.tagName === "INPUT" && node.checked !== mark.endsWith(":on")) node.checked = mark.endsWith(":on");
    if (node.disabled === Boolean(enabled)) node.disabled = !enabled;
  }}
}}

let said = "";
function show(s) {{
  const next = JSON.stringify(s.rows.map(r => r[0] === "button" ? r[1] : r[0]));
  style(s);
  if (next !== shape) {{ shape = next; draw(s); }} else update(s);
  if (s.say && s.say !== said) say.textContent = s.say;
  said = s.say;
  current = s;
}}

async function send(path, body) {{
  try {{
    const reply = await fetch(path, {{method: path === "state" ? "GET" : "POST", cache: "no-store",
      headers: {{"Content-Type": "application/json"}}, body: path === "state" ? undefined : JSON.stringify(body || {{}})}});
    if (reply.ok) show(await reply.json());
  }} catch (e) {{
    say.textContent = current.texts.closed;
    clearInterval(timer);
  }}
}}

main.addEventListener("change", e => {{ if (e.target.id) send("choose", {{id: e.target.id}}); }});
main.addEventListener("click", e => {{
  const node = e.target.closest("button");
  if (!node) return;
  if (node.id === "close") {{
    window.close();
    setTimeout(() => {{ say.textContent = current.texts.close_tab; }}, 300);
  }} else send("choose", {{id: node.id}});
}});

const MODS = {{Control: "ctrl", Alt: "alt", Shift: "shift", Meta: "super", OS: "super"}};
let down = new Set(), lone = null;
document.addEventListener("keydown", e => {{
  if (!current.capturing) return;
  e.preventDefault();
  if (e.repeat) return;
  if (e.key === "Escape") {{ send("cancel"); return; }}
  down.add(e.code);
  const mods = [];
  if (e.ctrlKey) mods.push("ctrl"); if (e.altKey) mods.push("alt"); if (e.shiftKey) mods.push("shift"); if (e.metaKey) mods.push("super");
  if (MODS[e.key] || e.key === "AltGraph") {{ lone = down.size === 1 ? {{code: e.code, key: e.key}} : null; return; }}
  lone = null;
  send("key", {{code: e.code, key: e.key, mods}});
}}, true);
document.addEventListener("keyup", e => {{
  if (!current.capturing) return;
  e.preventDefault();
  down.delete(e.code);
  if (lone && lone.code === e.code && down.size === 0) {{ send("key", {{...lone, mods: []}}); lone = null; }}
}}, true);

show(current);
const timer = setInterval(() => send("state"), 1000);
</script>
</body>
</html>
"""


def running_page() -> str | None:
    """The address of a settings page that is open already (another copy of this program serves it)."""
    import urllib.request

    import dictate
    try:
        info = json.loads((dictate.RUNTIME_DIR / "dictate-web.json").read_text(encoding="utf-8"))
        with urllib.request.urlopen(info["url"] + "state", timeout=2) as reply:
            return info["url"] if reply.status == 200 else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def main() -> int:
    url = running_page()
    if url:
        webbrowser.open(url)
        return 0
    import dictate
    server = Server(WebSettings())
    info = dictate.RUNTIME_DIR / "dictate-web.json"
    try:
        info.parent.mkdir(parents=True, exist_ok=True)
        info.write_text(json.dumps({"pid": os.getpid(), "url": server.url}), encoding="utf-8")
        if os.name != "nt":
            info.chmod(0o600)
    except OSError:
        pass
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if not webbrowser.open(server.url):
        print(f"Open this address in a web browser: {server.url}", flush=True)
    while not server.idle():
        time.sleep(1)
    server.shutdown()
    try:
        if json.loads(info.read_text(encoding="utf-8")).get("pid") == os.getpid():
            info.unlink()
    except (OSError, ValueError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

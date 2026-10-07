"""Updates (update.py) against a stand-in for GitHub on 127.0.0.1: the check tells a current install
from an older one and says why it couldn't check; an update downloads the version, backs up the
program files, runs that version's installer (a stand-in here), waits until the new version runs,
and when anything fails puts the previous files back and starts them; the menu's two items and the
settings windows' buttons use it. Everything happens in a temporary folder: nothing is installed.

    python3 tests/unit_update.py        (any Python 3.11+ with numpy and Pillow)
"""
import http.server
import io
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import types
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="dictate-test-"))
os.environ.update(XDG_STATE_HOME=str(TMP / "state"), XDG_CONFIG_HOME=str(TMP / "config"), XDG_RUNTIME_DIR=str(TMP / "run"),
                  LOCALAPPDATA=str(TMP), HOME=str(TMP), USERPROFILE=str(TMP))

checks = []


def check(name, got, want):
    checks.append((name, got == want, got, want))


# --- a stand-in for GitHub: the API and the ZIP downloads ---
routes: dict[str, tuple[int, bytes]] = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        status, body = routes.get(self.path, (404, b"not found"))
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


github = Server(("127.0.0.1", 0), Handler)
threading.Thread(target=github.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{github.server_address[1]}"
os.environ.update(DICTATE_UPDATE_REPO="test/dictate", DICTATE_UPDATE_API=f"{BASE}/api",
                  DICTATE_UPDATE_CODELOAD=f"{BASE}/codeload")
import dictate as d  # noqa: E402
import update  # noqa: E402

REAL = {name: getattr(update, name) for name in ("run_installer", "wait_ready", "restart_dictation")}
update.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)  # (Windows and macOS keep it in the program's folder)
check("the same folders as dictate's", (update.APP_DIR, update.STATE_PATH, update.RUNTIME_DIR, update.STATUS_PATH),
      (d.APP_DIR, d.STATE_PATH, d.RUNTIME_DIR, d.STATUS_PATH))
check("git's name for a file's content", update.blob_sha(b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")

SHA, DATE = "a" * 40, "2026-10-08"
OLD = {"dictate.py": "print('old')\n", "keys.py": "KEYS = 1\n", "README.md": "old readme\n", "gone.py": "# removed later\n"}
NEW = {"dictate.py": "print('new')\n", "keys.py": "KEYS = 1\n", "README.md": "new readme\n", "extra.py": "# a new module\n"}
INSTALLERS = {"install.sh": 'printf "%s %s" "$DICTATE_KEEP" "$DICTATE_UI_LANGUAGE" > "$UPDATE_TEST_OUT"; exit 3\n',
              "install.ps1": 'Set-Content -NoNewline -Path $env:UPDATE_TEST_OUT -Value "$env:DICTATE_KEEP $env:DICTATE_UI_LANGUAGE"; exit 3\n'}


def version_zip(files: dict, folder: str = f"dictate-{SHA}") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name if name.startswith("../") else f"{folder}/{name}", text)
    return buf.getvalue()


def publish(sha=SHA, files=NEW):
    routes["/api/repos/test/dictate/commits/main"] = (200, json.dumps({
        "sha": sha, "commit": {"committer": {"date": f"{DATE}T09:00:00Z"}, "message": "New things.\n\nAnd details."}}).encode())
    routes[f"/api/repos/test/dictate/git/trees/{sha}"] = (200, json.dumps({"tree": [
        {"path": name, "type": "blob", "sha": update.blob_sha(text.encode())} for name, text in files.items()]
        + [{"path": "tests", "type": "tree", "sha": "0" * 40}]}).encode())
    routes[f"/codeload/test/dictate/zip/{sha}"] = (200, version_zip({**files, **INSTALLERS}))


def install(files: dict, where: Path = update.APP_DIR):
    where.mkdir(parents=True, exist_ok=True)
    for path in where.iterdir():
        if path.is_file() and update.is_program(path.name):
            path.unlink()
    for name, text in files.items():
        (where / name).write_text(text, encoding="utf-8", newline="")  # (as the ZIP has it: \n on Windows too)


def installed(where: Path = update.APP_DIR) -> dict:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(where.iterdir()) if p.is_file() and update.is_program(p.name)}


# --- the check ---
publish()
install(OLD)
result = update.check()
check("older program files: an update is available, with its date and description",
      (result.state, result.sha, result.date, result.summary), ("available", SHA, DATE, "New things."))
current = TMP / "current"
install(NEW, current)
check("the newest program files: up to date", update.check(current).state, "current")
(current / "local-extra.py").write_text("# only here\n")
check("a file GitHub doesn't have changes nothing", update.check(current).state, "current")
(current / "dictate.py").write_bytes(NEW["dictate.py"].replace("\n", "\r\n").encode())
check("Windows line endings (a git checkout with autocrlf) count as the same", update.check(current).state, "current")
(current / "dictate.py").write_bytes(b"print('changed')\r\n")
check("but other changes don't", update.check(current).state, "available")
routes["/api/repos/test/dictate/commits/main"] = (403, b"rate limit")
result = update.check()
check("GitHub's hourly limit is reported as such", (result.state, result.error), ("error", "limit"))
check("and explained", "few checks an hour" in update.problem(result), True)
routes["/api/repos/test/dictate/commits/main"] = (500, b"oops")
check("another answer from GitHub", update.check().error, "unexpected")
update.API, real_api = "http://127.0.0.1:9", update.API  # (nothing listens there)
result = update.check()
check("offline", (result.error, "online" in update.problem(result)), ("offline", True))
update.API = real_api
publish()

# --- an update, with the installer and the wait for the new version stood in ---
calls = {"installer": 0, "restart": 0}


def installer_copies(code):
    def run_installer(source, log):
        calls["installer"] += 1
        calls["env"] = {k: update.installer_env().get(k) for k in ("DICTATE_KEEP", "DICTATE_UI_LANGUAGE")}
        for name in NEW:  # what the real installer does to the program files
            shutil.copy2(source / name, update.APP_DIR / name)
        return code
    return run_installer


def restarted(say):
    calls["restart"] += 1


update.restart_dictation = restarted
update.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
update.STATE_PATH.write_text(json.dumps({"ui_language": "sk"}), encoding="utf-8")

update.run_installer, update.wait_ready = installer_copies(0), lambda old_pid, limit=0: True
check("an update that works", update.run(SHA, DATE), 0)
check("the new program files are in place", installed(), {**OLD, **NEW})
check("the previous ones are kept", installed(update.PREVIOUS), OLD)
check("the installer asks nothing and keeps the interface language", calls["env"],
      {"DICTATE_KEEP": "keep", "DICTATE_UI_LANGUAGE": "sk"})
check("it says how it went, once", (update.take_result(), update.take_result()),
      ({"ok": True, "sha": SHA, "date": DATE}, None))
check("the download is cleaned up, the lock is gone, nothing was restarted",
      (update.WORK.exists(), update.LOCK_PATH.exists(), calls["restart"]), (False, False, 0))

install(OLD)
update.run_installer = installer_copies(1)
check("the installer fails", update.run(SHA, DATE), 1)
check("the previous program files are back (the added module is gone, the removed one back)", installed(), OLD)
result = update.take_result()
check("the previous version is started and it says why", (calls["restart"], result["ok"], result["restored"], result["error"]),
      (1, False, True, "the installer stopped with code 1"))

install(OLD)
update.run_installer, update.wait_ready = installer_copies(0), lambda old_pid, limit=0: False
check("the new version doesn't start", (update.run(SHA, DATE), installed(), calls["restart"], update.take_result()["error"]),
      (1, OLD, 2, "the new version didn't start"))

update.wait_ready = lambda old_pid, limit=0: True
before = calls["installer"]
check("a version that can't be downloaded", (update.run("b" * 40, DATE), installed(), calls["restart"], calls["installer"]),
      (1, OLD, 2, before))
check("dictation was never stopped, so nothing is restored or restarted", update.take_result()["restored"], False)
routes[f"/codeload/test/dictate/zip/{'c' * 40}"] = (200, version_zip({**NEW, **INSTALLERS, "../evil.txt": "x"}))
check("a download with a path outside its folder is refused", (update.run("c" * 40, DATE), (TMP / ".local/share/dictate/cache/evil.txt").exists(),
                                                                "unsafe path" in update.take_result()["error"]), (1, False, True))
routes[f"/codeload/test/dictate/zip/{'d' * 40}"] = (200, version_zip({"hello.txt": "not dictate"}))
check("a download that isn't dictate is refused", (update.run("d" * 40, DATE), "isn't a version" in update.take_result()["error"]),
      (1, True))

update.LOCK_PATH.write_text(str(os.getpid()))
check("one update at a time", (update.run(SHA, DATE), update.RESULT_PATH.exists()), (1, False))
update.LOCK_PATH.write_text("999999999")
check("a lock left by an update that died doesn't count", update.run(SHA, DATE), 0)
update.take_result()

# --- the real pieces: the installer's command and environment, the wait for the new version ---
source = TMP / "source"
source.mkdir()
for name, text in INSTALLERS.items():
    (source / name).write_text(text, encoding="utf-8")
os.environ["UPDATE_TEST_OUT"] = str(TMP / "installer-saw.txt")
with open(TMP / "installer.log", "w") as log:
    code = REAL["run_installer"](source, log)
check("the version's own installer runs (install.ps1 on Windows, install.sh elsewhere) without questions",
      (code, (TMP / "installer-saw.txt").read_text(encoding="utf-8").strip()), (3, "keep sk"))
check("its command", update.installer_command(source)[-1], str(source / ("install.ps1" if d.WINDOWS else "install.sh")))

sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
try:
    update.STATUS_PATH.write_text(json.dumps({"pid": sleeper.pid, "model": "tiny"}), encoding="utf-8")
    started = time.monotonic()
    check("a new dictation that loaded its model counts as started (after 5 s of staying)",
          (REAL["wait_ready"](None, limit=20), time.monotonic() - started >= 5), (True, True))
    check("the old one doesn't count", REAL["wait_ready"](sleeper.pid, limit=2), False)
    update.STATUS_PATH.write_text(json.dumps({"pid": sleeper.pid, "model": None}), encoding="utf-8")
    check("nor one whose model didn't load", REAL["wait_ready"](None, limit=2), False)
finally:
    sleeper.kill()
    sleeper.wait()
update.STATUS_PATH.write_text(json.dumps({"pid": sleeper.pid, "model": "tiny"}), encoding="utf-8")
check("nor one that has ended", REAL["wait_ready"](None, limit=2), False)
update.STATUS_PATH.unlink()

# --- starting it, from the menu or a settings window ---
started_commands = []
real_popen, real_run = update.subprocess.Popen, update.subprocess.run
update.subprocess.Popen = lambda command, **kw: started_commands.append(("popen", command, kw.get("start_new_session")))
update.subprocess.run = lambda command, **kw: (started_commands.append(("run", command)),
                                               types.SimpleNamespace(returncode=0, stderr=""))[1]
try:
    update.in_dictate_service = lambda: False
    update.start(SHA, DATE)
    check("it runs on its own: update.py --run SHA DATE, detached", started_commands[-1][:2],
          ("popen", [update.interpreter(), str(update.APP_DIR / "update.py"), "--run", SHA, DATE]))
    if d.LINUX:
        update.in_dictate_service = lambda: True
        update.start(SHA, DATE)
        check("from within the systemd service: a unit of its own, which the service's restart can't end",
              started_commands[-1][1][:5], ["systemd-run", "--user", "--collect", "--unit=dictate-update",
                                             "--description=Dictate update"])
finally:
    update.subprocess.Popen, update.subprocess.run = real_popen, real_run

# --- the menu: Check for updates, then Install the update ---
import tray  # noqa: E402


class StandInTray:
    needs_main_thread = False

    def __init__(self, item_id, title, build_menu, on_click, on_activate=None):
        self.build_menu, self.on_activate = build_menu, on_activate
        self.menu = build_menu()

    def refresh_menu(self):
        self.menu = self.build_menu()

    def set_activate(self, on_activate):
        self.on_activate = on_activate

    def set(self, icon=None, label=None, tooltip=None):
        pass

    def notify(self, summary, body=""):
        pass

    def start(self):
        pass


stand_in = types.ModuleType("tray_pystray")
stand_in.MenuItem, stand_in.TrayIcon = tray.MenuItem, StandInTray
sys.modules["tray_pystray"] = stand_in
d.LINUX = False
notes = []
d.notify = lambda summary, body="": notes.append((summary, body))
update.STATE_PATH.write_text(json.dumps({"ui_language": "en"}), encoding="utf-8")  # (it was Slovak for the installer above)
top = d.TopBar(d.UiState(d.load_config()))
top.panel = types.SimpleNamespace(send=lambda msg: None)


def labels():
    return [item.label for item in top.tray.menu]


def click_and_wait(item_id):  # the check runs on a thread, and ends with a notification
    before = len(notes)
    top.clicked(item_id)
    for _ in range(200):
        if len(notes) > before:
            return
        time.sleep(0.02)


check("the menu has Check for updates", "Check for updates" in labels(), True)
update.check = lambda app_dir=None: update.Check("current", SHA, DATE, "New things.")
click_and_wait(34)
check("up to date: it says so", notes[-1][0], "Dictation is up to date")
update.check = lambda app_dir=None: update.Check("available", SHA, DATE, "New things.")
click_and_wait(34)
check("an update: the menu offers it, with its date", "Install the update from 2026-10-08…" in labels(), True)
check("and a notification says what it is", notes[-1], ("An update is available", "The version from 2026-10-08: New things. "
                                                        "Install it from the menu: Install the update."))
starts = []
update.start = lambda sha, date: starts.append((sha, date))
top.clicked(35)
check("installing it starts the update on its own", (starts, notes[-1][0]), ([(SHA, DATE)], "Updating dictation"))
update.check = lambda app_dir=None: update.Check("error", error="offline")
click_and_wait(34)
check("no connection: it says so", (notes[-1][0], "online" in notes[-1][1]), ("Could not check for updates", True))

# How it ended arrives as a file, which the StateWatcher passes on.
update.write_result({"ok": True, "sha": SHA, "date": DATE})
ended = []
d.StateWatcher(top.ui, lambda kind, value=None: None, ended.append).start()
for _ in range(50):
    if ended:
        break
    time.sleep(0.1)
check("the end of an update reaches dictation once", (ended, update.RESULT_PATH.exists()),
      ([{"ok": True, "sha": SHA, "date": DATE}], False))
top.update_finished(ended[0])
check("updated: it says so", notes[-1], ("Dictation is updated", "This is now the version from 2026-10-08."))
d.i18n.set_language("sk")
top.update_finished({"ok": False, "date": DATE, "error": "the new version didn't start"})
check("failed (in Slovak): the reason, translated", ("nová verzia sa nespustila" in notes[-1][1], notes[-1][0]),
      (True, "Aktualizácia sa nepodarila"))
d.i18n.set_language("en")

# --- the settings windows' buttons (the model both windows share) ---
import bigui  # noqa: E402

model = bigui.SettingsModel()
ids = lambda: [r[1] for r in model.rows() if r[0] == "button"]  # noqa: E731
check("the settings have Check for updates", ("update:check" in ids(), "update:install" in ids()), (True, False))
update.check = lambda app_dir=None: update.Check("available", SHA, DATE, "New things.")
model.check_update()
for _ in range(100):
    if not model.checking:
        break
    time.sleep(0.02)
check("an update: Install the update appears, and what it is", ("update:install" in ids(), model.update_note),
      (True, "The version from 2026-10-08 is available: New things."))
starts.clear()
next(r for r in model.rows() if r[0] == "button" and r[1] == "update:install")[5]()
check("installing it starts the update", (starts, "update:install" in ids()), ([(SHA, DATE)], False))

for name, good, got, want in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}" + ("" if good else f": got {got!r}, want {want!r}"))
sys.exit(0 if all(c[1] for c in checks) else 1)

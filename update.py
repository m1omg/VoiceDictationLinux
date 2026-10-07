"""Updates from GitHub, started from the menu: "Check for updates" compares the installed program
files with the newest version on GitHub; "Install the update" runs that version's own installer,
which keeps every setting, choice and model. The new version must then start and load its model;
if it doesn't (or the installer fails), the previous program files are put back and started.

    python update.py --check           is there a newer version? (what the menu does)
    python update.py --run SHA DATE    install that version (the menu starts this on its own)

Standard library only, and run as a process of its own: the installer stops dictation and starts
it again, and on Windows it must be able to replace the packages' files (a process that has
imported them keeps them open).
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from i18n import t

WINDOWS, MACOS = sys.platform == "win32", sys.platform == "darwin"
LINUX = not (WINDOWS or MACOS)
# (The tests point these at a stand-in for GitHub.)
REPO = os.environ.get("DICTATE_UPDATE_REPO", "m1omg/VoiceDictationLinux")
API = os.environ.get("DICTATE_UPDATE_API", "https://api.github.com")
CODELOAD = os.environ.get("DICTATE_UPDATE_CODELOAD", "https://codeload.github.com")
# What the installers copy into the program folder: the files an update compares and backs up.
PROGRAM = ("*.py", "requirements.txt", "requirements-cuda.txt", "config.example.toml", "README.md")
READY_LIMIT = 300  # seconds for the new version to start and load its model (a slow laptop's CPU)
DOWNLOAD_LIMIT = 100_000_000  # bytes: a version is about 1 MB
NO_WINDOW = 0x08000000 if WINDOWS else 0  # CREATE_NO_WINDOW

# The same folders as dictate.py's (tests/unit_update.py checks that they stay the same).
if WINDOWS:
    APP_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "dictate"
    STATE_PATH, RUNTIME_DIR = APP_DIR / "state.json", APP_DIR / "run"
elif MACOS:
    APP_DIR = Path.home() / "Library/Application Support/dictate"
    STATE_PATH, RUNTIME_DIR = APP_DIR / "state.json", APP_DIR / "run"
else:
    APP_DIR = Path.home() / ".local/share/dictate"
    STATE_PATH = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "dictate/state.json"
    RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
STATUS_PATH = RUNTIME_DIR / "dictate-status.json"  # written by the running dictation (pid, model)
RESULT_PATH = APP_DIR / "update-result.json"  # how the update ended: dictation shows it, then deletes it
LOG_PATH = APP_DIR / "update.log"
LOCK_PATH = APP_DIR / "update.lock"
PREVIOUS = APP_DIR / "previous"  # the program files from before the last update
WORK = APP_DIR / "cache/update"  # the downloaded version, while it is installed
UNIT = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd/user/dictate.service"


class UpdateFailed(Exception):
    pass


# --- is there a newer version? ---------------------------------------------------------------
@dataclass
class Check:
    state: str  # "current", "available" or "error"
    sha: str = ""  # the newest version's commit
    date: str = ""  # its date, YYYY-MM-DD
    summary: str = ""  # its description's first line
    error: str = ""  # "offline", "limit" or "unexpected"
    detail: str = ""


def get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "dictate-update"})
    with urllib.request.urlopen(request, timeout=20) as reply:
        return json.loads(reply.read().decode("utf-8"))


def blob_sha(data: bytes) -> str:
    """Git's name for a file's content; GitHub lists it for every file of a version."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def same_content(data: bytes, want: str) -> bool:
    """A file is the version's: the same bytes, or only Windows line endings instead (a git checkout
    with autocrlf; GitHub's ZIP has the repository's own)."""
    return blob_sha(data) == want or (b"\r\n" in data and blob_sha(data.replace(b"\r\n", b"\n")) == want)


def is_program(name: str) -> bool:
    return "/" not in name and any(fnmatch.fnmatch(name, pattern) for pattern in PROGRAM)


def check(app_dir: Path = APP_DIR) -> Check:
    """Whether the installed program files are the newest version's, however they were installed (git,
    a downloaded ZIP, an earlier update). Files that differ count as older: an update brings them
    back to what is on GitHub."""
    try:
        latest = get_json(f"{API}/repos/{REPO}/commits/main")
        sha, commit = latest["sha"], latest["commit"]
        date = commit["committer"]["date"][:10]
        summary = (commit["message"].strip().splitlines() or [""])[0]
        files = {entry["path"]: entry["sha"] for entry in get_json(f"{API}/repos/{REPO}/git/trees/{sha}")["tree"]
                 if entry.get("type") == "blob" and is_program(entry["path"])}
        if "dictate.py" not in files:
            raise ValueError("the newest version has no dictate.py")
        same = all((app_dir / name).is_file() and same_content((app_dir / name).read_bytes(), want)
                   for name, want in files.items())
        return Check("current" if same else "available", sha, date, summary)
    except urllib.error.HTTPError as e:
        return Check("error", error="limit" if e.code in (403, 429) else "unexpected", detail=f"HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        return Check("error", error="offline", detail=str(getattr(e, "reason", e)))
    except (OSError, ValueError, KeyError, TypeError, IndexError) as e:
        return Check("error", error="unexpected", detail=str(e))


def problem(result: Check) -> str:
    """Why a check failed, for the menu and the settings windows."""
    if result.error == "offline":
        return t("GitHub can't be reached: is the computer online?")
    if result.error == "limit":
        return t("GitHub allows only a few checks an hour from one address; try again later.")
    return t("GitHub's answer was unexpected ({detail}).", detail=result.detail)


# --- starting an update ----------------------------------------------------------------------
def in_dictate_service() -> bool:
    """Whether this process belongs to the systemd service (also the windows dictation opened): when
    the installer restarts the service, systemd ends every process in it."""
    try:
        return "dictate.service" in Path("/proc/self/cgroup").read_text()
    except OSError:
        return False


def interpreter() -> str:
    """The program's own Python; on Windows the one without a console window."""
    if WINDOWS:
        quiet = Path(sys.executable).with_name("pythonw.exe")
        return str(quiet if quiet.exists() else sys.executable)
    return sys.executable


def start(sha: str, date: str) -> None:
    """Install that version in a process of its own, which outlives dictation (the installer stops it
    and starts it again). Raises OSError, with a message for the user, if it can't be started."""
    command = [interpreter(), str(APP_DIR / "update.py"), "--run", sha, date]
    if LINUX and in_dictate_service():
        try:
            done = subprocess.run(["systemd-run", "--user", "--collect", "--unit=dictate-update",
                                   "--description=Dictate update", "--", *command],
                                  capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise OSError(f"systemd-run: {e}") from None
        if done.returncode != 0:
            raise OSError(done.stderr.strip() or f"systemd-run failed ({done.returncode})")
        return
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     cwd=str(APP_DIR), start_new_session=not WINDOWS,
                     creationflags=0x00000008 | 0x00000200 if WINDOWS else 0)  # DETACHED_PROCESS, NEW_PROCESS_GROUP


def take_result() -> dict | None:
    """How the last update ended, once: dictation shows it and the file goes."""
    try:
        result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
        RESULT_PATH.unlink()
        return result if isinstance(result, dict) else None
    except (OSError, ValueError):
        return None


# --- installing it (python update.py --run SHA DATE) -------------------------------------------
def alive(pid: int) -> bool:
    if not pid:
        return False
    if WINDOWS:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == 259
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_status() -> dict:
    try:
        status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        return status if isinstance(status, dict) else {}
    except (OSError, ValueError):
        return {}


def running_pid() -> int | None:
    pid = read_status().get("pid")
    return pid if isinstance(pid, int) and alive(pid) else None


def take_lock() -> bool:
    """One update at a time (a lock left by an update that died doesn't count)."""
    for _attempt in range(2):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                pid = int(LOCK_PATH.read_text().strip() or 0)
            except (OSError, ValueError):
                pid = 0
            if alive(pid):
                return False
            LOCK_PATH.unlink(missing_ok=True)
            continue
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    return False


def download(url: str, path: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "dictate-update"})
    with urllib.request.urlopen(request, timeout=60) as reply, open(path, "wb") as out:
        size = 0
        while chunk := reply.read(1 << 16):
            size += len(chunk)
            if size > DOWNLOAD_LIMIT:
                raise UpdateFailed("the download is far bigger than a version")
            out.write(chunk)


def extract(archive: Path, dest: Path) -> Path:
    """The version's folder from GitHub's ZIP (one folder, VoiceDictationLinux-SHA); nothing may land
    outside dest."""
    root = dest.resolve()
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            if not (root / member.filename).resolve().is_relative_to(root):
                raise UpdateFailed(f"the download has an unsafe path: {member.filename}")
        z.extractall(root)
    folders = [p for p in root.iterdir() if p.is_dir()]
    installer = "install.ps1" if WINDOWS else "install.sh"
    if len(folders) != 1 or not (folders[0] / installer).is_file() or not (folders[0] / "dictate.py").is_file():
        raise UpdateFailed("the download isn't a version of dictate")
    return folders[0]


def backup() -> None:
    """The program files as they are now (and the systemd unit), to put back if the update fails."""
    shutil.rmtree(PREVIOUS, ignore_errors=True)
    PREVIOUS.mkdir(parents=True)
    for path in APP_DIR.iterdir():
        if path.is_file() and is_program(path.name):
            shutil.copy2(path, PREVIOUS / path.name)
    if LINUX and UNIT.is_file():
        shutil.copy2(UNIT, PREVIOUS / "dictate.service")


def restore() -> None:
    """Put the backed-up program files back, and remove the ones the update added."""
    saved = {path.name for path in PREVIOUS.iterdir()}
    for path in APP_DIR.iterdir():
        if path.is_file() and is_program(path.name) and path.name not in saved:
            path.unlink()
    for path in PREVIOUS.iterdir():
        if path.name == "dictate.service":
            shutil.copy2(path, UNIT)
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=30)
        else:
            shutil.copy2(path, APP_DIR / path.name)


def installer_command(source: Path) -> list[str]:
    if WINDOWS:
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(source / "install.ps1")]
    return ["bash", str(source / "install.sh")]


def installer_env() -> dict:
    """No questions: keep every choice, and the interface language."""
    env = {**os.environ, "DICTATE_KEEP": "keep"}
    try:
        language = json.loads(STATE_PATH.read_text(encoding="utf-8")).get("ui_language")
    except (OSError, ValueError, AttributeError):
        language = None
    if language in ("en", "sk"):
        env["DICTATE_UI_LANGUAGE"] = language
    return env


def run_installer(source: Path, log) -> int:
    try:
        return subprocess.run(installer_command(source), cwd=str(source), env=installer_env(), stdin=subprocess.DEVNULL,
                              stdout=log, stderr=subprocess.STDOUT, timeout=3600, creationflags=NO_WINDOW).returncode
    except subprocess.TimeoutExpired:
        raise UpdateFailed("the installer didn't finish within an hour") from None


def wait_ready(old_pid: int | None, limit: float = READY_LIMIT) -> bool:
    """The new version runs: a dictation other than the old one reports its loaded model, and is still
    the same process 5 s later (not starting over and over)."""
    deadline, seen = time.monotonic() + limit, None
    while time.monotonic() < deadline:
        status = read_status()
        pid = status.get("pid")
        if isinstance(pid, int) and pid != old_pid and alive(pid) and status.get("model"):
            if seen is None or seen[0] != pid:
                seen = (pid, time.monotonic())
            elif time.monotonic() - seen[1] >= 5:
                return True
        time.sleep(1)
    return False


def restart_dictation(say) -> None:
    """Start the (restored) program the way its installer does, after stopping a copy that runs."""
    if LINUX and UNIT.is_file():
        subprocess.run(["systemctl", "--user", "reset-failed", "dictate.service"], capture_output=True, timeout=30)
        subprocess.run(["systemctl", "--user", "restart", "dictate.service"], capture_output=True, timeout=60)
        say("restarted the systemd service")
        return
    pid = running_pid()
    if pid:
        try:
            if WINDOWS:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=30,
                               creationflags=NO_WINDOW)
            else:
                os.kill(pid, 15)
        except OSError:
            pass
        for _ in range(20):
            if not alive(pid):
                break
            time.sleep(0.5)
    if MACOS:
        subprocess.run(["open", "-g", "-a", str(Path.home() / "Applications/Dictate.app")], capture_output=True, timeout=60)
    elif WINDOWS:
        subprocess.Popen([interpreter(), "-X", "utf8", str(APP_DIR / "dictate.py")], cwd=str(APP_DIR),
                         creationflags=0x00000008 | 0x00000200)
    else:
        subprocess.Popen(["sh", "-c", f'exec "{sys.executable}" "{APP_DIR / "dictate.py"}" >> "{APP_DIR / "dictate.log"}" 2>&1'],
                         stdin=subprocess.DEVNULL, start_new_session=True)
    say("started the previous version")


def run(sha: str, date: str) -> int:
    """Install that version; make sure it starts; else put the previous program files back."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8") as log:
        def say(text: str) -> None:
            print(f"{time.strftime('%H:%M:%S')} {text}", file=log, flush=True)

        if not take_lock():
            say("another update is running")
            return 1
        backed_up = installer_ran = False
        try:
            old_pid = running_pid()
            say(f"updating to {sha} ({date}); dictation now: {old_pid or 'not running'}")
            shutil.rmtree(WORK, ignore_errors=True)
            WORK.mkdir(parents=True)
            archive = WORK / "version.zip"
            download(f"{CODELOAD}/{REPO}/zip/{sha}", archive)
            source = extract(archive, WORK / "source")
            backup()
            backed_up = True
            say(f"running {source.name}'s installer")
            installer_ran = True
            code = run_installer(source, log)
            if code != 0:
                raise UpdateFailed(f"the installer stopped with code {code}")
            say("waiting for the new version to start")
            if not wait_ready(old_pid):
                raise UpdateFailed("the new version didn't start")
            write_result({"ok": True, "sha": sha, "date": date})
            say("done")
            return 0
        except Exception as e:  # (anything: the previous version must come back)
            say(f"failed: {e}")
            if backed_up:
                restore()
                say("put the previous program files back")
            if installer_ran:
                restart_dictation(say)
            write_result({"ok": False, "sha": sha, "date": date, "error": str(e), "restored": backed_up})
            return 1
        finally:
            shutil.rmtree(WORK, ignore_errors=True)
            LOCK_PATH.unlink(missing_ok=True)


def write_result(result: dict) -> None:
    tmp = RESULT_PATH.with_name(RESULT_PATH.name + ".tmp")
    tmp.write_text(json.dumps(result), encoding="utf-8")
    tmp.replace(RESULT_PATH)


def main() -> int:
    if sys.argv[1:2] == ["--check"]:
        result = check()
        print(result.state, result.sha[:7], result.date, result.summary or result.error, result.detail)
        return 0 if result.state != "error" else 1
    if sys.argv[1:2] == ["--run"] and len(sys.argv) == 4:
        return run(sys.argv[2], sys.argv[3])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())

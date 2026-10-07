"""Start at login, switched from the menu and kept by the installers, for each way a system starts
programs: a systemd user service (a stand-in systemctl), an XDG autostart entry, a macOS LaunchAgent
and a Windows Startup-folder shortcut (stand-in folders and registry). Everything happens in a
temporary folder: nothing is set up to start on your computer.

    python3 tests/unit_login.py        (any Python 3.11+ with numpy)
"""
import ctypes
import os
import plistlib
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="dictate-test-"))
os.environ.update(XDG_STATE_HOME=str(TMP / "state"), XDG_CONFIG_HOME=str(TMP / "config"), XDG_RUNTIME_DIR=str(TMP),
                  LOCALAPPDATA=str(TMP), HOME=str(TMP), USERPROFILE=str(TMP))
import dictate as d  # noqa: E402
import windows  # noqa: E402

checks = []
REAL = (d.WINDOWS, d.MACOS)


def check(name, got, want):
    checks.append((name, got == want, got, want))


def platform(windows_=False, macos=False):
    d.WINDOWS, d.MACOS = windows_, macos


APP_ID = "io.github.m1omg.VoiceDictationLinux"
assert d.STATE_PATH.is_relative_to(TMP) and Path.home() == TMP, (d.STATE_PATH, Path.home())
cfg = d.load_config()
config = TMP / "config"

# --- an XDG autostart entry (desktops without a systemd graphical session, e.g. Cinnamon) ---
platform()
entry = config / "autostart/dictate.desktop"
check("autostart: chosen where there is no systemd unit", d.login_item(APP_ID), ("autostart", entry))
check("autostart: off before anything is set up", d.starts_at_login(APP_ID), False)
d.set_start_at_login(True, APP_ID)
text = entry.read_text(encoding="utf-8")
check("autostart: on writes the entry", d.starts_at_login(APP_ID), True)
check("autostart: it starts the installed program and logs to its file",
      f'Exec=sh -c \'exec "{d.APP_DIR}/venv/bin/python" "{d.APP_DIR}/dictate.py" >> "{d.LOG_PATH}" 2>&1\'' in text, True)
entry.write_text(text.replace("X-GNOME-Autostart-enabled=true", "X-GNOME-Autostart-enabled=false"), encoding="utf-8")
check("autostart: switched off in the desktop's startup settings shows as off", d.starts_at_login(APP_ID), False)
entry.write_text(text + "Hidden=true\n", encoding="utf-8")
check("autostart: Hidden=true shows as off", d.starts_at_login(APP_ID), False)
d.set_start_at_login(True, APP_ID)
check("autostart: on again rewrites it", (d.starts_at_login(APP_ID), entry.read_text(encoding="utf-8")), (True, text))
d.set_start_at_login(False, APP_ID)
check("autostart: off removes the entry", (d.starts_at_login(APP_ID), entry.exists()), (False, False))
d.set_start_at_login(False, APP_ID)
check("autostart: off twice is fine", entry.exists(), False)

# --- a systemd user service (GNOME, KDE): enabled means the graphical-session.target.wants link ---
if not REAL[0]:  # (the stand-in systemctl is a shell script)
    units = config / "systemd/user"
    units.mkdir(parents=True)
    (units / "dictate.service").write_text("[Install]\nWantedBy=graphical-session.target\n")
    bin_dir = TMP / "bin"
    bin_dir.mkdir()
    (bin_dir / "systemctl").write_text(f"""#!/bin/sh
echo "$@" >> "{TMP}/systemctl.log"
wants="{units}/graphical-session.target.wants"
case "$2" in
  enable) mkdir -p "$wants" && ln -sf ../dictate.service "$wants/dictate.service" ;;
  disable) rm -f "$wants/dictate.service" ;;
  *) echo "Unknown command verb $2." >&2; exit 1 ;;
esac
""")
    (bin_dir / "systemctl").chmod(0o755)
    path = os.environ["PATH"]
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{path}"
    check("systemd: chosen once the unit is installed", d.login_item(APP_ID)[0], "systemd")
    check("systemd: a disabled unit is off", d.starts_at_login(APP_ID), False)
    d.set_start_at_login(True, APP_ID)
    check("systemd: on enables it", d.starts_at_login(APP_ID), True)
    d.set_start_at_login(False, APP_ID)
    check("systemd: off disables it (and doesn't stop the running copy)", d.starts_at_login(APP_ID), False)
    check("systemd: what systemctl was asked", (TMP / "systemctl.log").read_text().split("\n")[:2],
          ["--user enable dictate.service", "--user disable dictate.service"])
    (bin_dir / "systemctl").write_text("#!/bin/sh\necho 'Failed to connect to bus' >&2\nexit 1\n")
    try:
        d.set_start_at_login(True, APP_ID)
        check("systemd: a failure is reported", "no error", "OSError")
    except OSError as e:
        check("systemd: a failure is reported with systemctl's message", str(e), "Failed to connect to bus")
    os.environ["PATH"] = path
    (units / "dictate.service").unlink()

# --- a macOS LaunchAgent that opens Dictate.app ---
platform(macos=True)
agent = TMP / f"Library/LaunchAgents/{APP_ID}.plist"
check("macOS: a LaunchAgent named after the app id", d.login_item(APP_ID), ("launchagent", agent))
d.set_start_at_login(True, APP_ID)
plist = plistlib.loads(agent.read_bytes())
check("macOS: on writes it, opening Dictate.app in the background at login",
      (d.starts_at_login(APP_ID), plist), (True, {"Label": APP_ID, "RunAtLoad": True, "ProgramArguments":
                                                  ["/usr/bin/open", "-g", "-a", str(TMP / "Applications/Dictate.app")]}))
d.set_start_at_login(False, APP_ID)
check("macOS: off removes it", (d.starts_at_login(APP_ID), agent.exists()), (False, False))

# --- a Windows Startup-folder shortcut: a copy of the Start menu's, plus Windows' own on/off switch ---
platform(windows_=True)
folders = {"Programs": TMP / "Start Menu/Programs", "Startup": TMP / "Start Menu/Programs/Startup"}
disabled, allowed = set(), []
real_windows = windows.known_folder, windows.startup_disabled, windows.allow_startup
windows.known_folder = lambda name: folders[name]
windows.startup_disabled = lambda name: name in disabled
windows.allow_startup = lambda name: (allowed.append(name), disabled.discard(name))
shortcut = folders["Startup"] / "Dictate.lnk"
check("Windows: the Startup folder's Dictate.lnk", d.login_item(APP_ID), ("startup", shortcut))
try:
    d.set_start_at_login(True, APP_ID)
    check("Windows: without the Start menu shortcut it says so", "no error", "OSError")
except OSError as e:
    check("Windows: without the Start menu shortcut it says so", "run the installer again" in str(e), True)
folders["Programs"].mkdir(parents=True)
(folders["Programs"] / "Dictate.lnk").write_bytes(b"L\0\0\0 a shortcut")
d.set_start_at_login(True, APP_ID)
check("Windows: on copies the Start menu shortcut", (d.starts_at_login(APP_ID), shortcut.read_bytes()),
      (True, b"L\0\0\0 a shortcut"))
disabled.add("Dictate.lnk")
check("Windows: switched off in Settings > Apps > Startup shows as off", d.starts_at_login(APP_ID), False)
d.set_start_at_login(True, APP_ID)
check("Windows: on again lifts that switch", (d.starts_at_login(APP_ID), allowed), (True, ["Dictate.lnk", "Dictate.lnk"]))
d.set_start_at_login(False, APP_ID)
check("Windows: off removes the shortcut", (d.starts_at_login(APP_ID), shortcut.exists()), (False, False))
windows.known_folder, windows.startup_disabled, windows.allow_startup = real_windows
guid = windows.GUID.from_buffer_copy(uuid.UUID(windows.FOLDER_IDS["Startup"]).bytes_le)
check("Windows: the folder id structure is 16 bytes, in Windows' byte order",
      (ctypes.sizeof(windows.GUID), hex(guid.Data1), hex(guid.Data2), bytes(guid.Data4).hex()),
      (16, "0xb97d20bb", "0xf46a", "ba105e3608430854"))
platform(*REAL)
if d.WINDOWS:  # the real shell: read only
    check("Windows: the shell tells where the Startup folder is", windows.known_folder("Startup").is_dir(), True)
    check("Windows: the Startup apps registry can be read", type(windows.startup_disabled("Dictate.lnk")), bool)

# --- the menu's choice, which the installers keep (dictate.py --start-at-login=saved) ---
ui = d.UiState(cfg)
check("the saved choice is on at first", ui.autostart, True)
if not d.WINDOWS:  # (on Windows this would reach the real Startup folder)
    def installer(choice):
        done = subprocess.run([sys.executable, str(ROOT / "dictate.py"), f"--start-at-login={choice}"],
                              capture_output=True, text=True, timeout=120)
        return done.returncode, done.stdout.strip()
    where = "LaunchAgent" if d.MACOS else "autostart entry"
    check("installer: on at first", installer("saved"), (0, f"on ({where})"))
    check("installer: it is set up", d.starts_at_login(APP_ID), True)
    ui.set(autostart=False)
    check("installer: switched off in the menu stays off",
          installer("saved"), (0, f"off ({where}); the menu's Start at login switches it on"))
    check("installer: it is removed", d.starts_at_login(APP_ID), False)
    ui.set(autostart=True)
    check("installer: DICTATE_NO_AUTOSTART=1 (off) doesn't change the saved choice",
          (installer("off")[0], d.UiState(cfg).autostart, d.starts_at_login(APP_ID)), (0, True, False))

for name, good, got, want in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}" + ("" if good else f": got {got!r}, want {want!r}"))
sys.exit(0 if all(c[1] for c in checks) else 1)

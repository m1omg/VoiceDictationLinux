"""The installer's questions (dictate.py --setup) without a terminal, as installers and CI run them:
a first install takes the suggestions for the computer; an update keeps the current choices, also
those of an install from before the models could be chosen; an environment variable changes only its
own answer; a graphics card too small for the models is left alone. Nothing is downloaded, and the
settings go to a temporary folder.

    python3 tests/unit_setup.py        (any Python 3.11+ with numpy)
"""
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="dictate-test-"))
os.environ.update(XDG_STATE_HOME=str(TMP / "state"), XDG_CONFIG_HOME=str(TMP / "config"), XDG_RUNTIME_DIR=str(TMP),
                  LOCALAPPDATA=str(TMP), HOME=str(TMP), XDG_SESSION_TYPE="x11")
ANSWERS = ("DICTATE_LANGUAGE", "DICTATE_MODEL", "DICTATE_KEY", "DICTATE_LARGE_UI", "DICTATE_KEEP")
for name in ANSWERS:
    os.environ.pop(name, None)
import dictate as d  # noqa: E402
import models  # noqa: E402

assert d.STATE_PATH.is_relative_to(TMP) and d.CONFIG_PATH.is_relative_to(TMP)
sys.stdin = open(os.devnull)  # no terminal: every question takes its default
downloads = []
real_finish_setup = d.finish_setup
d.finish_setup = lambda gpu_model, cpu_model, slovak="": downloads.append((gpu_model, cpu_model)) or 0
LAPTOP = models.Hardware(None, cores=4, ram_gb=16, laptop=True)
checks = []


def check(name, got, want):
    checks.append((name, got == want, got, want))


def setup(hw, gpu="none", **answers):
    models.probe = lambda _gpu: hw
    os.environ.update(answers)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            d.run_setup(d.load_config(), gpu)
    finally:
        for name in answers:
            os.environ.pop(name)
    return json.loads(d.STATE_PATH.read_text(encoding="utf-8"))


def fresh():
    d.STATE_PATH.unlink(missing_ok=True)
    d.CONFIG_PATH.unlink(missing_ok=True)


fresh()
state = setup(LAPTOP, DICTATE_LANGUAGE="sk")
check("first install, Slovak on a 4-core laptop: turbo on the processor, Right Ctrl (Mac: Right Option)",
      (state["language"], state["cpu_model"], state["trigger"], downloads[-1]),
      ("sk", "large-v3-turbo", "Alt_R" if d.MACOS else "Control_R", (None, "large-v3-turbo")))



check("and no model fine-tuned for Slovak beside it unless one is asked for (off by default)", state["cpu_slovak"], "")
fresh()
state = setup(models.Hardware("nvidia", gpu_name="RTX 3060", vram_gb=12, cores=6, ram_gb=64), gpu="nvidia",
              DICTATE_LANGUAGE="auto")
check("both languages on a graphics card: turbo alone", (state["gpu_model"], state["gpu_slovak"]), ("large-v3-turbo", ""))
fresh()
state = setup(LAPTOP, DICTATE_LANGUAGE="auto", DICTATE_MODEL="small", DICTATE_SLOVAK_MODEL="small-sk")
check("DICTATE_SLOVAK_MODEL=small-sk: small-sk beside small", (state["cpu_model"], state["cpu_slovak"]), ("small", "small-sk"))
state = setup(LAPTOP)
check("an update keeps a Slovak model chosen before", state["cpu_slovak"], "small-sk")
fresh()
state = setup(LAPTOP, DICTATE_LANGUAGE="en")
check("English only: no Slovak model", state.get("cpu_slovak", ""), "")
fresh()
state = setup(LAPTOP, DICTATE_LANGUAGE="sk", DICTATE_SLOVAK_MODEL="none")
check("DICTATE_SLOVAK_MODEL=none: none", state["cpu_slovak"], "")


class EndedInput(io.StringIO):
    """A terminal whose input has ended: NUL on Windows counts as a terminal."""

    def isatty(self):
        return True


fresh()
sys.stdin = EndedInput()
state = setup(LAPTOP, DICTATE_LANGUAGE="sk")
sys.stdin = open(os.devnull)
check("the same when the terminal's input has ended (NUL on Windows)",
      (state["cpu_model"], state["trigger"]), ("large-v3-turbo", "Alt_R" if d.MACOS else "Control_R"))

fresh()  # an install from before the models could be chosen: a settings file, and the menu's three choices
d.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
d.CONFIG_PATH.write_text('model = "large-v3-turbo"\nfallback_model = "small"\n', encoding="utf-8")
d.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
d.STATE_PATH.write_text(json.dumps({"language": "sk", "live": False, "sounds": True}), encoding="utf-8")
state = setup(LAPTOP)
check("update of an older install: language, key and model kept",
      (state["language"], state["trigger"], state["cpu_model"], state["live"], downloads[-1]),
      ("sk", "KP_Delete", "small", False, (None, "small")))

d.UiState(d.load_config()).set(big_panel=True, big_settings=True)
state = setup(LAPTOP, DICTATE_MODEL="medium")
check("an update with DICTATE_MODEL changes only the model",
      (state["cpu_model"], state["language"], state["big_panel"], state["big_settings"]),
      ("medium", "sk", True, True))

state = setup(LAPTOP, DICTATE_KEEP="change")
check("choosing again without a terminal suggests the current choices",
      (state["cpu_model"], state["language"], state["trigger"], state["big_panel"]), ("medium", "sk", "KP_Delete", True))

if d.LINUX:  # any X11 key name, also those python-xlib knows only after loading their group
    check("Linux: AltGr (ISO_Level3_Shift) and XF86 keys are accepted", [
        setup(LAPTOP, DICTATE_KEY=key)["trigger"] for key in ("ISO_Level3_Shift", "XF86Launch5")],
        ["ISO_Level3_Shift", "XF86Launch5"])

fresh()
state = setup(models.Hardware("nvidia", vram_gb=1.5, cores=8, ram_gb=16), "nvidia", DICTATE_LANGUAGE="en")
check("a 1.5 GB graphics card: the processor is chosen and remembered",
      (state.get("device"), state["cpu_model"], downloads[-1]), ("cpu", "small", (None, "small")))

fresh()
state = setup(models.Hardware("nvidia", vram_gb=12, cores=8, ram_gb=32), "nvidia", DICTATE_LANGUAGE="sk")
check("a 12 GB graphics card, Slovak: one download serves both",
      (state.get("device"), state["gpu_model"], state["cpu_model"], downloads[-1]),
      (None, "large-v3-turbo", "large-v3-turbo", ("large-v3-turbo", "large-v3-turbo")))

# --- the model check after the questions: the dictation that runs is stopped first, so the check has
# the graphics card to itself (two sets of models with Slovak and English ones didn't fit on 12 GB) ---
import subprocess  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import update  # noqa: E402

assert update.STATUS_PATH.is_relative_to(TMP) and d.COMMAND_PATH.is_relative_to(TMP) and update.UNIT.is_relative_to(TMP)
STAND_IN = TMP / "stand_in_dictation.py"  # quits on the settings window's command, or ignores it
STAND_IN.write_text("""import pathlib, sys, time
command = pathlib.Path(sys.argv[1])
for _ in range(600):
    if sys.argv[2] == "obeys" and command.exists():
        command.unlink()
        sys.exit(0)
    time.sleep(0.05)
""")


def running(mode):
    proc = subprocess.Popen([sys.executable, str(STAND_IN), str(d.COMMAND_PATH), mode])
    threading.Thread(target=proc.wait, daemon=True).start()  # (reaped at once: a zombie would count as running)
    update.STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    update.STATUS_PATH.write_text(json.dumps({"pid": proc.pid, "model": "tiny"}), encoding="utf-8")
    return proc


def stop_quietly():
    with contextlib.redirect_stdout(io.StringIO()) as out:
        stopped = d.stop_for_check()
    return stopped, out.getvalue()


update.STATUS_PATH.unlink(missing_ok=True)
check("no dictation running: nothing to stop, nothing said", stop_quietly(), (False, ""))
proc = running("obeys")
os.environ["DICTATE_NO_AUTOSTART"] = "1"
check("an installer that won't start dictation again leaves it running", (stop_quietly()[0], proc.poll()), (False, None))
del os.environ["DICTATE_NO_AUTOSTART"]
stopped, said = stop_quietly()
proc.wait(10)
check("a running dictation is asked to quit, and has", (stopped, proc.returncode, bool(said), d.COMMAND_PATH.exists()),
      (True, 0, True, False))
proc = running("ignores")
real_sleep, time.sleep = time.sleep, lambda seconds: real_sleep(0.01)
try:
    stopped, _ = stop_quietly()
finally:
    time.sleep = real_sleep
check("one that doesn't react: the command is taken back (the next dictation mustn't quit at once)",
      (stopped, d.COMMAND_PATH.exists()), (True, False))
proc.kill()
proc.wait()

restarts = []
real_run, real_stop, real_restart = subprocess.run, d.stop_for_check, update.restart_dictation
d.stop_for_check, update.restart_dictation = (lambda: True), (lambda say: restarts.append("restarted"))
try:
    for code in (0, 1):
        subprocess.run = lambda command, **kw: subprocess.CompletedProcess(command, code)
        with contextlib.redirect_stdout(io.StringIO()):
            real_finish_setup(None, "")
finally:
    subprocess.run, d.stop_for_check, update.restart_dictation = real_run, real_stop, real_restart
check("a failed check starts the stopped dictation again (a passed one: the installer does)", restarts, ["restarted"])

for name, good, got, want in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}" + ("" if good else f": got {got!r}, want {want!r}"))
sys.exit(0 if all(c[1] for c in checks) else 1)

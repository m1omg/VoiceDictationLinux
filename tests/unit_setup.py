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
d.finish_setup = lambda gpu_model, cpu_model: downloads.append((gpu_model, cpu_model)) or 0
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

fresh()
state = setup(models.Hardware("nvidia", vram_gb=1.5, cores=8, ram_gb=16), "nvidia", DICTATE_LANGUAGE="en")
check("a 1.5 GB graphics card: the processor is chosen and remembered",
      (state.get("device"), state["cpu_model"], downloads[-1]), ("cpu", "small", (None, "small")))

fresh()
state = setup(models.Hardware("nvidia", vram_gb=12, cores=8, ram_gb=32), "nvidia", DICTATE_LANGUAGE="sk")
check("a 12 GB graphics card, Slovak: one download serves both",
      (state.get("device"), state["gpu_model"], state["cpu_model"], downloads[-1]),
      (None, "large-v3-turbo", "large-v3-turbo", ("large-v3-turbo", "large-v3-turbo")))

for name, good, got, want in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}" + ("" if good else f": got {got!r}, want {want!r}"))
sys.exit(0 if all(c[1] for c in checks) else 1)

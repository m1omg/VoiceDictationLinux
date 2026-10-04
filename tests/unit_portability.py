"""dictate.py, windows.py and macos.py import on every OS: nothing Linux-only (fcntl, jeepney, Xlib,
os.getuid) and nothing Windows- or macOS-only runs at import time. It pretends to be Windows and
macOS by setting sys.platform before each import. No windows, microphone, clipboard or keys.

    python3 tests/unit_portability.py        (any Python 3.11+; needs numpy)
"""
import importlib.abc
import importlib.util
import os
import sys
from pathlib import Path

# The standard library looks at sys.platform when it is first imported (shutil, subprocess, ...):
# import what dictate.py uses for real before pretending to be another OS.
for stdlib in ("argparse", "ctypes", "faulthandler", "functools", "glob", "inspect", "itertools", "json",
               "logging", "queue", "re", "secrets", "select", "shutil", "signal", "struct", "subprocess",
               "sysconfig", "threading", "time", "tomllib", "wave", "collections", "dataclasses", "types",
               "numpy"):
    __import__(stdlib)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BLOCKED = {"fcntl", "jeepney", "Xlib", "winsound", "Quartz", "AppKit", "Foundation", "objc"}


class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"{name} is blocked by this test")
        return None


def load(platform: str):
    """Import a fresh dictate.py while sys.platform says `platform`."""
    real = sys.platform
    sys.platform = platform
    try:
        spec = importlib.util.spec_from_file_location(f"dictate_{platform}", ROOT / "dictate.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # dataclasses look the module up while it runs
        spec.loader.exec_module(module)
        return module
    finally:
        sys.platform = real


checks = []
sys.meta_path.insert(0, Block())
for name in BLOCKED:
    sys.modules.pop(name, None)
getuid = getattr(os, "getuid", None)  # Windows has none
if getuid:
    del os.getuid
os.environ.setdefault("LOCALAPPDATA", str(Path.home() / "AppData/Local"))
try:
    for platform, app_dir, kind in [("win32", Path(os.environ["LOCALAPPDATA"]) / "dictate", "windows"),
                                    ("darwin", Path.home() / "Library/Application Support/dictate", "macos")]:
        d = load(platform)
        cfg = d.SimpleNamespace(**d.DEFAULTS)
        checks.append((f"{platform}: dictate.py imports", True))
        checks.append((f"{platform}: app folder", d.APP_DIR == app_dir and d.CONFIG_PATH.parent == app_dir))
        checks.append((f"{platform}: backend", d.backend(cfg) == kind))
        checks.append((f"{platform}: no key-repeat guessing", d.keyboard_repeat()[0] is False))
    for module in ("windows", "macos"):
        spec = importlib.util.spec_from_file_location(module, ROOT / f"{module}.py")
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
        checks.append((f"{module}.py imports", True))
finally:
    if getuid:
        os.getuid = getuid
os.environ.setdefault("XDG_RUNTIME_DIR", "/tmp")
d = load("linux")
checks.append(("linux: paths unchanged", d.APP_DIR == Path.home() / ".local/share/dictate"
               and d.CONFIG_PATH.name == "config.toml" and d.STATE_PATH.name == "state.json"))
for name, good in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}")
sys.exit(0 if all(good for _, good in checks) else 1)

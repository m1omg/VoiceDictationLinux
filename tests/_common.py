"""Shared helpers for the benchmark scripts. They need no microphone, no windows and no user.

Run them with the app's own Python:  ~/.local/share/dictate/venv/bin/python tests/<script>.py
(macOS: ~/Library/Application Support/dictate/venv/bin/python; Windows:
%LOCALAPPDATA%\\dictate\\venv\\Scripts\\python.exe; set DICTATE_DIR if it is installed elsewhere).
"""
import importlib.util
import json
import logging
import os
import re
import sys
from pathlib import Path

DEFAULT_DIR = (Path(os.environ.get("LOCALAPPDATA", "")) / "dictate" if sys.platform == "win32"
               else Path.home() / "Library/Application Support/dictate" if sys.platform == "darwin"
               else Path.home() / ".local/share/dictate")
DICTATE_DIR = Path(os.environ.get("DICTATE_DIR", DEFAULT_DIR))
FLEURS = Path(__file__).resolve().parent / "fleurs"
logging.basicConfig(level=logging.WARNING)


def load_dictate():
    """Import the installed dictate.py as a module."""
    sys.path.insert(0, str(DICTATE_DIR))
    spec = importlib.util.spec_from_file_location("dictate", DICTATE_DIR / "dictate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["dictate"] = module
    spec.loader.exec_module(module)
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    return module


def load_model(d, cfg):
    """The model dictation would use (the menu's choice), or the one named on the command line, e.g.
    `bench_asr.py small` (add --cpu to run it on the CPU)."""
    ui = d.UiState(cfg)
    names = [a for a in sys.argv[1:] if not a.startswith("--")]
    tr = d.Transcriber(cfg)
    tr.load("cpu" if "--cpu" in sys.argv else ui.device, *(names[:1] * 2 or (ui.gpu_model, ui.cpu_model)))
    return tr


def sentences(lang: str) -> list[dict]:
    """The downloaded test sentences for "en" or "sk" (run fetch_fleurs.py first)."""
    name = {"en": "eng_Latn", "sk": "slk_Latn"}[lang]
    return json.load(open(FLEURS / f"{name}.json", encoding="utf-8"))


def audio(item):
    from faster_whisper import decode_audio
    return decode_audio(str(FLEURS / item["file"]), sampling_rate=16000)


def words(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", text.lower()).split()


def errors(ref: str, hyp: str) -> tuple[int, int]:
    """Word errors (substitutions + insertions + deletions) and reference length."""
    r, h = words(ref), words(hyp)
    row = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, row[0] = row[0], i
        for j, hw in enumerate(h, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (rw != hw))
    return row[len(h)], len(r)

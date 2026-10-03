"""Model recommendations for made-up computers, model completeness checks, and the menu choices in
state.json (old files, bad values). No downloads, no model, no user.

    python3 tests/unit_models.py        (any Python 3.11+ with numpy; or the app's own Python)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="dictate-test-"))
# Settings and state in TMP on every OS (Linux: XDG folders, Windows: LOCALAPPDATA, macOS: HOME);
# DICTATE_DIR: this checkout's dictate.py, not the installed one.
os.environ.update(XDG_STATE_HOME=str(TMP / "state"), XDG_CONFIG_HOME=str(TMP / "config"), XDG_RUNTIME_DIR=str(TMP),
                  LOCALAPPDATA=str(TMP), HOME=str(TMP), DICTATE_DIR=str(ROOT))
import models  # noqa: E402
from _common import load_dictate  # noqa: E402

checks = []


def check(name, got, want):
    checks.append((name, got == want, got, want))


H = models.Hardware
check("RTX 3060: turbo on the GPU, small on the CPU", models.recommend(H("nvidia", vram_gb=12, cores=6), "en"),
      ("gpu", "large-v3-turbo", "small"))
check("GPU of unknown size (AMD on Windows): turbo", models.recommend(H("amd", cores=8), "sk")[:2],
      ("gpu", "large-v3-turbo"))
check("1.5 GB GPU: CPU", models.recommend(H("nvidia", vram_gb=1.5, cores=8), "en")[:2], ("cpu", None))
check("MacBook Air 2017, English: base", models.recommend(H(None, cores=2, ram_gb=8), "en"), ("cpu", None, "base"))
check("MacBook Air 2017, Slovak: small", models.recommend(H(None, cores=2, ram_gb=8), "sk")[2], "small")
check("2 cores, auto-detect: small", models.recommend(H(None, cores=2), "auto")[2], "small")
check("4-core laptop, English: small", models.recommend(H(None, cores=4, laptop=True), "en")[2], "small")
check("2 GB RAM: base", models.recommend(H(None, cores=4, ram_gb=2), "sk")[2], "base")
check("1 GB RAM: tiny", models.recommend(H(None, cores=4, ram_gb=1), "en")[2], "tiny")
check("medium is never recommended", any("medium" in str(models.recommend(H(g, vram_gb=v, cores=c), lang))
                                          for g in (None, "nvidia") for v in (0, 2, 24) for c in (1, 2, 4, 16)
                                          for lang in ("en", "sk", "auto")), False)
check("size label", (models.size_label("tiny"), models.size_label("large-v3-turbo")), ("76 MB", "1.6 GB"))

md = TMP / "models"
(md / "small").mkdir(parents=True)
(md / "small" / "model.bin").write_bytes(b"x")
check("model.bin alone is incomplete", models.installed(md, "small"), False)
for f in ("config.json", "tokenizer.json", "vocabulary.txt"):
    (md / "small" / f).write_text("{}")
check("all files: complete", models.installed(md, "small"), True)
(md / "large-v3-turbo").mkdir()
for f in ("model.bin", "config.json", "tokenizer.json", "vocabulary.json"):
    (md / "large-v3-turbo" / f).write_text("{}")
check("large-v3 needs preprocessor_config.json", models.installed(md, "large-v3-turbo"), False)
(md / "mine").mkdir()
(md / "mine" / "model.bin").write_bytes(b"x")
check("a model of your own needs model.bin", models.installed(md, "mine"), True)

d = load_dictate()
cfg = d.load_config()
state = d.STATE_PATH
assert state.is_relative_to(TMP), state
state.parent.mkdir(parents=True)
state.write_text(json.dumps({"language": "sk", "live": False, "sounds": True}))  # written before models were choosable
ui = d.UiState(cfg)
check("old state.json: its choices kept", (ui.language, ui.live), ("sk", False))
check("old state.json: new choices from config", (ui.device, ui.gpu_model, ui.cpu_model),
      ("auto", "large-v3-turbo", "small"))
state.write_text(json.dumps({"device": "npu", "gpu_model": "../../etc", "cpu_model": "base", "language": "xx"}))
ui = d.UiState(cfg)
check("bad values ignored", (ui.device, ui.gpu_model, ui.cpu_model, ui.language), ("auto", "large-v3-turbo", "base", "en"))
ui.set(device="cpu")
check("a choice is saved", json.loads(state.read_text())["device"], "cpu")
check("and read back", d.UiState(cfg).device, "cpu")

for name, good, got, want in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}" + ("" if good else f": got {got!r}, want {want!r}"))
sys.exit(0 if all(c[1] for c in checks) else 1)

"""Qwen3-ASR for English: the engine that runs qwen_worker.py in its own process (here a stand-in that
speaks the same protocol, so no PyTorch or model is needed), what happens when a pass fails, hangs or
the process ends (Whisper takes over), which passes go to Qwen (English after release; live passes
need Whisper's word timings), and installing the Qwen environment (the commands, with a stand-in).

    python3 tests/unit_qwen.py        (the program's Python: numpy, faster-whisper's VAD)
"""
import os
import sys
import tempfile
import textwrap
import time
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="dictate-test-"))
os.environ.update(XDG_STATE_HOME=str(TMP / "state"), XDG_CONFIG_HOME=str(TMP / "config"), XDG_RUNTIME_DIR=str(TMP),
                  LOCALAPPDATA=str(TMP), HOME=str(TMP), USERPROFILE=str(TMP))
import dictate as d  # noqa: E402
import models  # noqa: E402

checks = []


def check(name, got, want):
    checks.append((name, got == want, got, want))


notes = []
d.notify = lambda summary, body="": notes.append(summary)

# --- the engine, with a stand-in for qwen_worker.py (same protocol) ---
WORKER = TMP / "stand_in_worker.py"
WORKER.write_text(textwrap.dedent('''
    import json, sys, time
    if sys.argv[1].endswith("broken"):
        print(json.dumps({"error": "OSError: no such model"}), flush=True)
        sys.exit(1)
    print("a library talking on stderr", file=sys.stderr)
    print(json.dumps({"ready": True, "device": "cuda:0/bfloat16"}), flush=True)
    stdin = sys.stdin.buffer
    for line in iter(stdin.readline, b""):
        request = json.loads(line)
        stdin.read(4 * request["samples"])
        if request["context"] == "crash":
            sys.exit(3)
        if request["context"] == "slow":
            time.sleep(30)
        if request["context"] == "error":
            print(json.dumps({"error": "RuntimeError: CUDA out of memory"}), flush=True)
            continue
        print(json.dumps({"text": f"{request['samples']} samples, told {request['language']}, {request['context']}",
                          "language": "English"}), flush=True)
'''))
engine = d.QwenEngine("qwen3-asr-1.7b", python=sys.executable, worker=WORKER)
engine.start()
check("it starts and says where it runs", (engine.alive(), engine.device), (True, "cuda:0/bfloat16"))
check("a pass: the audio and the vocabulary go over (no language: Qwen says what it heard), the text comes back",
      engine.transcribe(np.zeros(16000, np.float32), "Claude, GNOME"), ("16000 samples, told None, Claude, GNOME", "English"))
try:
    engine.transcribe(np.zeros(100, np.float32), "error")
    check("a failed pass raises", "nothing", "RuntimeError")
except RuntimeError as e:
    check("a failed pass raises, and the process goes on", (str(e), engine.alive()), ("RuntimeError: CUDA out of memory", True))
engine.PASS_LIMIT = 1
started = time.monotonic()
try:
    engine.transcribe(np.zeros(100, np.float32), "slow")
    check("a pass that takes too long raises", "nothing", "TimeoutError")
except TimeoutError:
    check("a pass that takes too long ends the process (a late answer would be taken for the next one)",
          (engine.alive(), time.monotonic() - started < 5), (False, True))
engine = d.QwenEngine("qwen3-asr-1.7b", python=sys.executable, worker=WORKER)
engine.start()
try:
    engine.transcribe(np.zeros(100, np.float32), "crash")
    check("a process that ends raises", "nothing", "RuntimeError")
except RuntimeError:
    check("a process that ends raises, and is seen as ended", engine.alive(), False)
broken = d.QwenEngine("broken", python=sys.executable, worker=WORKER)
d.model_dir, real_model_dir = (lambda name: str(TMP / name)), d.model_dir
try:
    broken.start()
    check("a model that can't start says why", "nothing", "RuntimeError")
except RuntimeError as e:
    check("a model that can't start says why", str(e), "OSError: no such model")
d.model_dir = real_model_dir

# --- which passes go to Qwen ---
calls = []


class Whisper:
    def transcribe(self, audio, language=None, beam_size=None, vad_filter=None, condition_on_previous_text=None,
                   without_timestamps=None, word_timestamps=None, hotwords=None, initial_prompt=None, batch_size=None):
        calls.append(("whisper", language))
        return iter([types.SimpleNamespace(text="whisper's text")]), None


class StandInQwen:
    name = "qwen3-asr-1.7b"

    def __init__(self):
        self.fail, self.ended, self.heard = None, False, "English"

    def alive(self):
        return not self.ended

    def transcribe(self, audio, context=""):
        calls.append(("qwen", context))
        if self.fail:
            raise self.fail
        return "qwen's text", self.heard


tr = d.Transcriber(d.load_config())
tr.model = tr.batched = Whisper()
tr.english = qwen = StandInQwen()
d.speech_only, real_speech_only = (lambda audio: audio), d.speech_only
speech = np.ones(2 * d.RATE, np.float32) * 0.5
check("English after release: Qwen, with the vocabulary as its context",
      ([s.text for s in tr.run(speech, "en")], calls[-1]), (["qwen's text"], ("qwen", tr.hotwords)))
calls.clear()
tr.run(speech, "en", words=True)
tr.run(speech, "sk")
check("live passes (word timings) and Slovak: Whisper", calls, [("whisper", "en"), ("whisper", "sk")])
calls.clear()
qwen.heard = "Czech"  # (what Qwen calls Slovak: it was taken for English by mistake)
check("Qwen hears Czech: done again as Slovak, and the worker learns it was Slovak",
      ([s.text for s in tr.run(speech, "en")], calls[-1], tr.heard), (["whisper's text"], ("whisper", "sk"), "sk"))
tr.heard = None
calls.clear()
qwen.heard = "German"
check("Qwen hears something else: Whisper in English, as without Qwen", ([s.text for s in tr.run(speech, "en")], calls[-1],
                                                                        tr.heard), (["whisper's text"], ("whisper", "en"), None))
qwen.heard = "English"
calls.clear()
qwen.fail = RuntimeError("CUDA out of memory")
check("a failed Qwen pass: Whisper does that one; Qwen stays", ([s.text for s in tr.run(speech, "en")], tr.english is qwen),
      (["whisper's text"], True))
qwen.ended = True
tr.run(speech, "en")
check("Qwen ended: Whisper from now on, and it says so", (tr.english, notes[-1]), (None, "Qwen stopped working"))
d.speech_only = real_speech_only
check("silence has no speech for Qwen", d.speech_only(np.zeros(3 * d.RATE, np.float32)).size, 0)
tr.english, qwen.ended, qwen.fail = qwen, False, None
calls.clear()
check("a recording of silence: nothing goes to Qwen", (tr.run(np.zeros(3 * d.RATE, np.float32), "en"), calls), ([], []))

# --- the Qwen environment and the model ---
models_dir = TMP / "app/models"
models_dir.mkdir(parents=True)
commands = []


def run(command, env=None, capture_output=None, text=None, fail_at=None):
    commands.append(command)
    return types.SimpleNamespace(returncode=1 if fail_at and fail_at in command else 0, stderr="no space left", stdout="")


models.install_qwen_runtime(models_dir, "/the/program/python", run=run)
uv = str(models_dir.parent / "bin" / ("uv.exe" if os.name == "nt" else "uv"))
check("the environment: a venv from the program's Python, PyTorch for CUDA, the few packages, qwen-asr alone",
      [c[:3] + [c[-1]] if c[1] == "venv" else c[1:2] + c[-3:] for c in commands],
      [[uv, "venv", "--quiet", str(models.qwen_venv(models_dir))],
       ["pip", "torch", "--index-url", models.QWEN_TORCH_INDEX],
       ["pip", "soundfile", "librosa", "nagisa==0.2.11"],
       ["pip", str(models.qwen_python(models_dir)), "--no-deps", models.QWEN_ASR],
       ["cache", uv, "cache", "clean"]])
check("and it counts as installed only after the last step", models.qwen_runtime_ready(models_dir), False)
models.qwen_python(models_dir).parent.mkdir(parents=True, exist_ok=True)
models.qwen_python(models_dir).write_text("")
check("(with its Python)", models.qwen_runtime_ready(models_dir), True)
try:
    models.install_qwen_runtime(models_dir, "/the/program/python", run=lambda c, **kw: run(c, fail_at="torch", **kw))
    check("a failed step says why", "nothing", "RuntimeError")
except RuntimeError as e:
    check("a failed step says why, and the environment no longer counts", ("no space left" in str(e),
                                                                           models.qwen_runtime_ready(models_dir)), (True, False))
check("what choosing it downloads: the model and, the first time, the environment",
      (models.size_label("qwen3-asr-1.7b"), models.size_label("qwen3-asr-0.6b", with_runtime=False)),
      ("4.7 GB + 4.0 GB of software", "1.9 GB"))

import huggingface_hub  # noqa: E402

installs = []
models.install_qwen_runtime = lambda md, python, run=None: (installs.append(python), (models.qwen_venv(md) / "dictate-ready").write_text("x"))


def stand_in_download(repo, revision=None, local_dir=None, allow_patterns=None):
    Path(local_dir).mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "preprocessor_config.json", "vocab.json", "model.safetensors"):
        (Path(local_dir) / name).write_text("{}")
    installs.append((repo, revision))


huggingface_hub.snapshot_download = stand_in_download
d.MODELS_DIR = models_dir
first_note = d.TopBar.download_note("qwen3-asr-1.7b", "")
models.download(models_dir, "qwen3-asr-1.7b")
check("the download: the environment first, then the model at its pinned revision",
      (len(installs), installs[1], models.installed(models_dir, "qwen3-asr-1.7b")),
      (2, ("Qwen/Qwen3-ASR-1.7B", models.QWEN["qwen3-asr-1.7b"][1]), True))
second_note = d.TopBar.download_note("qwen3-asr-0.6b", "")
models.download(models_dir, "qwen3-asr-0.6b")
check("a second Qwen model reuses the environment", len(installs), 3)
check("the menu: the environment's size only while it isn't installed",
      ("4.7 GB" in first_note and "4.0 GB" in first_note, "1.9 GB" in second_note and "4.0 GB" not in second_note), (True, True))

for name, good, got, want in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}" + ("" if good else f": got {got!r}, want {want!r}"))
sys.exit(0 if all(c[1] for c in checks) else 1)

"""Model switching with a stand-in worker (no model is loaded, nothing is downloaded): at start-up a
chosen model that isn't downloaded (dictation restarted during its download, say) is replaced by an
installed one, then downloaded; a failed download puts the menu back on the model that runs, and
picking the model again retries.

    python3 tests/unit_switch.py        (any Python 3.11+ with numpy)
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="dictate-test-"))
os.environ.update(XDG_STATE_HOME=str(TMP / "state"), XDG_CONFIG_HOME=str(TMP / "config"), XDG_RUNTIME_DIR=str(TMP),
                  LOCALAPPDATA=str(TMP), HOME=str(TMP))
os.environ.pop("DICTATE_FORCE_CPU", None)
import dictate as d  # noqa: E402

checks, notes = [], []


def check(name, got, want):
    checks.append((name, got == want, got, want))


INSTALLED = {"small", "base"}
d.models.installed = lambda models_dir, name: name in INSTALLED
d.models.partial_bytes = lambda models_dir, name: 0
d.notify = lambda summary, body="": notes.append(summary)


class Worker:
    def __init__(self):
        self.wanted, self.choice, self.loading = None, ("auto", None, None), None
        self.transcriber = SimpleNamespace(ready=threading.Event(), name=None, on_cpu=False, gpu_available=None)

    def want(self, choice):
        self.wanted = choice

    def loaded(self, choice, name, on_cpu=True):  # what the worker thread does after a load
        self.choice, self.wanted = choice, None
        self.transcriber.name, self.transcriber.on_cpu, self.transcriber.gpu_available = name, on_cpu, False
        self.transcriber.ready.set()


ui = d.UiState(d.load_config())
ui.set(device="cpu", cpu_model="large-v3-turbo")  # chosen, but its download never finished
worker = Worker()
switch = d.ModelSwitch(ui, worker, SimpleNamespace(update=lambda **changes: None))
downloads = []
switch.download = lambda name, key: downloads.append((name, key))
switch.start()
check("start-up: installed models stand in for missing ones", worker.wanted, ("cpu", "small", "small"))
worker.loaded(worker.wanted, "small")
end = time.monotonic() + 5
while not downloads and time.monotonic() < end:
    time.sleep(0.1)
check("then the chosen model is downloaded", downloads, [("large-v3-turbo", "cpu_model")])


class FailedDownload:  # the child process: exits at once, without a model
    returncode = 1

    def __init__(self, *args, **kwargs):
        pass

    def poll(self):
        return 1


d.subprocess.Popen = FailedDownload
d.ModelSwitch._download(switch, "large-v3-turbo", "cpu_model")
check("a failed download is reported", notes[-1:], ["The large-v3-turbo model could not be downloaded"])
check("and the menu shows the model that runs", ui.cpu_model, "small")
ui.set(cpu_model="large-v3-turbo")  # picked again in the menu
switch.changed()
check("picking it again retries", downloads[-1], ("large-v3-turbo", "cpu_model"))
ui.set(cpu_model="base")
switch.changed()
check("an installed model is simply loaded", worker.wanted, ("cpu", ui.gpu_model, "base"))

for name, good, got, want in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}" + ("" if good else f": got {got!r}, want {want!r}"))
sys.exit(0 if all(c[1] for c in checks) else 1)

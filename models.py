"""Speech models for dictate: the ones on offer, which suits this computer, and downloading one.

recommend() is pure logic (tests/unit_models.py feeds it made-up computers). probe() asks psutil and
the GPU tools; download() needs huggingface_hub with HF_HUB_OFFLINE=0 when it is first imported.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from i18n import t

# name: (Hugging Face repository, download size in MB, what it is like). All multilingual.
MODELS = {
    "tiny": ("Systran/faster-whisper-tiny", 76, "fastest; rough English, no Slovak"),
    "base": ("Systran/faster-whisper-base", 145, "fast; good English, no Slovak"),
    "small": ("Systran/faster-whisper-small", 484, "very good English, poor Slovak"),
    "medium": ("Systran/faster-whisper-medium", 1528, "excellent English, fair Slovak; slow without a GPU"),
    "large-v3-turbo": ("dropbox-dash/faster-whisper-large-v3-turbo", 1620, "best, also for Slovak; slow without a GPU"),
    # Bigger and slower (32 decoder layers instead of 4); measured on an RTX 3060, FLEURS:
    "large-v3": ("Systran/faster-whisper-large-v3", 3090, "as accurate as large-v3-turbo, 1.5x slower; graphics card only"),
    "large-v2": ("Systran/faster-whisper-large-v2", 3090, "slightly better English, worse Slovak, 1.5x slower; graphics card only"),
}
FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
# Fine-tuned for Slovak by KInIT (Kempelen Institute of Intelligent Technologies), downloaded in Hugging
# Face's format at a pinned revision and converted on this computer (convert.py, no PyTorch needed).
# About a third of the Slovak errors (FLEURS: large-v3-turbo-sk 2.1 % against large-v3-turbo's 6.4 %;
# KInIT's Common Voice test: 9.3 % against 29.2 %), but they no longer understand English (70 % WER),
# so dictation uses one only for Slovak, next to the general model.
SLOVAK = {  # name: (repository, revision, download MB, size on disk MB, description)
    "large-v3-turbo-sk": ("kinit/whisper-large-v3-turbo-sk", "b83f859c3dd3d54066d3fa1f51b2a590bf1c3bf0", 3235, 1620,
                          "the most accurate; graphics card"),
    "medium-sk": ("kinit/whisper-medium-sk", "90c56408fdfea993f898d223e92e14d169d56829", 3055, 1530,
                  "very good; slow without a GPU"),
    "small-sk": ("kinit/whisper-small-sk", "6a8b78a7d3d28a17746478ee6e696a3946dd1ed3", 966, 484,
                 "good, and fast enough for a processor"),
    "base-sk": ("kinit/whisper-base-sk", "c178ecfc4d571f35915733fe48f32c4aa51cd591", 290, 145,
                "fast; makes more mistakes"),
}


# Qwen3-ASR (Alibaba's Qwen team), for English only: it has no Slovak (Slovak speech comes out as
# Czech, 62 % WER). On the 15 FLEURS sentences: 1.7B 2.9 % against large-v3-turbo's 3.9 %. It runs
# on PyTorch, which the program's environment doesn't have: choosing one installs a Qwen environment of
# its own (qwen-venv: about 4 GB to download with NVIDIA's libraries, 7 GB on disk) and runs it in a separate process
# (qwen_worker.py). NVIDIA graphics cards only: on a processor it takes as long as the speech itself.
QWEN = {  # name: (repository, revision, download MB, description)
    "qwen3-asr-1.7b": ("Qwen/Qwen3-ASR-1.7B", "7278e1e70fe206f11671096ffdd38061171dd6e5", 4703,
                       "the most accurate English"),
    "qwen3-asr-0.6b": ("Qwen/Qwen3-ASR-0.6B", "5eb144179a02acc5e5ba31e748d22b0cf3e303b0", 1881,
                       "smaller and faster, still very good English"),
}
QWEN_RUNTIME_MB = 4000  # PyTorch with CUDA and the rest: what installing the Qwen environment downloads
QWEN_TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
# Only what qwen-asr's inference imports (its own list adds a web demo: gradio, flask); its internals
# follow transformers closely, hence the exact versions it was released with.
QWEN_PACKAGES = ["transformers==4.57.6", "accelerate==1.12.0", "soundfile", "librosa", "nagisa==0.2.11"]
QWEN_ASR = "qwen-asr==0.0.6"


def qwen_venv(models_dir: Path) -> Path:
    return models_dir.parent / "qwen-venv"


def qwen_python(models_dir: Path) -> Path:
    venv = qwen_venv(models_dir)
    return venv / "Scripts" / "python.exe" if os.name == "nt" else venv / "bin" / "python"


def qwen_runtime_ready(models_dir: Path) -> bool:
    """Installed completely (the marker is written last)."""
    return (qwen_venv(models_dir) / "dictate-ready").is_file() and qwen_python(models_dir).is_file()


def install_qwen_runtime(models_dir: Path, base_python: str, run=subprocess.run) -> None:
    """The Qwen environment: a venv from the program's own Python, with PyTorch (CUDA) and the few
    packages qwen-asr needs, installed by the program's uv. Raises RuntimeError if a step fails."""
    app_dir = models_dir.parent
    uv = app_dir / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    venv, python = qwen_venv(models_dir), str(qwen_python(models_dir))
    env = {**os.environ, "UV_CACHE_DIR": str(app_dir / "cache" / "uv"), "UV_PYTHON_INSTALL_DIR": str(app_dir / "python"),
           "UV_NO_CONFIG": "1", "UV_PYTHON_DOWNLOADS": "never",
           "UV_HTTP_TIMEOUT": "300", "UV_HTTP_RETRIES": "5"}  # (NVIDIA's servers are slow at times; 30 s timed out)
    (venv / "dictate-ready").unlink(missing_ok=True)
    steps = [[str(uv), "venv", "--quiet", "--allow-existing", "--python", base_python, str(venv)],
             [str(uv), "pip", "install", "--quiet", "--python", python, "torch", "--index-url", QWEN_TORCH_INDEX],
             [str(uv), "pip", "install", "--quiet", "--python", python, *QWEN_PACKAGES],
             [str(uv), "pip", "install", "--quiet", "--python", python, "--no-deps", QWEN_ASR]]
    for command in steps:
        for attempt in (1, 2):  # (what was downloaded stays in uv's cache: a second try goes on from there)
            done = run(command, env=env, capture_output=True, text=True)
            if done.returncode == 0:
                break
        else:
            raise RuntimeError(f"installing the Qwen environment failed: {(done.stderr or done.stdout).strip()[-500:]}")
    run([str(uv), "cache", "clean"], env=env, capture_output=True, text=True)  # (a second copy of all of it)
    venv.mkdir(parents=True, exist_ok=True)
    (venv / "dictate-ready").write_text("qwen-asr " + QWEN_ASR.split("==")[1] + "\n", encoding="utf-8")


def slovak_for(model: str) -> str:
    """The Slovak model of about the same size and speed as a general model."""
    for prefix, slovak in (("large", "large-v3-turbo-sk"), ("medium", "medium-sk"), ("small", "small-sk")):
        if model.startswith(prefix):
            return slovak
    return "base-sk"


def size_label(name: str, with_runtime: bool = True) -> str:
    """What choosing the model downloads (a Slovak model: its Hugging Face files, about twice its size
    after the conversion; a Qwen model: also the Qwen environment, the first time)."""
    def label(mb):
        return f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{mb} MB"
    if name in QWEN:
        model = label(QWEN[name][2])
        return t("{model} + {software} of software", model=model, software=label(QWEN_RUNTIME_MB)) if with_runtime else model
    return label(MODELS[name][1] if name in MODELS else SLOVAK[name][2] if name in SLOVAK else 0)


def complete(path: Path, name: str) -> bool:
    """Every file a model from the list needs (large-v3 models also need 128 mel bins)."""
    if name in QWEN:
        return all((path / f).is_file() for f in ("config.json", "preprocessor_config.json", "vocab.json")) and \
            any(path.glob("*.safetensors"))
    needed = ["model.bin", "config.json", "tokenizer.json"] + (["preprocessor_config.json"]
                                                                if name.startswith("large-v3") else [])
    return all((path / f).is_file() for f in needed) and any(path.glob("vocabulary.*"))


def installed(models_dir: Path, name: str) -> bool:
    """On disk and complete? Without tokenizer.json faster-whisper would try to download one; a model
    of your own (a folder name not in MODELS) needs at least model.bin."""
    if name in QWEN:  # (also needs its environment)
        return complete(models_dir / name, name) and qwen_runtime_ready(models_dir)
    if name not in MODELS and name not in SLOVAK:
        return (models_dir / name / "model.bin").is_file()
    return complete(models_dir / name, name)


def download(models_dir: Path, name: str) -> Path:
    """Download into models/.partial-NAME (an interrupted download resumes there), check it, then
    move it into place, so a half-downloaded model is never used."""
    from huggingface_hub import snapshot_download
    final, partial = models_dir / name, models_dir / f".partial-{name}"
    models_dir.mkdir(parents=True, exist_ok=True)
    if name in SLOVAK:
        return download_slovak(models_dir, name)
    if name in QWEN:
        return download_qwen(models_dir, name)
    snapshot_download(MODELS[name][0], local_dir=partial, allow_patterns=FILES)
    if not complete(partial, name):
        raise RuntimeError(f"the download of {name} is incomplete")
    if final.exists():
        shutil.rmtree(final)
    partial.rename(final)
    return final


def download_slovak(models_dir: Path, name: str) -> Path:
    """KInIT's files at the pinned revision into models/.partial-NAME-hf (resumable), converted into
    models/.partial-NAME, checked, moved into place; then the Hugging Face files go."""
    from huggingface_hub import snapshot_download

    import convert
    repo, revision, _download_mb, _mb, _info = SLOVAK[name]
    final, partial, source = models_dir / name, models_dir / f".partial-{name}", models_dir / f".partial-{name}-hf"
    snapshot_download(repo, revision=revision, local_dir=source,
                      allow_patterns=["*.safetensors", "model.safetensors.index.json", "config.json",
                                      "generation_config.json", "tokenizer.json", "preprocessor_config.json"])
    shutil.rmtree(partial, ignore_errors=True)
    convert.convert(source, partial)
    if not complete(partial, name):
        raise RuntimeError(f"the conversion of {name} is incomplete")
    if final.exists():
        shutil.rmtree(final)
    partial.rename(final)
    shutil.rmtree(source, ignore_errors=True)
    return final


def download_qwen(models_dir: Path, name: str) -> Path:
    """The Qwen environment (once), then the model at its pinned revision, checked, moved into place."""
    import sys

    from huggingface_hub import snapshot_download
    if not qwen_runtime_ready(models_dir):
        install_qwen_runtime(models_dir, getattr(sys, "_base_executable", sys.executable))
    repo, revision, _mb, _info = QWEN[name]
    final, partial = models_dir / name, models_dir / f".partial-{name}"
    snapshot_download(repo, revision=revision, local_dir=partial, allow_patterns=["*.json", "*.safetensors", "*.txt"])
    if not complete(partial, name):
        raise RuntimeError(f"the download of {name} is incomplete")
    if final.exists():
        shutil.rmtree(final)
    partial.rename(final)
    return final


def partial_bytes(models_dir: Path, name: str) -> int:
    """How much of a running download is on disk (for a progress percentage)."""
    total = 0
    folders = [models_dir / f".partial-{name}", models_dir / f".partial-{name}-hf"]  # (Slovak: download, conversion)
    if name in QWEN and not qwen_runtime_ready(models_dir):
        folders.append(qwen_venv(models_dir))  # (its environment fills first)
    for folder in folders:
        for path in folder.rglob("*"):
            try:
                total += path.stat().st_size if path.is_file() else 0
            except OSError:
                pass
    return total


@dataclass
class Hardware:
    gpu: str | None  # "nvidia" or "amd" when this install can use that GPU, else None
    gpu_name: str = ""
    vram_gb: float = 0.0  # 0 = unknown
    cores: int = 2  # physical CPU cores
    ram_gb: float = 8.0
    laptop: bool = False  # has a battery: probably no numpad

    def describe(self) -> str:
        gpu = (f"{self.gpu_name or self.gpu.upper() + ' GPU'}" + (f" ({self.vram_gb:.0f} GB)" if self.vram_gb else "")
               if self.gpu else t("no usable GPU"))
        return (t("{gpu}; {cores}-core CPU; {ram} GB RAM", gpu=gpu, cores=self.cores, ram=f"{self.ram_gb:.0f}")
                + ("; " + t("laptop") if self.laptop else ""))


def has_system_battery(power_supply: Path = Path("/sys/class/power_supply")) -> bool:
    """Linux: a battery that powers the computer. psutil also counts a wireless mouse's or headset's
    ("hidpp_battery_0"), which the kernel marks with scope "Device"."""
    def read(path: Path) -> str:
        try:
            return path.read_text().strip()
        except OSError:
            return ""
    return any(read(p / "type") == "Battery" and read(p / "scope") != "Device" for p in power_supply.iterdir())


def probe(gpu: str | None) -> Hardware:
    """This computer. `gpu` is what the installer found ("nvidia", "amd" or None)."""
    try:
        import psutil
        cores = psutil.cpu_count(logical=False) or 0
        ram = psutil.virtual_memory().total / 2**30
        laptop = has_system_battery() if Path("/sys/class/power_supply").is_dir() else psutil.sensors_battery() is not None
    except Exception:
        cores, ram, laptop = 0, 8.0, False
    hw = Hardware(gpu, cores=cores or max(1, (os.cpu_count() or 2) // 2), ram_gb=ram, laptop=laptop)
    if gpu == "nvidia":
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=10).stdout.splitlines()
            name, mib = out[0].rsplit(",", 1)
            hw.gpu_name, hw.vram_gb = name.strip(), int(mib) / 1024
        except Exception:
            pass
    elif gpu == "amd":
        for vram in sorted(Path("/sys/class/drm").glob("card*/device/mem_info_vram_total")):
            try:
                hw.vram_gb = max(hw.vram_gb, int(vram.read_text()) / 2**30)
            except (OSError, ValueError):
                pass
    return hw


def cpu_model_for(hw: Hardware, language: str) -> str:
    """Whisper always works on 30 s at a time, so a pass costs about the same for any short dictation.
    Measured on 4 cores (FLEURS, int8): base 0.7 s, small ~2 s, large-v3-turbo ~4 s per sentence.
    English is fine from base up (5.5 % word errors; small 4.5 %); in Slovak tiny and base are
    unusable, small gets a third of the words wrong and large-v3-turbo 7 %, so with 4 cores and the
    memory for it Slovak gets turbo, and small elsewhere (twice as slow on 2 cores)."""
    if hw.ram_gb < 2.5:
        return "base" if hw.ram_gb >= 1.5 else "tiny"
    if language in ("sk", "auto"):
        return "large-v3-turbo" if hw.cores >= 4 and hw.ram_gb >= 6 else "small"
    return "small" if hw.cores >= 4 else "base"


def recommend(hw: Hardware, language: str) -> tuple[str, str | None, str]:
    """(device, model for the GPU or None, model for the CPU). medium is never suggested:
    large-v3-turbo is as fast on a processor, faster on a GPU (its decoder has 4 layers), and much
    better in Slovak (7 % word errors against 15 %)."""
    cpu = cpu_model_for(hw, language)
    if hw.gpu and (hw.vram_gb == 0 or hw.vram_gb >= 2):  # 2-4 GB still fits turbo in int8_float16
        return "gpu", "large-v3-turbo", cpu
    return "cpu", None, cpu


def cpu_threads() -> int:
    """Physical cores, at most 8: hyper-threads and more threads than cores only slow int8 matrix maths."""
    try:
        import psutil
        cores = psutil.cpu_count(logical=False)
    except Exception:
        cores = None
    return max(1, min(8, cores or (os.cpu_count() or 2) // 2))

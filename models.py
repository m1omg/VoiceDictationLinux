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

# name: (Hugging Face repository, download size in MB, what it is like). All multilingual.
MODELS = {
    "tiny": ("Systran/faster-whisper-tiny", 76, "fastest; rough English, no Slovak"),
    "base": ("Systran/faster-whisper-base", 145, "fast; good English, no Slovak"),
    "small": ("Systran/faster-whisper-small", 484, "very good English, poor Slovak"),
    "medium": ("Systran/faster-whisper-medium", 1528, "excellent English, fair Slovak; slow without a GPU"),
    "large-v3-turbo": ("mobiuslabsgmbh/faster-whisper-large-v3-turbo", 1620, "best, also for Slovak; slow without a GPU"),
}
FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]


def size_label(name: str) -> str:
    mb = MODELS[name][1] if name in MODELS else 0
    return f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{mb} MB"


def complete(path: Path, name: str) -> bool:
    """Every file a model from the list needs (large-v3 models also need 128 mel bins)."""
    needed = ["model.bin", "config.json", "tokenizer.json"] + (["preprocessor_config.json"]
                                                                if name.startswith("large-v3") else [])
    return all((path / f).is_file() for f in needed) and any(path.glob("vocabulary.*"))


def installed(models_dir: Path, name: str) -> bool:
    """On disk and complete? Without tokenizer.json faster-whisper would try to download one; a model
    of your own (a folder name not in MODELS) needs at least model.bin."""
    if name not in MODELS:
        return (models_dir / name / "model.bin").is_file()
    return complete(models_dir / name, name)


def download(models_dir: Path, name: str) -> Path:
    """Download into models/.partial-NAME (an interrupted download resumes there), check it, then
    move it into place, so a half-downloaded model is never used."""
    from huggingface_hub import snapshot_download
    final, partial = models_dir / name, models_dir / f".partial-{name}"
    models_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(MODELS[name][0], local_dir=partial, allow_patterns=FILES)
    if not complete(partial, name):
        raise RuntimeError(f"the download of {name} is incomplete")
    if final.exists():
        shutil.rmtree(final)
    partial.rename(final)
    return final


def partial_bytes(models_dir: Path, name: str) -> int:
    """How much of a running download is on disk (for a progress percentage)."""
    total = 0
    for path in (models_dir / f".partial-{name}").rglob("*"):
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
               if self.gpu else "no usable GPU")
        return f"{gpu}; {self.cores}-core CPU; {self.ram_gb:.0f} GB RAM" + ("; laptop" if self.laptop else "")


def probe(gpu: str | None) -> Hardware:
    """This computer. `gpu` is what the installer found ("nvidia", "amd" or None)."""
    try:
        import psutil
        cores = psutil.cpu_count(logical=False) or 0
        ram = psutil.virtual_memory().total / 2**30
        laptop = psutil.sensors_battery() is not None
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

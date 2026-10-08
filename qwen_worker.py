"""Qwen3-ASR in a process of its own, for English: run by the Python of the separate Qwen environment
(models.qwen_python(): PyTorch and transformers, which the program's own environment doesn't have),
and kept running by dictate while the model is chosen.

    qwen-venv/bin/python qwen_worker.py MODEL_FOLDER [--home=ram|drive]

Protocol, on stdin and stdout (anything the libraries print goes to stderr):
  started:  {"ready": true, "device": "cuda:0/bfloat16"}  or  {"error": "..."}
  request:  {"samples": N, "language": null, "context": "words to spell like this"}, a newline,
            then N little-endian float32 samples at 16 kHz
  answer:   {"text": "...", "language": "English"}  or  {"error": "..."}
  request:  {"command": "park"}: free the graphics card. The weights wait in RAM (--home=ram: one
            pinned copy, back on the card in ~0.2 s) or only in the model's files (--home=drive: the
            disk cache, which the system frees when it needs the memory; back in ~1 s)
  request:  {"command": "wake"}: back to the graphics card (a pass while parked does this first)
  answer:   {"ok": true}  or  {"error": "..."}
It ends when stdin closes (dictation ended or chose another model).
"""
from __future__ import annotations

import ctypes
import gc
import json
import os
import sys

RATE = 16000


class Mover:
    """Moves the model's weights between the graphics card and their home in system memory. The home
    copy never changes, so parking only points the model back at it (nothing is copied)."""

    def __init__(self, module, device: str, dtype, in_ram: bool):
        import torch
        self.torch, self.device, self.on_card = torch, device, False
        tensors = list({id(t): t for t in [*module.parameters(), *module.buffers()]}.values())

        def on_card_as(t):  # the files hold bfloat16; a card without it gets float16
            return dtype if t.dtype == torch.bfloat16 else t.dtype

        if not in_ram:  # the tensors from_pretrained mapped from the files: no copy of their own
            self.home = [(t, t.data, on_card_as(t)) for t in tensors]
            return
        # One pinned block for all (PyTorch rounds every pinned allocation up to a power of two):
        # pinned memory copies to the card about 4x as fast as the files' pages.
        sizes = [-(-t.numel() * t.element_size() // 256) * 256 for t in tensors]
        try:
            block = torch.empty(sum(sizes), dtype=torch.uint8, pin_memory=True)
        except RuntimeError:  # no memory can be pinned: ordinary RAM, slower to copy
            block = torch.empty(sum(sizes), dtype=torch.uint8)
        self.home, offset = [], 0
        for t, size in zip(tensors, sizes):
            kind = on_card_as(t)
            view = block[offset:offset + t.numel() * t.element_size()].view(kind).view(t.shape)
            view.copy_(t.data)
            t.data = view
            self.home.append((t, view, kind))
            offset += size
        gc.collect()  # the mapped originals go
        if sys.platform.startswith("linux"):
            try:
                ctypes.CDLL(None).malloc_trim(0)
            except (OSError, AttributeError):
                pass

    def wake(self) -> None:
        if self.on_card:
            return
        self.on_card = True  # (also if it fails half-way: park() then puts every tensor back)
        try:
            for t, home, kind in self.home:
                t.data = home.to(self.device, dtype=kind, non_blocking=True)
            self.torch.cuda.synchronize()
        except Exception:
            self.park()
            raise

    def park(self) -> None:
        if not self.on_card:
            return
        for t, home, _kind in self.home:
            t.data = home
        self.on_card = False
        self.torch.cuda.empty_cache()  # (0.1 s; a gc.collect() first freed nothing more and took 0.18 s)


def main() -> int:
    protocol = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(2, 1)  # (stray prints from the libraries must not end up in the answers)

    def say(message: dict) -> None:
        protocol.write(json.dumps(message, ensure_ascii=False) + "\n")
        protocol.flush()

    try:
        import numpy as np
        import torch
        from qwen_asr import Qwen3ASRModel
        from transformers.utils import logging as transformers_logging
        transformers_logging.set_verbosity_error()  # (not "Setting pad_token_id ..." for every pass)
        mover = None
        if torch.cuda.is_available():
            device = "cuda:0"
            dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            # Into system memory first, then onto the card: loaded onto the card directly, transformers
            # reserves one block for all the weights, which PyTorch can't give back (3.3 GB stayed).
            model = Qwen3ASRModel.from_pretrained(sys.argv[1], dtype=torch.bfloat16, device_map="cpu",
                                                  max_inference_batch_size=1, max_new_tokens=1024)
            mover = Mover(model.model, device, dtype, "--home=ram" in sys.argv[2:])
            mover.wake()
        else:  # (works, but slowly: about 9 s for a 9 s sentence on a 6-core processor)
            device, dtype = "cpu", torch.float32
            torch.set_num_threads(max(1, int(os.environ.get("DICTATE_QWEN_THREADS", os.cpu_count() or 2))))
            model = Qwen3ASRModel.from_pretrained(sys.argv[1], dtype=dtype, device_map="cpu",
                                                  max_inference_batch_size=1, max_new_tokens=1024)
        model.transcribe(audio=(np.zeros(RATE, dtype=np.float32), RATE), language="English")  # warm-up
        if mover:
            torch.cuda.empty_cache()
    except Exception as e:
        say({"error": f"{type(e).__name__}: {e}"})
        return 1
    say({"ready": True, "device": f"{device}/{str(dtype).replace('torch.', '')}"})
    stdin = sys.stdin.buffer
    for line in iter(stdin.readline, b""):
        try:
            request = json.loads(line)
            command = request.get("command")
            if command in ("park", "wake"):
                if mover:
                    (mover.park if command == "park" else mover.wake)()
                say({"ok": True})
                continue
            data = stdin.read(4 * int(request["samples"]))
            audio = np.frombuffer(data, dtype="<f4").astype(np.float32)
            if mover:
                mover.wake()  # (there already, unless dictation parked it)
            result = model.transcribe(audio=(audio, RATE), language=request.get("language") or None,
                                      context=request.get("context") or "")[0]
            say({"text": result.text, "language": result.language})
        except Exception as e:  # one failed request: dictation falls back to Whisper for it
            say({"error": f"{type(e).__name__}: {e}"})
        if mover and mover.on_card:  # what PyTorch keeps for the next pass goes back to games and the rest
            torch.cuda.empty_cache()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Qwen3-ASR in a process of its own, for English: run by the Python of the separate Qwen environment
(models.qwen_python(): PyTorch and transformers, which the program's own environment doesn't have),
and kept running by dictate while the model is chosen.

    qwen-venv/bin/python qwen_worker.py MODEL_FOLDER

Protocol, on stdin and stdout (anything the libraries print goes to stderr):
  started:  {"ready": true, "device": "cuda:0/bfloat16"}  or  {"error": "..."}
  request:  {"samples": N, "language": "English", "context": "words to spell like this"}, a newline,
            then N little-endian float32 samples at 16 kHz
  answer:   {"text": "...", "language": "English"}  or  {"error": "..."}
It ends when stdin closes (dictation ended or chose another model).
"""
from __future__ import annotations

import json
import os
import sys

RATE = 16000


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
        if torch.cuda.is_available():
            device = "cuda:0"
            dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        else:  # (works, but slowly: about 9 s for a 9 s sentence on a 6-core processor)
            device, dtype = "cpu", torch.float32
            torch.set_num_threads(max(1, int(os.environ.get("DICTATE_QWEN_THREADS", os.cpu_count() or 2))))
        model = Qwen3ASRModel.from_pretrained(sys.argv[1], dtype=dtype, device_map=device, max_inference_batch_size=1,
                                              max_new_tokens=1024)
        model.transcribe(audio=(np.zeros(RATE, dtype=np.float32), RATE), language="English")  # warm-up
        if device != "cpu":
            torch.cuda.empty_cache()
    except Exception as e:
        say({"error": f"{type(e).__name__}: {e}"})
        return 1
    say({"ready": True, "device": f"{device}/{str(dtype).replace('torch.', '')}"})
    stdin = sys.stdin.buffer
    for line in iter(stdin.readline, b""):
        try:
            request = json.loads(line)
            data = stdin.read(4 * int(request["samples"]))
            audio = np.frombuffer(data, dtype="<f4").astype(np.float32)
            result = model.transcribe(audio=(audio, RATE), language=request.get("language") or None,
                                      context=request.get("context") or "")[0]
            say({"text": result.text, "language": result.language})
        except Exception as e:  # one failed pass: dictation falls back to Whisper for it
            say({"error": f"{type(e).__name__}: {e}"})
        if device != "cpu":  # what PyTorch keeps for the next pass goes back to games and the rest
            torch.cuda.empty_cache()
    return 0


if __name__ == "__main__":
    sys.exit(main())

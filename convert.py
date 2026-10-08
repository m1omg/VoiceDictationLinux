"""Whisper models in Hugging Face's format (safetensors, e.g. KInIT's Slovak fine-tunes) converted to
CTranslate2's, which faster-whisper loads: the same as `ct2-transformers-converter --quantization
float16 --copy_files tokenizer.json preprocessor_config.json`, but without PyTorch or transformers
(a multi-gigabyte download for a one-off conversion). It fills CTranslate2's own WhisperSpec the way
its converter does (ctranslate2/converters/transformers.py: WhisperLoader); tests/unit_convert.py
checks the result against that converter's.

    python convert.py HF_FOLDER CT2_FOLDER
"""
from __future__ import annotations

import json
import shutil
import struct
import sys
from pathlib import Path

import numpy as np

COPY = ("tokenizer.json", "preprocessor_config.json")


def read_safetensors(folder: Path) -> dict[str, np.ndarray]:
    """Every tensor of the model's safetensors file(s), as numpy arrays (mapped, not read into memory)."""
    index = folder / "model.safetensors.index.json"
    files = (sorted(set(json.loads(index.read_text(encoding="utf-8"))["weight_map"].values())) if index.exists()
             else ["model.safetensors"])
    tensors = {}
    for name in files:
        path = folder / name
        with open(path, "rb") as f:
            size = struct.unpack("<Q", f.read(8))[0]
            header = json.loads(f.read(size))
        data = np.memmap(path, dtype=np.uint8, mode="r", offset=8 + size)
        for key, info in header.items():
            if key == "__metadata__":
                continue
            begin, end = info["data_offsets"]
            raw = data[begin:end]
            if info["dtype"] == "F32":
                array = raw.view(np.float32)
            elif info["dtype"] == "F16":
                array = raw.view(np.float16)
            elif info["dtype"] == "BF16":  # bfloat16 is the top half of a float32
                array = (raw.view(np.uint16).astype(np.uint32) << 16).view(np.float32)
            else:
                raise ValueError(f"{key}: tensors of type {info['dtype']} aren't expected in a Whisper model")
            tensors[key] = array.reshape(info["shape"])
    return tensors


def vocabulary(folder: Path, vocab_size: int) -> list[str]:
    """The tokens by id (tokenizer.json's vocabulary and added tokens), with Whisper's timestamp tokens
    filling up to vocab_size, as WhisperLoader.get_vocabulary does."""
    data = json.loads((folder / "tokenizer.json").read_text(encoding="utf-8"))
    by_id = {index: token for token, index in data["model"]["vocab"].items()}
    by_id.update({added["id"]: added["content"] for added in data.get("added_tokens", [])})
    tokens = [by_id[i] for i in sorted(by_id)]
    if sorted(by_id) != list(range(len(tokens))):
        raise ValueError("the tokenizer's ids have gaps")
    tokens = tokens[:vocab_size]
    tokens.extend("<|%.2f|>" % (i * 0.02) for i in range(vocab_size - len(tokens)))
    return tokens


def convert(src: Path, dst: Path, quantization: str = "float16") -> None:
    from ctranslate2.converters import utils
    from ctranslate2.specs import common_spec, whisper_spec

    config = json.loads((src / "config.json").read_text(encoding="utf-8"))
    generation = json.loads((src / "generation_config.json").read_text(encoding="utf-8"))
    weights = read_safetensors(src)

    def w(name):
        return weights[name]

    def linear(spec, prefix, bias=True):
        spec.weight = w(f"{prefix}.weight")
        if bias and f"{prefix}.bias" in weights:
            spec.bias = w(f"{prefix}.bias")

    def layer_norm(spec, prefix):
        spec.gamma, spec.beta = w(f"{prefix}.weight"), w(f"{prefix}.bias")

    def attention(spec, prefix, self_attention):
        q, k, v = (common_spec.LinearSpec() for _ in range(3))
        linear(q, f"{prefix}.q_proj")
        linear(k, f"{prefix}.k_proj")
        linear(v, f"{prefix}.v_proj")
        if self_attention:
            utils.fuse_linear(spec.linear[0], [q, k, v])
        else:
            utils.fuse_linear(spec.linear[0], [q])
            utils.fuse_linear(spec.linear[1], [k, v])
        linear(spec.linear[-1], f"{prefix}.out_proj")

    def layers(spec, prefix, cross):
        for i, layer in enumerate(spec.layer):
            p = f"{prefix}.layers.{i}"
            attention(layer.self_attention, f"{p}.self_attn", True)
            layer_norm(layer.self_attention.layer_norm, f"{p}.self_attn_layer_norm")
            if cross:
                attention(layer.attention, f"{p}.encoder_attn", False)
                layer_norm(layer.attention.layer_norm, f"{p}.encoder_attn_layer_norm")
            linear(layer.ffn.linear_0, f"{p}.fc1")
            linear(layer.ffn.linear_1, f"{p}.fc2")
            layer_norm(layer.ffn.layer_norm, f"{p}.final_layer_norm")

    spec = whisper_spec.WhisperSpec(config["encoder_layers"], config["encoder_attention_heads"],
                                    config["decoder_layers"], config["decoder_attention_heads"])
    enc, dec = spec.encoder, spec.decoder
    for conv in ("conv1", "conv2"):
        getattr(enc, conv).weight = w(f"model.encoder.{conv}.weight")
        getattr(enc, conv).bias = w(f"model.encoder.{conv}.bias")
    enc.position_encodings.encodings = w("model.encoder.embed_positions.weight")
    layer_norm(enc.layer_norm, "model.encoder.layer_norm")
    layers(enc, "model.encoder", cross=False)
    dec.embeddings.weight = w("model.decoder.embed_tokens.weight")
    dec.position_encodings.encodings = w("model.decoder.embed_positions.weight")
    layer_norm(dec.layer_norm, "model.decoder.layer_norm")
    layers(dec, "model.decoder", cross=True)
    # The output projection shares the embeddings' weights (unless the model has its own).
    dec.projection.weight = weights.get("proj_out.weight", w("model.decoder.embed_tokens.weight"))

    spec.config.suppress_ids = generation["suppress_tokens"]
    spec.config.suppress_ids_begin = generation["begin_suppress_tokens"]
    if "alignment_heads" in generation:
        spec.config.alignment_heads = generation["alignment_heads"]
    else:  # (as WhisperLoader: the last half of the decoder's layers)
        spec.config.alignment_heads = [[layer, head] for layer in range(config["decoder_layers"] // 2, config["decoder_layers"])
                                       for head in range(config["decoder_attention_heads"])]
    spec.config.lang_ids = sorted(generation["lang_to_id"].values())
    spec.register_vocabulary(vocabulary(src, config["vocab_size"]))

    spec.validate()
    spec.optimize(quantization=quantization)
    dst.mkdir(parents=True, exist_ok=True)
    spec.save(str(dst))
    for name in COPY:
        shutil.copy2(src / name, dst / name)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    convert(Path(sys.argv[1]), Path(sys.argv[2]))

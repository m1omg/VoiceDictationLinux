"""convert.py, the Hugging Face → CTranslate2 conversion of the Slovak models, on a tiny Whisper-shaped
model made of random numbers: CTranslate2 loads the result and runs it; safetensors files in float16,
bfloat16 and in two parts read back the same; Whisper's timestamp tokens fill up the vocabulary; and
models.download_slovak() goes from the downloaded files to an installed model (with a stand-in for
the download). The real models were checked against CTranslate2's own converter (PyTorch-based):
byte for byte the same model.bin (kinit/whisper-large-v3-turbo-sk, 2026-10-08).

    python3 tests/unit_convert.py        (the program's Python: numpy and ctranslate2)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="dictate-test-"))
os.environ.update(XDG_STATE_HOME=str(TMP / "state"), XDG_CONFIG_HOME=str(TMP / "config"), XDG_RUNTIME_DIR=str(TMP),
                  LOCALAPPDATA=str(TMP), HOME=str(TMP), USERPROFILE=str(TMP))
import convert  # noqa: E402
import models  # noqa: E402

checks = []


def check(name, got, want):
    checks.append((name, got == want, got, want))


def write_safetensors(path: Path, tensors: dict, dtype: str = "F32") -> None:
    header, blobs, offset = {}, [], 0
    for name, array in tensors.items():
        if dtype == "F16":
            data = array.astype(np.float16).tobytes()
        elif dtype == "BF16":
            data = (array.astype(np.float32).view(np.uint32) >> 16).astype(np.uint16).tobytes()
        else:
            data = array.astype(np.float32).tobytes()
        header[name] = {"dtype": dtype, "shape": list(array.shape), "data_offsets": [offset, offset + len(data)]}
        blobs.append(data)
        offset += len(data)
    raw = json.dumps(header).encode()
    path.write_bytes(len(raw).to_bytes(8, "little") + raw + b"".join(blobs))


# A Whisper model: one layer each, 8 wide, 80 mel bins, 1500 positions (30 s), and its tokenizer.
D, FFN, MELS = 8, 16, 80
rng = np.random.default_rng(0)
SPECIAL = ["<|endoftext|>", "<|startoftranscript|>", "<|en|>", "<|sk|>", "<|translate|>", "<|transcribe|>",
           "<|startoflm|>", "<|startofprev|>", "<|nocaptions|>", "<|notimestamps|>"]
WORDS = ["a", "b", "c", "d", "e", "f"]
VOCAB = len(WORDS) + len(SPECIAL) + 3  # three timestamp tokens, which the tokenizer doesn't list


def r(*shape):
    return rng.standard_normal(shape).astype(np.float32) * 0.1


def layer(prefix, cross):
    t = {}
    for attention in ("self_attn", "encoder_attn") if cross else ("self_attn",):
        for proj in ("q_proj", "k_proj", "v_proj", "out_proj"):
            t[f"{prefix}.{attention}.{proj}.weight"] = r(D, D)
            if proj != "k_proj":  # (Whisper's key projections have no bias)
                t[f"{prefix}.{attention}.{proj}.bias"] = r(D)
        t[f"{prefix}.{attention}_layer_norm.weight"], t[f"{prefix}.{attention}_layer_norm.bias"] = 1 + r(D), r(D)
    t[f"{prefix}.fc1.weight"], t[f"{prefix}.fc1.bias"] = r(FFN, D), r(FFN)
    t[f"{prefix}.fc2.weight"], t[f"{prefix}.fc2.bias"] = r(D, FFN), r(D)
    t[f"{prefix}.final_layer_norm.weight"], t[f"{prefix}.final_layer_norm.bias"] = 1 + r(D), r(D)
    return t


tensors = {"model.encoder.conv1.weight": r(D, MELS, 3), "model.encoder.conv1.bias": r(D),
           "model.encoder.conv2.weight": r(D, D, 3), "model.encoder.conv2.bias": r(D),
           "model.encoder.embed_positions.weight": r(1500, D),
           "model.encoder.layer_norm.weight": 1 + r(D), "model.encoder.layer_norm.bias": r(D),
           "model.decoder.embed_tokens.weight": r(VOCAB, D), "model.decoder.embed_positions.weight": r(448, D),
           "model.decoder.layer_norm.weight": 1 + r(D), "model.decoder.layer_norm.bias": r(D),
           **layer("model.encoder.layers.0", False), **layer("model.decoder.layers.0", True)}
hf = TMP / "hf"
hf.mkdir()
write_safetensors(hf / "model.safetensors", tensors)
(hf / "config.json").write_text(json.dumps({"encoder_layers": 1, "encoder_attention_heads": 2, "decoder_layers": 1,
                                            "decoder_attention_heads": 2, "vocab_size": VOCAB, "num_mel_bins": MELS}))
ids = {token: i for i, token in enumerate(WORDS + SPECIAL)}
(hf / "generation_config.json").write_text(json.dumps({
    "suppress_tokens": [ids["a"]], "begin_suppress_tokens": [ids["<|endoftext|>"]], "alignment_heads": [[0, 1]],
    "lang_to_id": {"<|sk|>": ids["<|sk|>"], "<|en|>": ids["<|en|>"]}}))
(hf / "tokenizer.json").write_text(json.dumps({
    "model": {"vocab": {w: ids[w] for w in WORDS}},
    "added_tokens": [{"id": ids[token], "content": token} for token in SPECIAL]}))
(hf / "preprocessor_config.json").write_text(json.dumps({"feature_size": MELS}))

# --- the conversion ---
out = TMP / "ct2"
convert.convert(hf, out)
check("the files faster-whisper needs", sorted(p.name for p in out.iterdir()),
      ["config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"])
vocabulary = json.loads((out / "vocabulary.json").read_text())
check("the vocabulary by id, filled up with Whisper's timestamp tokens",
      (len(vocabulary), vocabulary[:2], vocabulary[ids["<|sk|>"]], vocabulary[-3:]),
      (VOCAB, ["a", "b"], "<|sk|>", ["<|0.00|>", "<|0.02|>", "<|0.04|>"]))
config = json.loads((out / "config.json").read_text())
check("its settings: suppressed tokens, alignment heads, languages (sorted by id)",
      (config["suppress_ids"], config["suppress_ids_begin"], config["alignment_heads"], config["lang_ids"]),
      ([ids["a"]], [ids["<|endoftext|>"]], [[0, 1]], sorted([ids["<|en|>"], ids["<|sk|>"]])))
import ctranslate2  # noqa: E402

whisper = ctranslate2.models.Whisper(str(out), device="cpu", compute_type="float32")
features = ctranslate2.StorageView.from_array(rng.standard_normal((1, MELS, 3000)).astype(np.float32))
encoded = np.array(whisper.encode(features))
check("CTranslate2 loads it and encodes 30 s of audio", encoded.shape, (1, 1500, D))
result = whisper.generate(features, [["<|startoftranscript|>", "<|sk|>", "<|transcribe|>", "<|notimestamps|>"]],
                          max_length=6, beam_size=1)
check("and decodes (random weights, random tokens, but in its vocabulary)",
      all(token in vocabulary for token in result[0].sequences[0]), True)

# --- other ways the safetensors come ---
small = {"x": r(3, 4), "y": r(5)}
for dtype, tolerance in (("F16", 1e-3), ("BF16", 1e-2)):
    folder = TMP / dtype
    folder.mkdir()
    write_safetensors(folder / "model.safetensors", small, dtype)
    back = convert.read_safetensors(folder)
    check(f"{dtype} tensors read back (as float32 where needed)",
          all(np.allclose(back[k].astype(np.float32), small[k], atol=tolerance) for k in small), True)
shards = TMP / "shards"
shards.mkdir()
write_safetensors(shards / "model-00001-of-00002.safetensors", {"x": small["x"]})
write_safetensors(shards / "model-00002-of-00002.safetensors", {"y": small["y"]})
(shards / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {
    "x": "model-00001-of-00002.safetensors", "y": "model-00002-of-00002.safetensors"}}))
back = convert.read_safetensors(shards)
check("a model in two files (large-v3-sk)", all(np.array_equal(back[k], small[k]) for k in small), True)

# --- the download: KInIT's files, converted, checked, moved into place ---
import huggingface_hub  # noqa: E402
import shutil  # noqa: E402

asked = []


def stand_in_download(repo, revision=None, local_dir=None, allow_patterns=None):
    asked.append((repo, revision))
    shutil.copytree(hf, local_dir, dirs_exist_ok=True)
    return local_dir


huggingface_hub.snapshot_download = stand_in_download
models_dir = TMP / "models"
models_dir.mkdir()
final = models.download(models_dir, "base-sk")
check("KInIT's repository at the pinned revision", asked, [("kinit/whisper-base-sk", models.SLOVAK["base-sk"][1])])
check("converted and installed; the downloaded files are gone",
      (models.installed(models_dir, "base-sk"), final == models_dir / "base-sk", sorted(p.name for p in models_dir.iterdir())),
      (True, True, ["base-sk"]))
check("a Slovak model of the same size as a general one", [models.slovak_for(m) for m in
                                                           ("large-v3-turbo", "large-v3", "medium", "small", "small.en", "base", "tiny")],
      ["large-v3-turbo-sk", "large-v3-turbo-sk", "medium-sk", "small-sk", "small-sk", "base-sk", "base-sk"])
check("its size is what it downloads", models.size_label("large-v3-turbo-sk"), "3.2 GB")

# --- dictation: Slovak after release = the Slovak model's words formatted by the general model ---
import dictate as d  # noqa: E402

calls = []


class StandInModel:
    def __init__(self, name, text):
        self.name, self.text = name, text

    def transcribe(self, audio, language=None, beam_size=None, vad_filter=None, condition_on_previous_text=None,
                   without_timestamps=None, word_timestamps=None, hotwords=None, initial_prompt=None, batch_size=None):
        calls.append((self.name, language, word_timestamps))
        return iter([types.SimpleNamespace(text=self.text)]), None


import types  # noqa: E402

tr = d.Transcriber(d.load_config())
general = StandInModel("general", "Toto zajistuje, aby obraz pokrýval celú obrazovku. Hovorí sa tomu DVD.")
tr.model, tr.batched = general, general
tr.slovak = tr.slovak_batched = StandInModel("Slovak", "toto zaisťuje aby obraz pokrýval celú obrazovku hovorí sa tomu dvd")
second, long = np.zeros(d.RATE, np.float32), np.zeros(31 * d.RATE, np.float32)
check("Slovak after release: the Slovak model's words, the general model's punctuation and capitals",
      [seg.text for seg in tr.run(second, "sk")], ["Toto zaisťuje, aby obraz pokrýval celú obrazovku. Hovorí sa tomu DVD."])
check("(both models ran)", calls, [("general", "sk", False), ("Slovak", "sk", False)])
calls.clear()
tr.run(second, "sk", words=True)
tr.run(second, "en")
check("live passes (word timings) and English: the general model alone", calls, [("general", "sk", True), ("general", "en", False)])
calls.clear()
tr.slovak = tr.slovak_batched = None
tr.run(long, "sk")
check("without a Slovak model, the general one does Slovak", calls, [("general", "sk", False)])

# The transfer itself.
T = d.transfer_format
check("same words: the styled text as it is", T("Áno, presne tak.", "áno presne tak"), "Áno, presne tak.")
check("a different word takes the capitals and punctuation of the one it replaces",
      T("Videl som Bratislavu, potom domov.", "videl som bratislavou potom domov"), "Videl som Bratislavou, potom domov.")
check("an acronym stays in capitals", T("Formát DVD je známy.", "formát dvd je známy"), "Formát DVD je známy.")
check("an extra word comes as it is", T("Je to dobré.", "je to veľmi dobré"), "Je to veľmi dobré.")
check("a missing word leaves its sentence end", T("Je to tak. Áno.", "je to"), "Je to.")
check("two words for one: the punctuation goes after the last", T("Prišiel domov.", "prišiel do mov"), "Prišiel do mov.")
check("a sentence after a full stop starts with a capital", T("Toto. Áno.", "toto nie"), "Toto. Nie.")
check("nothing styled: the plain words, the first one capitalized", T("", "ahoj svet"), "Ahoj svet")

for name, good, got, want in checks:
    print(f"{'ok  ' if good else 'FAIL'} {name}" + ("" if good else f": got {got!r}, want {want!r}"))
sys.exit(0 if all(c[1] for c in checks) else 1)

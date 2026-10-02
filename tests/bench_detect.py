"""Language detection accuracy versus how much speech it has heard.

Reference result: English is recognised from 0.5 s of speech; Slovak is confidently mistaken
for English below ~1.5 s and always right from 1.5 s on. That is why auto mode waits for 1.5 s
of speech (dictate.py, Worker._live).
"""
from _common import audio, load_dictate, sentences
from faster_whisper.vad import VadOptions, get_speech_timestamps

d = load_dictate()
tr = d.Transcriber(d.load_config())
tr.load()
for lang in ("en", "sk"):
    results = {s: [] for s in (0.5, 1.0, 1.5, 2.0, 3.0)}
    for item in sentences(lang)[:15]:
        a = audio(item)
        stamps = get_speech_timestamps(a, VadOptions())
        onset = stamps[0]["start"] / 16000 if stamps else 0.0
        for speech, out in results.items():
            got, share = tr.detect(a[: int((onset + speech) * 16000)])
            out.append((got == lang, share))
    print(lang)
    for speech, out in results.items():
        wrong_but_sure = sum(1 for ok, share in out if share >= 0.9 and not ok)
        print(f"   {speech:.1f} s of speech: correct {sum(ok for ok, _ in out)}/{len(out)}, "
              f"confidently wrong {wrong_but_sure}")

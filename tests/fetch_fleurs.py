"""Download real English and Slovak test sentences (Google FLEURS: read speech + reference text).

Plain python3 is enough:  python3 tests/fetch_fleurs.py      (~27 MB into tests/fleurs/)
"""
import json
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent / "fleurs"
API = "https://datasets-server.huggingface.co/rows?dataset=WueNLP/belebele-fleurs&config={}&split=test&offset={}&length=10"


def fetch(config: str, want: int) -> None:
    items, offset = [], 0
    while len(items) < want:
        rows = json.load(urllib.request.urlopen(API.format(config, offset), timeout=60))["rows"]
        if not rows:
            break
        for row in rows:
            for sentence in row["row"]["sentence_data"]:
                if len(items) >= want or not sentence["audio"]:
                    continue
                name = f"{config}_{len(items):02d}.wav"
                urllib.request.urlretrieve(sentence["audio"][0][0]["src"], OUT / name)
                items.append({"file": name, "text": sentence["raw_transcription"]})
        offset += 10
    (OUT / f"{config}.json").write_text(json.dumps(items, ensure_ascii=False, indent=1))
    print(f"{config}: {len(items)} sentences")


OUT.mkdir(exist_ok=True)
fetch("slk_Latn", 30)
fetch("eng_Latn", 15)

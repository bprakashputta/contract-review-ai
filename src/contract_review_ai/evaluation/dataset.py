"""CUAD v1 loader (The Atticus Project, CC BY 4.0).

Uses the authors' official train/test split from https://github.com/TheAtticusProject/cuad
(``data.zip``: ``train_separate_questions.json`` = 408 contracts, ``test.json`` = 102).
The train split is our dev set (prefilter tuning, prompt iteration); test is reported."""

from __future__ import annotations

import hashlib
import json
import random
import re
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..categories import BY_CUAD, BY_KEY

URL = "https://github.com/TheAtticusProject/cuad/raw/main/data.zip"
FILES = {"dev": "train_separate_questions.json", "test": "test.json"}


@dataclass
class GoldSpan:
    start: int
    end: int
    text: str


@dataclass
class Contract:
    id: str
    text: str
    gold: dict[str, list[GoldSpan]] = field(default_factory=dict)  # category key -> spans


def download(data_dir: Path) -> Path:
    raw = data_dir / "raw"
    target = raw / "cuad"
    if (target / FILES["test"]).exists():
        return target
    raw.mkdir(parents=True, exist_ok=True)
    zpath = raw / "data.zip"
    if not zpath.exists():
        print(f"downloading {URL} ...")
        urllib.request.urlretrieve(URL, zpath)  # noqa: S310 - fixed https URL
    print("sha256", hashlib.sha256(zpath.read_bytes()).hexdigest())
    with zipfile.ZipFile(zpath) as z:
        z.extractall(target)
    return target


def load(split: str, data_dir: Path = Path("data")) -> list[Contract]:
    path = download(data_dir) / FILES[split]
    raw = json.loads(path.read_text())["data"]
    out = []
    for item in raw:
        para = item["paragraphs"][0]
        text = para["context"]
        gold: dict[str, list[GoldSpan]] = {}
        for qa in para["qas"]:
            cuad_name = re.sub(r"_\d+$", "", qa["id"].rsplit("__", 1)[-1])
            cat = BY_CUAD.get(cuad_name)
            if cat is None:
                continue
            spans = gold.setdefault(cat.key, [])
            seen = {(g.start, g.end) for g in spans}
            for a in qa["answers"]:
                s = a["answer_start"]
                e = s + len(a["text"])
                if (s, e) in seen:
                    continue
                seen.add((s, e))
                assert text[s:e] == a["text"], f"bad offset in {item['title']}"
                spans.append(GoldSpan(s, e, a["text"]))
        for key in BY_KEY:
            gold.setdefault(key, [])
        out.append(Contract(id=item["title"], text=text, gold=gold))
    return out


def sample(contracts: list[Contract], n: int | None, seed: int = 7) -> list[Contract]:
    if n is None or n >= len(contracts):
        return sorted(contracts, key=lambda c: c.id)
    rng = random.Random(seed)
    return sorted(rng.sample(sorted(contracts, key=lambda c: c.id), n), key=lambda c: c.id)

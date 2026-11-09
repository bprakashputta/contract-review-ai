from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


@dataclass(frozen=True)
class Prompt:
    version: str
    system: str
    user: str


@cache
def load_prompt(name: str, prompts_dir: Path = PROMPTS_DIR) -> Prompt:
    data = yaml.safe_load((prompts_dir / f"{name}.yaml").read_text())
    return Prompt(version=data["version"], system=data["system"], user=data["user"])

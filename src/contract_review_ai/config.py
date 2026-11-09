from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    model: str = "claude-haiku-5-5"
    spend_cap_usd: float = 5.00
    data_dir: Path = Path("data")
    db_path: Path = Path("data/contracts.db")
    playbook_path: Path = Path("playbooks/default.yaml")
    prompts_dir: Path = Path("prompts")
    # documents estimated below this many tokens are sent whole: prefiltering them saves
    # little and can only lose recall
    prefilter_min_tokens: int = 3000
    prefilter_k: int = 3
    llm_timeout_s: float = 120.0
    llm_max_retries: int = 3
    log_level: str = "INFO"


def get_settings() -> Settings:
    return Settings()

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(slots=True)
class Settings:
    bot_token: str
    bot_name: str
    bot_version: str
    bot_username: str
    owner_ids: set[int]
    owner_labels: list[str]
    support_url: str
    support_text: str
    database_path: Path
    data_dir: Path
    auto_restart: bool
    memory_limit_mb: int
    cpu_limit_seconds: int

    @property
    def owner_names(self) -> list[str]:
        labels = self.owner_labels or []
        ids = list(self.owner_ids)
        if labels:
            return labels
        return [str(item) for item in ids]


def get_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required")

    bot_name = os.getenv("BOT_NAME", "Vip Host Bot").strip()
    bot_version = os.getenv("BOT_VERSION", "v1.0").strip()
    bot_username = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
    owner_ids = {int(item) for item in _split_csv(os.getenv("OWNER_IDS"))}
    owner_labels = _split_csv(os.getenv("OWNER_LABELS"))
    support_url = os.getenv("SUPPORT_URL", "https://t.me/").strip()
    support_text = os.getenv("SUPPORT_TEXT", "Hubungi support untuk bantuan.").strip()
    database_path = Path(os.getenv("DATABASE_PATH", "data/bot.db"))
    data_dir = Path(os.getenv("DATA_DIR", "data"))
    auto_restart = os.getenv("AUTO_RESTART", "true").strip().lower() in {"1", "true", "yes", "on"}
    memory_limit_mb = max(128, int(os.getenv("BOT_MEMORY_LIMIT_MB", "512")))
    cpu_limit_seconds = max(0, int(os.getenv("BOT_CPU_LIMIT_SECONDS", "0")))

    return Settings(
        bot_token=bot_token,
        bot_name=bot_name,
        bot_version=bot_version,
        bot_username=bot_username,
        owner_ids=owner_ids,
        owner_labels=owner_labels,
        support_url=support_url,
        support_text=support_text,
        database_path=database_path,
        data_dir=data_dir,
        auto_restart=auto_restart,
        memory_limit_mb=memory_limit_mb,
        cpu_limit_seconds=cpu_limit_seconds,
    )

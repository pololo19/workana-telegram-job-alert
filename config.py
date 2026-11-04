from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _parse_keywords(raw: str | None) -> list[str]:
    if not raw or not raw.strip():
        return []
    return [k.strip().lower() for k in raw.split(",") if k.strip()]


def _parse_max_bids(raw: str | None) -> int | None:
    if raw is None or not str(raw).strip():
        return None
    return int(raw.strip())


def _parse_bool(raw: str | None) -> bool:
    return (raw or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    poll_interval_seconds: int
    workana_jobs_url: str
    keywords: list[str]
    max_bids: int | None
    workana_use_playwright: bool
    workana_proxy: str | None
    data_dir: Path

    @classmethod
    def from_env(cls) -> Settings:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            raise SystemExit(
                "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env (see .env.example)."
            )

        interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "90"))
        if interval < 30:
            raise SystemExit("POLL_INTERVAL_SECONDS must be at least 30.")

        url = os.environ.get(
            "WORKANA_JOBS_URL",
            "https://www.workana.com/en/jobs?order=recent&language=xx",
        ).strip()

        data_dir = BASE_DIR / "data"
        data_dir.mkdir(exist_ok=True)

        proxy = os.environ.get("WORKANA_PROXY", "").strip() or None

        return cls(
            telegram_bot_token=token,
            telegram_chat_id=chat_id,
            poll_interval_seconds=interval,
            workana_jobs_url=url,
            keywords=_parse_keywords(os.environ.get("KEYWORDS")),
            max_bids=_parse_max_bids(os.environ.get("MAX_BIDS")),
            workana_use_playwright=_parse_bool(
                os.environ.get("WORKANA_USE_PLAYWRIGHT")
            ),
            workana_proxy=proxy,
            data_dir=data_dir,
        )

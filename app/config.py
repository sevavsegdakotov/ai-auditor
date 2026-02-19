from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.1")
    keyso_api_token: str = os.getenv("KEYSO_API_TOKEN", "")
    keyso_base_url: str = os.getenv("KEYSO_BASE_URL", "https://api.keys.so")
    keyso_timeout_seconds: int = int(os.getenv("KEYSO_TIMEOUT_SECONDS", "45"))
    keyso_verify_ssl: bool = os.getenv("KEYSO_VERIFY_SSL", "true").lower() == "true"
    google_sheets_webhook_url: str = os.getenv("GOOGLE_SHEETS_WEBHOOK_URL", "")
    google_sheets_webhook_token: str = os.getenv("GOOGLE_SHEETS_WEBHOOK_TOKEN", "")
    google_sheets_verify_ssl: bool = os.getenv("GOOGLE_SHEETS_VERIFY_SSL", "true").lower() == "true"
    base_url: str = os.getenv("BASE_URL", "http://localhost:8000")
    headless: bool = os.getenv("HEADLESS", "true").lower() == "true"


settings = Settings()

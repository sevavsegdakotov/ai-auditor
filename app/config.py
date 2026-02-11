from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4.1")
    base_url: str = os.getenv("BASE_URL", "http://localhost:8000")
    headless: bool = os.getenv("HEADLESS", "true").lower() == "true"


settings = Settings()

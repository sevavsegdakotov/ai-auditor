from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_build_sha: str = os.getenv("APP_BUILD_SHA", "")
    app_build_time: str = os.getenv("APP_BUILD_TIME", "")
    top10_structure_parser_version: str = os.getenv("TOP10_STRUCTURE_PARSER_VERSION", "v2_strict")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.1")
    keyso_api_token: str = os.getenv("KEYSO_API_TOKEN", "")
    keyso_base_url: str = os.getenv("KEYSO_BASE_URL", "https://api.keys.so")
    keyso_timeout_seconds: int = int(os.getenv("KEYSO_TIMEOUT_SECONDS", "120"))
    keyso_project_ready_timeout_seconds: int = int(os.getenv("KEYSO_PROJECT_READY_TIMEOUT_SECONDS", "600"))
    top10_urls_timeout_seconds: int = int(os.getenv("TOP10_URLS_TIMEOUT_SECONDS", "110"))
    keyso_verify_ssl: bool = os.getenv("KEYSO_VERIFY_SSL", "true").lower() == "true"
    google_sheets_webhook_url_site: str = os.getenv("GOOGLE_SHEETS_WEBHOOK_URL_SITE", "")
    google_sheets_webhook_url_competitors: str = os.getenv("GOOGLE_SHEETS_WEBHOOK_URL_COMPETITORS", "")
    google_sheets_webhook_url_structure: str = os.getenv("GOOGLE_SHEETS_WEBHOOK_URL_STRUCTURE", "")
    # Legacy fallback (kept for backwards compatibility).
    google_sheets_webhook_url: str = os.getenv("GOOGLE_SHEETS_WEBHOOK_URL", "")
    google_sheets_webhook_token: str = os.getenv("GOOGLE_SHEETS_WEBHOOK_TOKEN", "")
    google_sheets_verify_ssl: bool = os.getenv("GOOGLE_SHEETS_VERIFY_SSL", "true").lower() == "true"
    apps_script_timeout_seconds: int = int(os.getenv("APPS_SCRIPT_TIMEOUT_SECONDS", "180"))
    base_url: str = os.getenv("BASE_URL", "http://localhost:8000")
    headless: bool = os.getenv("HEADLESS", "true").lower() == "true"
    crawler_page_timeout_ms: int = int(os.getenv("CRAWLER_PAGE_TIMEOUT_MS", "90000"))
    yandex_metrika_access_token: str = os.getenv("YANDEX_METRIKA_ACCESS_TOKEN", "")
    yandex_metrika_client_id: str = os.getenv("YANDEX_METRIKA_CLIENT_ID", "")
    yandex_metrika_client_secret: str = os.getenv("YANDEX_METRIKA_CLIENT_SECRET", "")
    yandex_metrika_redirect_uri: str = os.getenv("YANDEX_METRIKA_REDIRECT_URI", "")
    strict_block_display_format: bool = os.getenv("STRICT_BLOCK_DISPLAY_FORMAT", "true").lower() == "true"


settings = Settings()

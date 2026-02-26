from __future__ import annotations

import json
import ssl
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

import certifi

from app.config import settings


class AppsScriptSheetsExporter:
    def __init__(self, webhook_url: str | None = None) -> None:
        webhook_url = (webhook_url or settings.google_sheets_webhook_url).strip()
        if not webhook_url:
            raise RuntimeError("Не задан GOOGLE_SHEETS_WEBHOOK_URL.")
        self.webhook_url = webhook_url
        self.webhook_token = settings.google_sheets_webhook_token.strip()
        self.ssl_context = (
            ssl.create_default_context(cafile=certifi.where())
            if settings.google_sheets_verify_ssl
            else ssl._create_unverified_context()  # noqa: S323
        )

    def export(self, report_type: str, payload: dict[str, Any]) -> dict[str, str]:
        body = {
            "report_type": report_type,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "payload": payload,
        }
        if self.webhook_token:
            body["token"] = self.webhook_token
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        target_url = self.webhook_url
        if self.webhook_token:
            headers["X-Webhook-Token"] = self.webhook_token
            parsed = urlparse(target_url)
            query_pairs = dict(parse_qsl(parsed.query, keep_blank_values=True))
            query_pairs["token"] = self.webhook_token
            target_url = urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    parsed.params,
                    urlencode(query_pairs),
                    parsed.fragment,
                )
            )

        req = Request(
            target_url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(req, timeout=60, context=self.ssl_context) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8")
        except HTTPError as exc:
            details = ""
            try:
                details = exc.read().decode("utf-8")
            except Exception:  # noqa: BLE001
                details = str(exc)
            raise RuntimeError(f"Apps Script error {exc.code}: {details}") from exc
        except URLError as exc:
            raise RuntimeError(f"Apps Script connection error: {exc.reason}") from exc

        data: dict[str, Any] = {}
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    data = parsed
            except json.JSONDecodeError:
                data = {}

        spreadsheet_url = (
            str(data.get("spreadsheet_url") or "")
            or str(data.get("spreadsheetUrl") or "")
            or str(data.get("sheetUrl") or "")
            or str(data.get("url") or "")
        )
        spreadsheet_urls_raw = data.get("spreadsheet_urls") or data.get("spreadsheetUrls") or []
        spreadsheet_urls: list[str] = []
        if isinstance(spreadsheet_urls_raw, list):
            spreadsheet_urls = [str(item).strip() for item in spreadsheet_urls_raw if str(item).strip()]
            if not spreadsheet_url and spreadsheet_urls:
                spreadsheet_url = spreadsheet_urls[0]
        if not spreadsheet_url:
            raise RuntimeError(
                "Apps Script не вернул ссылку на таблицу. Ожидается поле spreadsheet_url/spreadsheetUrl/sheetUrl/url."
            )
        spreadsheet_id = str(data.get("spreadsheet_id") or data.get("spreadsheetId") or "")
        result: dict[str, str | list[str]] = {
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_url": spreadsheet_url,
        }
        for key in ("compare_sheet", "sites_sheet", "analysis_sheet", "structure_sheet"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                result[key] = value.strip()
        if spreadsheet_urls:
            result["spreadsheet_urls"] = spreadsheet_urls
        return result  # type: ignore[return-value]

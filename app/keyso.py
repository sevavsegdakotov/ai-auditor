from __future__ import annotations

import json
import ssl
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

import certifi

from app.config import settings


@dataclass(frozen=True)
class KeysoTopUrl:
    url: str
    count: int


class KeysoClient:
    def __init__(self) -> None:
        if not settings.keyso_api_token:
            raise RuntimeError("KEYSO_API_TOKEN is not set")
        self.base_url = settings.keyso_base_url.rstrip("/") + "/"
        self.timeout = settings.keyso_timeout_seconds
        self.ssl_context = (
            ssl.create_default_context(cafile=certifi.where())
            if settings.keyso_verify_ssl
            else ssl._create_unverified_context()  # noqa: S323
        )
        self._headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Keyso-TOKEN": settings.keyso_api_token,
        }

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> Any:
        relative = path.lstrip("/")
        target = urljoin(self.base_url, relative)
        if query:
            clean_query = {k: v for k, v in query.items() if v is not None}
            target = f"{target}?{urlencode(clean_query)}"
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(target, data=body, headers=self._headers, method=method)
        last_connection_error: URLError | None = None
        for attempt in range(3):
            try:
                with urlopen(req, timeout=self.timeout, context=self.ssl_context) as resp:  # noqa: S310
                    raw = resp.read().decode("utf-8")
                    break
            except HTTPError as exc:
                details = ""
                try:
                    details = exc.read().decode("utf-8")
                except Exception:  # noqa: BLE001
                    details = str(exc)
                if exc.code >= 500 and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise RuntimeError(f"KeySo API error {exc.code}: {details}") from exc
            except URLError as exc:
                last_connection_error = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise RuntimeError(f"KeySo API connection error: {exc.reason}") from exc
        else:
            if last_connection_error is not None:
                raise RuntimeError(f"KeySo API connection error: {last_connection_error.reason}")
            raise RuntimeError("KeySo API connection error: unknown")

        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def _create_serp_project(self, name: str, queries: list[str], region_id: int, top_number: int) -> None:
        payload = {
            "data": {
                "name": name,
                "regionId": region_id,
                "topNumber": top_number,
                "searchEngine": 0,
                "words": queries,
            }
        }
        self._request_json("POST", "/serp", payload=payload)

    def _find_project_by_name(self, name: str) -> dict[str, Any] | None:
        # Берём несколько страниц списка на случай активного аккаунта с большим количеством проектов.
        for page in range(1, 6):
            response = self._request_json(
                "GET",
                "/serp",
                query={"current_page": page, "per_page": 100},
            )
            rows = response.get("data") if isinstance(response, dict) else None
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict) and row.get("name") == name:
                    return row
        return None

    def _wait_project_ready(self, project_id: int, timeout_seconds: int = 240) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status = self._request_json("GET", f"/serp/{project_id}/status")
            if isinstance(status, dict):
                batches = int(status.get("batches", 0) or 0)
                total = int(status.get("batches_total", 0) or 0)
                if total > 0 and batches >= total:
                    return
            # У KeySo статус иногда "подвисает", но страницы уже доступны.
            probe = self._request_json(
                "GET",
                f"/serp/{project_id}/competitor-pages",
                query={"page": 1, "per_page": 1, "organic": "true", "context": "false", "paramsGET": "false"},
            )
            if isinstance(probe, dict):
                try:
                    total_pages = int(probe.get("total", 0) or 0)
                except (TypeError, ValueError):
                    total_pages = 0
                if total_pages > 0 or (isinstance(probe.get("data"), list) and len(probe.get("data") or []) > 0):
                    return
            time.sleep(2.5)
        raise RuntimeError("KeySo: таймаут ожидания завершения парсинга выдачи.")

    @staticmethod
    def _normalize_url(raw: str) -> str:
        value = (raw or "").strip()
        if not value:
            return ""
        if not value.startswith(("http://", "https://")):
            value = f"https://{value}"
        return value

    def _collect_competitor_pages(self, project_id: int) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        page = 1
        per_page = 100
        last_page = None
        while True:
            response = self._request_json(
                "GET",
                f"/serp/{project_id}/competitor-pages",
                query={"page": page, "per_page": per_page, "organic": "true", "context": "false", "paramsGET": "false"},
            )
            if not isinstance(response, dict):
                break
            data = response.get("data")
            if isinstance(data, list):
                pages.extend(item for item in data if isinstance(item, dict))
            if last_page is None:
                raw_last_page = response.get("last_page")
                if raw_last_page is not None:
                    try:
                        last_page = int(raw_last_page or 1)
                    except (TypeError, ValueError):
                        last_page = 1
                else:
                    try:
                        total = int(response.get("total", 0) or 0)
                    except (TypeError, ValueError):
                        total = 0
                    last_page = max(1, (total + per_page - 1) // per_page) if total > 0 else 1
            if page >= (last_page or 1):
                break
            page += 1
        return pages

    def get_top_urls(self, queries: list[str], region_id: int, top_n: int = 10) -> list[KeysoTopUrl]:
        cleaned_queries = [query.strip() for query in queries if query.strip()]
        if not cleaned_queries:
            raise RuntimeError("KeySo: передайте хотя бы один поисковый запрос.")
        top_n = max(1, min(top_n, 30))
        project_name = f"ai-analyst-{int(time.time())}-{abs(hash(tuple(cleaned_queries))) % 100000}"
        self._create_serp_project(project_name, cleaned_queries, region_id, top_n)
        project = self._find_project_by_name(project_name)
        if not project or "id" not in project:
            raise RuntimeError("KeySo: не удалось найти созданный проект выдачи.")
        project_id = int(project["id"])
        wait_error: RuntimeError | None = None
        try:
            self._wait_project_ready(project_id, timeout_seconds=300)
        except RuntimeError as exc:
            # У KeySo нередко зависает статус, но данные появляются позже.
            wait_error = exc

        rows: list[dict[str, Any]] = []
        for attempt in range(3):
            rows = self._collect_competitor_pages(project_id)
            if rows:
                break
            time.sleep(2.5 * (attempt + 1))
        if not rows and wait_error is not None:
            raise wait_error

        counts: dict[str, int] = defaultdict(int)
        for row in rows:
            raw_url = row.get("full_url") or row.get("url") or ""
            normalized = self._normalize_url(str(raw_url))
            if not normalized:
                continue
            try:
                row_cnt = int(row.get("cnt", 0) or 0)
            except (TypeError, ValueError):
                row_cnt = 0
            counts[normalized] += row_cnt if row_cnt > 0 else 1

        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        return [KeysoTopUrl(url=url, count=count) for url, count in ranked[:top_n]]

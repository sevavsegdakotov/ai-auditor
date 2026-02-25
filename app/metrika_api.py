from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from app.config import settings
from app.metrics import TopPage


class MetrikaApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class MetrikaCounter:
    id: int
    name: str
    site: str


class MetrikaClient:
    def __init__(self) -> None:
        self.base_mgmt_url = "https://api-metrika.yandex.net/management/v1"
        self.base_stat_url = "https://api-metrika.yandex.net/stat/v1"
        self.token = settings.yandex_metrika_access_token.strip()
        if not self.token:
            raise MetrikaApiError(
                "Не задан YANDEX_METRIKA_ACCESS_TOKEN. Добавьте OAuth-токен в .env."
            )

    def _request_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        full_url = url
        if params:
            full_url = f"{url}?{urlencode(params, doseq=True)}"
        request = Request(
            full_url,
            headers={
                "Authorization": f"OAuth {self.token}",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=45) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8")
            except Exception:  # noqa: BLE001
                body = ""
            raise MetrikaApiError(f"Ошибка API Метрики: HTTP {exc.code}. {body}") from exc
        except URLError as exc:
            raise MetrikaApiError(f"Не удалось обратиться к API Метрики: {exc.reason}") from exc

    def list_counters(self) -> list[MetrikaCounter]:
        payload = self._request_json(
            f"{self.base_mgmt_url}/counters",
            {"per_page": 1000, "field": "goals,mirrors,grants"},
        )
        rows = payload.get("counters", [])
        counters: list[MetrikaCounter] = []
        for row in rows:
            try:
                counter_id = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            counters.append(
                MetrikaCounter(
                    id=counter_id,
                    name=str(row.get("name") or f"Счётчик {counter_id}"),
                    site=str(row.get("site") or "").strip(),
                )
            )
        counters.sort(key=lambda item: item.name.lower())
        return counters

    def _date_window(self, days: int = 90) -> tuple[str, str]:
        date2 = datetime.utcnow().date()
        date1 = date2 - timedelta(days=max(1, days))
        return date1.isoformat(), date2.isoformat()

    @staticmethod
    def _is_query_too_complex_error(message: str) -> bool:
        lowered = message.lower()
        return (
            "запрос слишком сложный" in lowered
            or "query_error" in lowered
            or "уменьшите интервал дат" in lowered
        )

    def _load_report_once(
        self,
        counter_id: int,
        dimensions: list[str],
        metrics: list[str],
        date1: str,
        date2: str,
        accuracy: str,
        limit: int = 100,
        sort: list[str] | None = None,
        filters: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "ids": str(counter_id),
            "date1": date1,
            "date2": date2,
            "dimensions": ",".join(dimensions),
            "metrics": ",".join(metrics),
            "limit": str(limit),
            "accuracy": accuracy,
            "lang": "ru",
        }
        if sort:
            params["sort"] = ",".join(sort)
        if filters:
            params["filters"] = filters

        payload = self._request_json(f"{self.base_stat_url}/data", params)
        data = payload.get("data", [])
        rows: list[dict[str, Any]] = []
        for item in data:
            dimensions_payload = item.get("dimensions", [])
            dimension_text = " | ".join(str(part.get("name") or "").strip() for part in dimensions_payload).strip(" |")
            metrics_payload = item.get("metrics", [0])
            visits = 0
            if metrics_payload:
                try:
                    visits = int(float(metrics_payload[0]))
                except (TypeError, ValueError):
                    visits = 0
            rows.append({"dimension": dimension_text, "visits": visits})
        return rows

    def _load_report(
        self,
        counter_id: int,
        dimensions: list[str],
        metrics: list[str],
        limit: int = 100,
        sort: list[str] | None = None,
        filters: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        attempts = [
            (90, "full", limit),
            (60, "high", min(limit, 120)),
            (30, "high", min(limit, 100)),
            (14, "medium", min(limit, 80)),
            (7, "low", min(limit, 60)),
        ]

        last_error: MetrikaApiError | None = None
        for days, accuracy, current_limit in attempts:
            date1, date2 = self._date_window(days)
            try:
                rows = self._load_report_once(
                    counter_id=counter_id,
                    dimensions=dimensions,
                    metrics=metrics,
                    date1=date1,
                    date2=date2,
                    accuracy=accuracy,
                    limit=current_limit,
                    sort=sort,
                    filters=filters,
                )
                return rows, {
                    "days": days,
                    "date1": date1,
                    "date2": date2,
                    "accuracy": accuracy,
                    "limit": current_limit,
                }
            except MetrikaApiError as exc:
                last_error = exc
                if not self._is_query_too_complex_error(str(exc)):
                    raise
                continue

        if last_error:
            raise MetrikaApiError(
                "Ошибка API Метрики: запрос слишком сложный даже после автоматического упрощения "
                "(период 90→7 дней, сниженная точность). Попробуйте другой счётчик или сузьте период в Метрике."
            ) from last_error
        return [], {}

    @staticmethod
    def _normalize_site_url(site: str) -> str:
        value = site.strip()
        if not value:
            return ""
        if not value.startswith(("http://", "https://")):
            value = f"https://{value}"
        return value.rstrip("/")

    @staticmethod
    def _normalize_page_url(raw: str, base_site: str) -> str:
        value = raw.strip()
        if not value:
            return ""
        if value.startswith(("http://", "https://")):
            return value.rstrip("/")
        if base_site:
            if value.startswith("/"):
                return f"{base_site}{value}".rstrip("/")
            return f"{base_site}/{value}".rstrip("/")
        return value

    def load_metrics_snapshot(
        self,
        counter_id: int,
        top_n: int,
    ) -> tuple[pd.DataFrame, list[TopPage], dict[str, Any]]:
        counters = self.list_counters()
        current_counter = next((counter for counter in counters if counter.id == counter_id), None)
        if current_counter is None:
            raise MetrikaApiError(f"Счётчик {counter_id} не найден в списке доступных.")

        report_specs = [
            ("Источники трафика", ["ym:s:lastTrafficSource"], ["ym:s:visits"], 50, ["-ym:s:visits"], None),
            (
                "Поисковые запросы",
                ["ym:s:searchPhrase"],
                ["ym:s:visits"],
                100,
                ["-ym:s:visits"],
                "ym:s:lastTrafficSource=='organic'",
            ),
            ("Устройства", ["ym:s:deviceCategory"], ["ym:s:visits"], 20, ["-ym:s:visits"], None),
            ("Возвраты", ["ym:s:isNewUser"], ["ym:s:visits"], 10, ["-ym:s:visits"], None),
            ("Страницы входа", ["ym:s:startURL"], ["ym:s:visits"], 200, ["-ym:s:visits"], None),
        ]

        report_rows: list[pd.DataFrame] = []
        report_query_profile: dict[str, dict[str, Any]] = {}
        top_pages: list[TopPage] = []
        base_site = self._normalize_site_url(current_counter.site)

        for report_name, dimensions, metrics, limit, sort, filters in report_specs:
            try:
                rows, query_meta = self._load_report(
                    counter_id=counter_id,
                    dimensions=dimensions,
                    metrics=metrics,
                    limit=limit,
                    sort=sort,
                    filters=filters,
                )
            except MetrikaApiError:
                if filters:
                    rows, query_meta = self._load_report(
                        counter_id=counter_id,
                        dimensions=dimensions,
                        metrics=metrics,
                        limit=limit,
                        sort=sort,
                        filters=None,
                    )
                else:
                    raise
            report_query_profile[report_name] = query_meta

            frame = pd.DataFrame(rows)
            if frame.empty:
                continue
            frame["report"] = report_name
            report_rows.append(frame[["report", "dimension", "visits"]])

            if report_name == "Страницы входа":
                for row in rows:
                    normalized_url = self._normalize_page_url(str(row.get("dimension") or ""), base_site)
                    if not normalized_url.startswith(("http://", "https://")):
                        continue
                    top_pages.append(TopPage(url=normalized_url, visits=int(row.get("visits") or 0)))

        if not report_rows:
            return pd.DataFrame(columns=["report", "dimension", "visits"]), [], {
                "counter_id": current_counter.id,
                "counter_name": current_counter.name,
                "counter_site": current_counter.site,
                "query_profile": report_query_profile,
            }

        combined = pd.concat(report_rows, ignore_index=True)
        unique_top: list[TopPage] = []
        seen_urls: set[str] = set()
        for row in sorted(top_pages, key=lambda item: item.visits, reverse=True):
            if row.url in seen_urls:
                continue
            seen_urls.add(row.url)
            unique_top.append(row)
            if len(unique_top) >= max(1, top_n):
                break

        min_days = min((int(meta.get("days", 90)) for meta in report_query_profile.values()), default=90)
        max_accuracy_rank = {"low": 1, "medium": 2, "high": 3, "full": 4}
        effective_accuracy = "full"
        if report_query_profile:
            effective_accuracy = min(
                report_query_profile.values(),
                key=lambda meta: max_accuracy_rank.get(str(meta.get("accuracy", "full")), 4),
            ).get("accuracy", "full")

        return combined, unique_top, {
            "counter_id": current_counter.id,
            "counter_name": current_counter.name,
            "counter_site": current_counter.site,
            "query_profile": report_query_profile,
            "effective_period_days": min_days,
            "effective_accuracy": effective_accuracy,
        }

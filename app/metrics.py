from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import pandas as pd


URL_CANDIDATES = [
    "страница входа",
    "страница выхода",
    "адрес страницы",
    "страница",
    "адрес",
    "url",
    "page",
    "landing",
]
VISITS_CANDIDATES = [
    "визиты",
    "посещения",
    "visits",
    "sessions",
    "просмотры",
    "просмотров",
    "view",
]
SKIP_VALUES = {"", "не определено", "nan", "none"}


@dataclass
class TopPage:
    url: str
    visits: int


def _normalize_columns(columns: Iterable[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for col in columns:
        normalized = str(col).strip().lower()
        mapping[normalized] = str(col)
    return mapping


def _pick_column(columns_map: dict[str, str], candidates: list[str]) -> str | None:
    for candidate in candidates:
        for normalized, original in columns_map.items():
            if candidate in normalized:
                return original
    return None


def _clean_metric(value: object) -> float:
    text = str(value).strip().replace(" ", "").replace(",", ".")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _is_valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _prepare_subset(df: pd.DataFrame) -> pd.DataFrame:
    columns_map = _normalize_columns(df.columns)
    url_col = _pick_column(columns_map, URL_CANDIDATES)
    visits_col = _pick_column(columns_map, VISITS_CANDIDATES)
    if not url_col or not visits_col:
        return pd.DataFrame(columns=["url", "visits"])

    subset = df[[url_col, visits_col]].copy()
    subset.columns = ["url", "visits"]
    subset["url"] = subset["url"].astype(str).str.strip()
    subset["url"] = subset["url"].str.rstrip("/")
    subset = subset[~subset["url"].str.lower().isin(SKIP_VALUES)]
    subset = subset[subset["url"].map(_is_valid_url)]
    subset["visits"] = subset["visits"].map(_clean_metric)
    subset = subset[subset["visits"] > 0]
    return subset


def _detect_header_row(file_path: Path, sheet: str, max_rows: int = 15) -> int | None:
    try:
        raw = pd.read_excel(file_path, sheet_name=sheet, header=None, nrows=max_rows)
    except Exception:  # noqa: BLE001
        return None
    for idx, row in raw.fillna("").iterrows():
        values = [str(v).strip().lower() for v in row.tolist()]
        has_url = any(any(token in cell for token in URL_CANDIDATES) for cell in values)
        has_visits = any(any(token in cell for token in VISITS_CANDIDATES) for cell in values)
        if has_url and has_visits:
            return int(idx)
    return None


def _extract_from_file(file_path: Path) -> list[pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    xls = pd.ExcelFile(file_path)

    for sheet in xls.sheet_names:
        header_row = _detect_header_row(file_path, sheet)
        if header_row is not None:
            try:
                df = pd.read_excel(file_path, sheet_name=sheet, header=header_row)
            except Exception:  # noqa: BLE001
                df = pd.DataFrame()
            subset = _prepare_subset(df)
            if not subset.empty:
                frames.append(subset)
                continue

        # Шаблоны Метрики часто имеют служебные строки перед заголовком.
        for header_row in range(0, 12):
            try:
                df = pd.read_excel(file_path, sheet_name=sheet, header=header_row)
            except Exception:  # noqa: BLE001
                continue
            if df.empty:
                continue
            subset = _prepare_subset(df)
            if not subset.empty:
                frames.append(subset)
                break
    return frames


def parse_metrics_files(files: list[Path], top_n: int = 10) -> tuple[pd.DataFrame, list[TopPage]]:
    frames: list[pd.DataFrame] = []
    for file_path in files:
        try:
            frames.extend(_extract_from_file(file_path))
        except Exception:  # noqa: BLE001
            continue

    if not frames:
        return pd.DataFrame(columns=["url", "visits"]), []

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.groupby("url", as_index=False)["visits"].sum()
    combined = combined.sort_values("visits", ascending=False)

    top_pages = [
        TopPage(url=row.url, visits=int(row.visits))
        for row in combined.head(top_n).itertuples(index=False)
    ]
    return combined, top_pages


def dataframe_preview(df: pd.DataFrame, limit: int = 30) -> str:
    if df.empty:
        return "Нет данных после парсинга Excel-файлов."
    preview = df.head(limit)
    try:
        return preview.to_markdown(index=False)
    except Exception:  # noqa: BLE001
        return preview.to_string(index=False)

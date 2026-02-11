from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.crawler import collect_page_artifacts
from app.llm import LLMClient
from app.metrics import dataframe_preview, parse_metrics_files

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="AI-аудитор сайта")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "result": None,
            "asset_version": datetime.now().strftime("%Y%m%d%H%M%S"),
            "default_role_prompt": (
                "<instructions>\n"
                "- Соблюдай <answering_rules> и <self_reflection>.\n"
                "- Отвечай кратко, структурно, без воды; аргументируй фактами и цифрами.\n"
                "- Пиши по-русски с корректной типографикой («ёлочки», длинное тире).\n"
                "- Приводи примеры, критикуй аргументированно, предлагай лучший вариант.\n"
                "- Форматируй строго по запросу (статья, пост, тезисы и т.п.).\n"
                "- Избегай англицизмов, при необходимости давай русский аналог.\n\n"
                "<self_reflection>\n"
                "1. Используй язык сообщения пользователя.\n"
                "2. В первом сообщении назначай себе экспертную роль с упоминанием реального авторитета или "
                "награды (пример: «Отвечу как известный эксперт в <сфера>, лауреат <престижная награда>»).\n"
                "3. Действуй в рамках назначенной роли.\n"
                "4. Отвечай естественно, по-человечески.\n"
                "5. Всегда используй структуру <example> для первого сообщения.\n"
                "6. Если пользователь не просит конкретных действий — не предлагай их.\n"
                "7. Таблицы используй только при прямом запросе или если это явно улучшает восприятие.\n\n"
                "<answering_rules>\n"
                "1. Используй язык запроса.\n"
                "2. В первом ответе назначь себе экспертную роль с реальным авторитетом/наградой.\n"
                "3. Действуй в рамках роли, отвечай естественно.\n"
                "4. Всегда используй <example> для первого ответа.\n"
                "5. Не предлагай действий без запроса.\n"
                "6. Таблицы — только при запросе или явной пользе.\n\n"
                "<example>\n"
                "Отвечу как эксперт в <сфера>, лауреат <награда>\n"
                "Краткий вывод\n"
                "<Пошаговый ответ с конкретикой, аргументами и примерами>\n"
                "</example>\n"
                "</instructions>"
            ),
            "default_metrics_prompt": (
                "Проанализируй выгрузку по яндекс.метрике для сайта и дай развёрнутые аргументированные "
                "выводы, которые позволят определить ЦА сайт для дальнейшей его доработки: из каких "
                "источников приходят на сайт, по каким поисковым запросам заходят, на какие страницы "
                "заходят, сколько страниц смотрят, возвращаются ли на сайт, с каких устройств заходят и "
                "есть ли различия ПК/мобильные. Дополнительно выдели топ-5 страниц входа по объёму трафика."
            ),
            "default_audience_prompt": "Определи ЦА страницы и её сегменты по JTBD.",
            "default_pages_prompt": (
                "Проанализируй скриншоты и текст топ-5 страниц входа, как маркетолог с пониманием ЦА по JTBD "
                "и эксперт в юзабилити, сделай выводы, выдели проблемы и предложи доработки.\n\n"
                "Проблемы подели на 3 группы - соответствие ЦА, общая структура и юзабилити, адаптивность "
                "мобильной версии.\n\n"
                "Делай анализ проблем для каждой страницы отдельно, вначале — сделай общие выводы по всем "
                "страницам.\n\n"
                "Проблемы описывай кратко и тезисно, в формате: проблема - почему проблема - решение.\n\n"
                "Для каждой страницы в каждой категории выдели до 5 самых важных проблем. Можно и меньше, но "
                "не более 5, и несколько."
            ),
        },
    )


async def _run_analysis(
    files: list[UploadFile] = File(...),
    top_n: int = Form(5),
    role_prompt: str = Form(...),
    metrics_prompt: str = Form(...),
    audience_prompt: str = Form(...),
    pages_prompt: str = Form(...),
) -> dict:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_upload_dir = UPLOADS_DIR / run_id
    run_screens_dir = SCREENSHOTS_DIR / run_id
    run_upload_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: list[Path] = []
    for upload in files:
        target = run_upload_dir / upload.filename
        content = await upload.read()
        target.write_bytes(content)
        saved_paths.append(target)

    combined_df, top_pages = parse_metrics_files(saved_paths, top_n=top_n)

    result = {
        "run_id": run_id,
        "top_pages": [],
        "metrics_analysis": "",
        "audience_analysis": "",
        "pages_analysis": "",
        "errors": [],
    }

    if not top_pages:
        result["errors"].append(
            "Не удалось извлечь URL и посещаемость из Excel. Проверьте названия колонок (url/страница и visits/визиты)."
        )
        return result

    try:
        llm = LLMClient()
        metrics_payload = {
            "top_pages": [{"url": item.url, "visits": item.visits} for item in top_pages],
            "table_preview": dataframe_preview(combined_df, limit=50),
        }
        result["metrics_analysis"] = await llm.analyze(
            f"{role_prompt}\n\nЗадача этапа 1 (анализ выгрузок):\n{metrics_prompt}",
            metrics_payload,
        )

        audience_payload = {
            "metrics_analysis": result["metrics_analysis"],
            "top_pages": [{"url": item.url, "visits": item.visits} for item in top_pages],
        }
        result["audience_analysis"] = await llm.analyze(
            f"{role_prompt}\n\nЗадача этапа 2 (выделение ЦА/JTBD):\n{audience_prompt}",
            audience_payload,
        )

        artifacts = await collect_page_artifacts(top_pages, run_screens_dir)
        for item in artifacts:
            item["desktop_screenshot"] = str(Path(item["desktop_screenshot"]).relative_to(BASE_DIR)).replace("\\", "/")
            item["mobile_screenshot"] = str(Path(item["mobile_screenshot"]).relative_to(BASE_DIR)).replace("\\", "/")
        result["top_pages"] = artifacts

        pages_payload = {
            "metrics_analysis": result["metrics_analysis"],
            "audience_analysis": result["audience_analysis"],
            "pages": artifacts,
        }
        result["pages_analysis"] = await llm.analyze(
            f"{role_prompt}\n\nЗадача этапа 3 (анализ страниц):\n{pages_prompt}",
            pages_payload,
        )

        (DATA_DIR / f"report_{run_id}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))

    return result


@app.post("/analyze")
async def analyze(
    files: list[UploadFile] = File(...),
    top_n: int = Form(5),
    role_prompt: str = Form(...),
    metrics_prompt: str = Form(...),
    audience_prompt: str = Form(...),
    pages_prompt: str = Form(...),
) -> JSONResponse:
    result = await _run_analysis(
        files=files,
        top_n=top_n,
        role_prompt=role_prompt,
        metrics_prompt=metrics_prompt,
        audience_prompt=audience_prompt,
        pages_prompt=pages_prompt,
    )
    return JSONResponse(content=result)

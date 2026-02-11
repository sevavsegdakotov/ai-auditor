from __future__ import annotations

import json
from html import escape
from datetime import datetime
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from playwright.async_api import async_playwright

from app.crawler import collect_page_artifacts
from app.llm import LLMClient
from app.metrics import TopPage, dataframe_preview, parse_metrics_files

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

FINAL_SUMMARY_PROMPT = """<PROMPT_FINAL_SUMMARY_v1>

Сделай краткое итоговое саммари по проделанной работе (данные Метрики + JTBD-сегментация + анализ скриншотов/UX).
Пиши максимально по делу, без вступлений и без повторов.

<ограничения>
— Не выводи роль.
— Не объясняй методологию.
— Без «воды» и общих фраз.
— Если чего-то не было в исходных материалах — не выдумывай.
— Объём: 8–14 строк (строго).
</ограничения>

<формат_вывода_строго>
1) Страница: <URL или название> (1 строка)
2) ЦА (JTBD, 3–5 сегментов): (в 3–5 строк, каждый сегмент — 3–7 слов, по ситуации/задаче, НЕ по демографии)
3) Главное (5–7 пунктов): (маркированный список, только самое полезное)
   — 1–2 ключевые находки (что уже хорошо/работает)
   — 2–3 ключевые проблемы (что мешает конверсии/пониманию)
   — 2–3 приоритетные доработки (что делать в первую очередь)
4) Риски/зависимости (1–2 строки): что нельзя утверждать без дополнительных данных
</формат_вывода_строго>

<подсказка_по_формулировкам_сегментов>
Сегмент JTBD формулируй так: «Когда <ситуация>, хочу <результат>».
Пример: «Когда сменили подрядчика, хочу прогнозируемый рост лидов».
</подсказка_по_формулировкам_сегментов>

</PROMPT_FINAL_SUMMARY_v1>"""


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "result": None,
            "asset_version": datetime.now().strftime("%Y%m%d%H%M%S"),
            "default_role_prompt": """<SYSTEM_OUTPUT_STANDARD_v2_NO_ROLE_OUTPUT>

<internal_role>
Ты — старший веб-аналитик и UX-стратег с JTBD-подходом (Яндекс.Метрика + посадочные страницы).
Роль используется только для качества анализа и выбора фокуса, но НЕ выводится в ответе.
</internal_role>

<principles>
1) Пиши по-русски, корректная типографика («ёлочки», длинное тире).
2) Кратко и структурно: только то, что влияет на решения.
3) Любой вывод → опора на данные/наблюдение. Если цифр нет — говори «по скриншоту/по тексту видно…».
4) Не выдумывай метрики/факты. Если данных не хватает — явно укажи, каких полей/разрезов не хватает.
5) Избегай англицизмов; если термин нужен — дай русский аналог в скобках.
6) Таблицы используй только если это явно повышает читаемость (иначе — списки).
</principles>

<universal_output_format>
Всегда соблюдай следующий каркас (если секция не применима — пропусти). Важно: НЕ выводи «роль» и любые награды/самоописания.

1) Краткий вывод (3–6 строк)
— 1–2 главные находки
— 1–2 главных риска/узких места
— 1–2 главных действия с наибольшим эффектом

2) Основание выводов
Коротко перечисли, на чём основаны выводы:
— какие поля/срезы в данных использованы ИЛИ
— что именно видно на скриншотах/в тексте (цитаты/названия блоков/элементы)

3) Детальный разбор (по заданной задаче)
Выводи блоками строго по задаче. В каждом блоке:
а) Наблюдение (что видно)
б) Интерпретация (что это значит)
в) Рекомендация (что менять)

4) Проблемы и решения (если задача про проблемы/UX)
Формат каждой строки строго:
«Проблема → почему проблема → решение»
Лимит: не более N пунктов на раздел (N задаётся в пользовательском промпте; если не задано — N=5).

5) Приоритизация
Если есть несколько рекомендаций:
— High / Medium / Low (влияние × трудоёмкость)
— 1–2 предложения обоснования

6) Что уточнить (только если не хватает данных)
Список из 3–7 конкретных полей/срезов/вопросов.
</universal_output_format>

<quality_bar>
— Никакой «воды» и общих фраз.
— Рекомендации должны быть проверяемыми и внедряемыми (что именно поменять).
— Если просили «до 5» — не превышай.
— Не добавляй секции, которых не просили, кроме «Что уточнить», когда реально не хватает данных.
</quality_bar>

</SYSTEM_OUTPUT_STANDARD_v2_NO_ROLE_OUTPUT>""",
            "default_metrics_prompt": """<PROMPT_METRIKA_DATA_ANALYSIS_v1>

Ты — веб-аналитик уровня senior по Яндекс.Метрике. Проанализируй приложенную выгрузку из Яндекс.Метрики по сайту и дай развёрнутые, аргументированные выводы, которые помогут определить и уточнить целевую аудиторию сайта для последующей доработки.

<обязательные_блоки_анализа>
1) Источники трафика
— Определи каналы/источники (и кампании, если есть).
— Выяви 3–5 ключевых паттернов, опираясь на цифры.

2) Поисковые запросы
— Проанализируй запросы.
— Сгруппируй по намерению: «информационные», «сравнение/выбор», «покупка/заказ», «брендовые» (или эквивалентные группы по данным).
— Выведи выводы о мотивации и ситуации пользователя.

3) Страницы входа
— Определи топовые страницы входа.
— Объясни, что это говорит о потребностях ЦА.
— Если в данных есть поведенческие признаки (отказы/глубина/время/конверсия) — используй их, иначе не выдумывай.

4) Возвраты
— Оцени, возвращаются ли пользователи (новые/вернувшиеся, частота — если есть в выгрузке).
— Вывод: «разовая потребность» или «длинный выбор/повторные визиты».

5) Устройства (ПК/мобайл)
— Сравни структуру трафика и поведение на ПК и мобайле.
— Зафиксируй различия и последствия для доработок.

</обязательные_блоки_анализа>

<дополнительно>
— Выдели «топ-1 страницу входа по объёму трафика» (используй визиты/пользователей — что есть в файле) и объясни, почему она ключевая.
</дополнительно>

<формат_вывода>
Используй каркас из SYSTEM_OUTPUT_STANDARD_v1.
В разделе «Детальный разбор» сделай блоки:
«Источники», «Запросы», «Страницы входа», «Возвраты», «Устройства», «Топ-1 страница входа», «Портрет ЦА».
В каждом блоке: наблюдение → интерпретация → рекомендация.
</формат_вывода>

</PROMPT_METRIKA_DATA_ANALYSIS_v1>""",
            "default_audience_prompt": """<PROMPT_JTBD_TARGET_AUDIENCE_v1>

Ты — продуктовый маркетолог и исследователь JTBD. По содержимому страницы (текст, оффер, структура блоков, цены/тарифы, формы, визуальные акценты, отзывы/кейсы) и/или по данным аналитики (источники, запросы, страницы входа, устройства, поведение) определи целевую аудиторию этой страницы и сегменты по JTBD.

<задача>
1) Сформулируй основной JTBD страницы (1–2 формулировки) в виде:
«Когда …, я хочу …, чтобы …».

2) Выдели 3–7 сегментов JTBD (разные ситуации/контексты/мотивы).
Не сегментируй по демографии, если она не дана.
</задача>

<на_каждый_сегмент_опиши>
— Ситуация/контекст
— Триггер (почему сейчас)
— Ожидаемый результат: функциональный + эмоциональный + социальный
— Критерии выбора (3–5) и какие доказательства нужны (кейсы, цифры, примеры, гарантии и т.п.)
— Альтернативы (как решают без нас: «сам», конкуренты, откладывание)
— Барьеры/риски (что мешает)
— «Фразы клиента»: 2–3 характерные формулировки запроса/сомнений
</на_каждый_сегмент_опиши>

<приоритизация>
— Оцени важность/частоту/ценность сегмента (если нет данных — качественно, но объясни логику).
— Выведи топ-2 приоритетных сегмента для страницы сейчас.
</приоритизация>

<проверка_соответствия_страницы>
— Для каждого сегмента оцени соответствие страницы по шкале 0–10 и объясни оценку.
— Укажи 3 ключевых несоответствия (где страница «не попадает» в ожидания).
</проверка_соответствия_страницы>

<формат_вывода>
Используй каркас из SYSTEM_OUTPUT_STANDARD_v1.
В «Детальном разборе» порядок:
«Основной JTBD» → «Сегменты JTBD» → «Приоритеты» → «Несоответствия страницы».
</формат_вывода>

</PROMPT_JTBD_TARGET_AUDIENCE_v1>""",
            "default_pages_prompt": """<PROMPT_TOP_ENTRY_PAGES_SCREENSHOTS_UX_v1>

Ты — маркетолог с JTBD-мышлением и UX-эксперт. Проанализируй скриншоты (ПК и мобайл) и текст топ-страниц входа. Цель: выявить ключевые проблемы, влияющие на понимание оффера, соответствие аудитории и удобство, и предложить конкретные доработки.

<входные_данные_по_каждой_странице>
— URL/название страницы
— Скриншот ПК
— Скриншот мобайл
— Текст страницы (если есть)
Если чего-то нет — явно укажи, что отсутствует, и не делай выводы «наугад».
</входные_данные_по_каждой_странице>

<сначала_общее>
Сделай 5–10 тезисов по всем страницам:
— что объединяет страницы как первые точки контакта,
— повторяющиеся провалы/риски,
— 2–3 доработки с максимальным эффектом.
</сначала_общее>

<затем_по_каждой_странице_отдельно>
Разбор по 3 категориям:

A) Соответствие ЦА и JTBD
— ясность результата и «для кого»
— релевантность обещания запросу/каналу
— доказательства (кейсы/цифры/примеры/гарантии/сроки)
— снятие страхов и рисков

B) Общая структура и юзабилити
— иерархия и логика блоков, первый экран
— читаемость и когнитивная нагрузка
— заметность и понятность призывов к действию, формы/контакты
— доверие и прозрачность

C) Адаптивность мобильной версии
— читаемость, кликабельность, отступы
— порядок блоков и первый экран на мобайле
— «простыни» и скорость восприятия
— поломки/налезания/обрезания

</затем_по_каждой_странице_отдельно>

<формат_проблем>
Для каждой страницы в каждой категории выведи до 5 самых важных проблем (можно меньше, но не больше 5).
Каждая проблема строго в формате:
«Проблема → почему проблема → решение».
Не перечисляй мелочи: выбирай то, что сильнее всего влияет на понимание, доверие и действие.
</формат_проблем>

<формат_вывода>
Используй каркас из SYSTEM_OUTPUT_STANDARD_v1.
В «Детальном разборе» структура:
«Общие выводы по всем страницам» → далее для каждой страницы блоки A/B/C.
</формат_вывода>

</PROMPT_TOP_ENTRY_PAGES_SCREENSHOTS_UX_v1>""",
        },
    )


async def _run_analysis(
    files: list[UploadFile] | None = File(None),
    audit_mode: str = Form("full"),
    page_url: str = Form(""),
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

    result = {
        "run_id": run_id,
        "audit_mode": audit_mode,
        "top_pages": [],
        "final_summary": "",
        "metrics_analysis": "",
        "audience_analysis": "",
        "pages_analysis": "",
        "errors": [],
    }

    try:
        llm = LLMClient()

        if audit_mode == "screenshot":
            normalized_url = page_url.strip()
            if not normalized_url.startswith(("http://", "https://")):
                result["errors"].append("Для режима «Аудит по ссылке» укажите корректную ссылку на страницу (http/https).")
                return result
            top_pages = [TopPage(url=normalized_url, visits=0)]
            result["metrics_analysis"] = ""
        else:
            if not files:
                result["errors"].append("Для режима «Полный аудит» загрузите Excel-файлы Метрики.")
                return result

            saved_paths: list[Path] = []
            for upload in files:
                target = run_upload_dir / upload.filename
                content = await upload.read()
                target.write_bytes(content)
                saved_paths.append(target)

            combined_df, top_pages = parse_metrics_files(saved_paths, top_n=top_n)
            if not top_pages:
                result["errors"].append(
                    "Не удалось извлечь URL и посещаемость из Excel. Проверьте названия колонок (url/страница и visits/визиты)."
                )
                return result

            metrics_payload = {
                "top_pages": [{"url": item.url, "visits": item.visits} for item in top_pages],
                "table_preview": dataframe_preview(combined_df, limit=50),
            }
            result["metrics_analysis"] = await llm.analyze(
                f"{role_prompt}\n\nЗадача этапа 1 (анализ выгрузок):\n{metrics_prompt}",
                metrics_payload,
            )

        artifacts = await collect_page_artifacts(top_pages, run_screens_dir)
        for item in artifacts:
            item["desktop_screenshot"] = str(Path(item["desktop_screenshot"]).relative_to(BASE_DIR)).replace("\\", "/")
            item["mobile_screenshot"] = str(Path(item["mobile_screenshot"]).relative_to(BASE_DIR)).replace("\\", "/")
        result["top_pages"] = artifacts

        audience_payload = {
            "metrics_analysis": result["metrics_analysis"],
            "top_pages": [{"url": item["url"], "visits": item["visits"]} for item in artifacts],
            "pages": artifacts,
        }
        result["audience_analysis"] = await llm.analyze(
            f"{role_prompt}\n\nЗадача этапа 2 (выделение ЦА/JTBD):\n{audience_prompt}",
            audience_payload,
        )

        pages_payload = {
            "metrics_analysis": result["metrics_analysis"],
            "audience_analysis": result["audience_analysis"],
            "pages": artifacts,
        }
        result["pages_analysis"] = await llm.analyze(
            f"{role_prompt}\n\nЗадача этапа 3 (анализ страниц):\n{pages_prompt}",
            pages_payload,
        )

        summary_payload = {
            "audit_mode": audit_mode,
            "metrics_analysis": result["metrics_analysis"],
            "audience_analysis": result["audience_analysis"],
            "pages_analysis": result["pages_analysis"],
            "pages": artifacts,
        }
        result["final_summary"] = await llm.analyze(
            f"{role_prompt}\n\nЗадача этапа 4 (итоговое саммари):\n{FINAL_SUMMARY_PROMPT}",
            summary_payload,
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
    files: list[UploadFile] | None = File(None),
    audit_mode: str = Form("full"),
    page_url: str = Form(""),
    top_n: int = Form(5),
    role_prompt: str = Form(...),
    metrics_prompt: str = Form(...),
    audience_prompt: str = Form(...),
    pages_prompt: str = Form(...),
) -> JSONResponse:
    result = await _run_analysis(
        files=files,
        audit_mode=audit_mode,
        page_url=page_url,
        top_n=top_n,
        role_prompt=role_prompt,
        metrics_prompt=metrics_prompt,
        audience_prompt=audience_prompt,
        pages_prompt=pages_prompt,
    )
    return JSONResponse(content=result)


def _build_report_html(result: dict) -> str:
    top_pages = result.get("top_pages") or []
    rows = []
    for idx, page in enumerate(top_pages, start=1):
        rows.append(
            "<li>"
            f"{idx}. <b>{escape(str(page.get('url', '-')))}</b><br>"
            f"Визиты: {escape(str(page.get('visits', '-')))}<br>"
            f"Title: {escape(str(page.get('title', '-')))}"
            "</li>"
        )
    top_pages_html = "<ul>" + "".join(rows) + "</ul>" if rows else "<p>Нет страниц</p>"

    def section(title: str, text: str) -> str:
        if not text:
            return ""
        return f"<h3>{escape(title)}</h3><pre>{escape(text)}</pre>"

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #111; }}
    h1 {{ margin: 0 0 8px; }}
    h2 {{ margin: 0 0 14px; color: #555; font-size: 16px; }}
    h3 {{ margin: 18px 0 8px; }}
    pre {{
      white-space: pre-wrap;
      border: 1px solid #ddd;
      padding: 10px;
      border-radius: 6px;
      background: #fafafa;
      font-family: Arial, sans-serif;
    }}
    ul {{ margin-top: 8px; }}
    li {{ margin-bottom: 8px; }}
  </style>
</head>
<body>
  <h1>Отчёт аудита сайта</h1>
  <h2>Run ID: {escape(str(result.get("run_id", "-")))} | Режим: {escape(str(result.get("audit_mode", "-")))}</h2>
  {section("Саммари", str(result.get("final_summary", "")))}
  {section("Анализ метрики", str(result.get("metrics_analysis", "")))}
  {section("Анализ ЦА / JTBD", str(result.get("audience_analysis", "")))}
  {section("Анализ страниц и скриншотов", str(result.get("pages_analysis", "")))}
  <h3>Топ страницы</h3>
  {top_pages_html}
</body>
</html>"""


@app.post("/report/pdf")
async def report_pdf(payload: dict = Body(...)) -> Response:
    html = _build_report_html(payload)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context()
        page = await context.new_page()
        await page.set_content(html, wait_until="load")
        pdf_bytes = await page.pdf(format="A4", print_background=True, margin={"top": "18mm", "right": "12mm", "bottom": "18mm", "left": "12mm"})
        await context.close()
        await browser.close()

    run_id = str(payload.get("run_id", "report"))
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="audit_report_{run_id}.pdf"'},
    )

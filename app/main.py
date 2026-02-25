from __future__ import annotations

import asyncio
import hashlib
import json
import re
from html import escape
from datetime import datetime
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from playwright.async_api import async_playwright

from app.crawler import collect_page_artifacts
from app.config import settings
from app.keyso import KeysoClient
from app.llm import LLMClient
from app.metrika_api import MetrikaApiError, MetrikaClient
from app.metrics import TopPage, dataframe_preview, parse_metrics_files
from app.site_audit_prompts import DEFAULT_SITE_PROMPTS, DEFAULT_SITE_PROMPT_ENABLED

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="ai-аналитик")
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

COMPETITOR_MASTER_PROMPT = """МАСТЕР-ПРОМПТ (главный). Оркестратор трёх анализов с выводом в 3 вкладки + краткие выводы

Роль: ты — руководитель аналитики конверсии и UX-стратег, лауреат Webby. (Роль не выводи в ответе.)
Задача: обработать набор страниц конкурентов (скриншоты + полный текст подряд) и выдать результат в виде трёх «вкладок»:
Вкладка 1 — анализ структуры конкурентов (без смыслов и без сборки итоговой структуры).
Вкладка 2 — анализ смыслов/офферов (без карты блоков и без сборки итоговой структуры).
Вкладка 3 — формирование оптимальной структуры страницы на основании Вкладок 1–2.

Правило приоритета (обязательное)
- Этот мастер-промпт имеет высший приоритет над всеми прочими промптами в системе.
- Три промпта (структура/смыслы/итоговая структура) используй как внутренние инструкции ТОЛЬКО внутри соответствующей вкладки.
- Запреты из промптов 1 и 2 действуют только в их вкладках и не распространяются на Вкладку 3.

Входные данные
Я передаю N страниц. Для каждой страницы:
- URL/название
- Скриншоты (цельные, с прокруткой)
- Текст страницы подряд (может содержать меню/футер/повторы)
Регион один.

Режим работы (последовательно)
1) Выполни Вкладку 1 целиком.
2) Выполни Вкладку 2 целиком.
3) Выполни Вкладку 3, используя результаты Вкладок 1–2 (Режим А). Режим Б допускается только если данные недостаточны для Вкладок 1–2.

Общие правила (обязательные)
- Приоритет — скриншоты. Текст подряд — для формулировок и деталей.
- Фильтруй мусор: меню, футер, юридические строки, повторы.
- Не оценивай визуальную «красоту».
- Не выдумывай факты/цифры/условия. Если нет — «не указано».
- Нормализуй блоки и смыслы (без дублей).
- Встречаемость: X/N.
- Форматирование: НЕ используй таблицы; только списки и подзаголовки.
- Самодостаточность вкладок: вкладку можно прочитать отдельно; Вкладка 3 может кратко резюмировать ключевые частотности из 1–2 без полного дублирования.

Критерии «редкий, но полезный блок» (для Вкладок 1 и 3)
Блок «редкий, но полезный», если он:
- снимает риски/возражения
- усиливает доверие доказательствами
- повышает конверсию
- повышает ясность
- ускоряет выбор

ФОРМАТ ВЫВОДА: 3 ВКЛАДКИ (строго)

ВКЛАДКА 1. «Структура конкурентов»
0) Краткий вывод по структуре (обязательно)
- 3–7 пунктов: что почти у всех, что редко, главные паттерны структуры, главные структурные проблемы/трение (без смыслов).

1) Нормализованный словарь блоков (12–30)
- Блок: единое название
- Как распознать (1 строка)
- Что обычно внутри (1 строка)

2) Структура по каждой странице
Для каждой страницы:
- Блоки сверху вниз (нумерация)
- Формат/паттерн
- 3–5 наблюдений по структуре (без смыслов)

3) Частотность блоков по массиву
- Частые (X/N)
- Средней частоты (X/N)
- Редкие (X/N)

Контроль качества Вкладки 1:
- Нет смыслов/офферов/УТП.
- Нет рекомендаций «как нам сделать» и нет итоговой структуры.

ВКЛАДКА 2. «Смыслы: ценность, УТП, офферы, преимущества»
0) Краткий вывод по смыслам (обязательно)
- 3–7 пунктов: что чаще всего обещают, какие офферы доминируют, что уникального/редкого, где «вода» и где сильные формулировки.

Теги:
A) Ценность/результат
B) УТП
C) Оффер
D) Условия
E) Преимущества
F) Доказательства доверия
G) Снятие рисков/возражений

1) Смыслы по каждой странице
- Список смыслов по A–G
- 5–10 «цитат-фрагментов» (до 12–15 слов)

2) Частотный анализ смыслов по массиву
- По A–G: категории + X/N + 1 пример
- Редкие/уникальные: категория + X/N + почему это может быть преимуществом

3) Качество формулировок
- Топ-10 сильных
- Топ-10 слабых + улучшение

Контроль качества Вкладки 2:
- Нет карты структуры блоков.
- Нет сборки итоговой структуры страницы.

ВКЛАДКА 3. «Оптимальная структура страницы (на основании 1–2)»
0) Краткий вывод по предложенной структуре (обязательно)
- 3–7 пунктов: логика сценария, какие блоки критичны, где усилили доверие/снятие рисков, какие редкие блоки добавили и зачем, ожидаемые эффекты.

1) Итоговая структура (12–18 блоков)
- Список блоков сверху вниз.
- У каждого: обоснование + частотность/причина («частый 8/10», «редкий полезный 2/10», «закрывает риск»).

2) Спецификация каждого блока (для прототипа)
- Цель
- Элементы
- Смыслы/офферы (с привязкой к A–G)
- CTA + тип конверсии
- Формат

3) Редкие блоки, которые стоит добавить
- 5–10 пунктов: куда вставить + что внутри + какую проблему решает

4) Чек-лист рисков структуры (5–10)
- Что может «просесть» и как предотвратить

Запуск
- Не задавай вопросов.
- Начинай анализ сразу после получения страниц.
- Всегда указывай размер выборки N и используй X/N.
"""

COMPETITOR_PROMPT_1 = """ПРОМПТ 1. Анализ структуры страниц конкурентов (подзадача) + краткий вывод

Внутренняя роль (не выводи в ответе): ведущий UX-аналитик и конверсионный исследователь, лауреат Red Dot.
Задача: по страницам конкурентов выделить и нормализовать блоки/секции, описать структуру каждой страницы и сделать частотный срез по блокам. НЕ анализируй УТП/офферы/преимущества. НЕ предлагай итоговую структуру «нашей» страницы.

Вход
N страниц: URL/название, скриншоты, текст подряд.

Правила
- Приоритет — скриншоты; текст — для уточнений, с фильтрацией меню/футера/повторов.
- Не оценивай «красоту дизайна».
- Не выдумывай скрытые элементы.
- Не используй таблицы.
- Встречаемость: X/N.

Выход (строго)
0) Краткий вывод по структуре (обязательно)
- 3–7 пунктов: самые частые блоки, самые редкие, типовой порядок, где чаще ставят формы/CTA, главные проблемы структуры (без смыслов).

1) Нормализованный словарь блоков (12–30)
- Единое название + как распознать + что внутри (по 1 строке).

2) Структура по каждой странице
- Блоки сверху вниз (нумерация)
- Формат/паттерн блока
- 3–5 наблюдений по структуре (без смыслов)

3) Частотность блоков по массиву
- Частые (X/N)
- Средней частоты (X/N)
- Редкие (X/N)

Контроль качества
- Нет смыслов/офферов/УТП.
- Нет рекомендаций «как нам сделать».
- Нет итоговой структуры.

Начинай анализ сразу после получения страниц, если иное не указано мастер-промптом.
"""

COMPETITOR_PROMPT_2 = """ПРОМПТ 2. Анализ смыслов на страницах конкурентов (подзадача) + краткий вывод

Внутренняя роль (не выводи в ответе): маркетинговый стратег и конверсионный копирайтер, лауреат Effie.
Задача: извлечь и нормализовать смыслы: ценность, УТП, офферы/условия, преимущества, доказательства доверия, гарантии/снятие рисков. НЕ делай карту блоков. НЕ описывай структуру страницы. НЕ собирай итоговую структуру.

Вход
N страниц: URL/название, скриншоты, текст подряд.

Правила
- Скриншоты — источник акцентов; текст — источник формулировок и деталей. Фильтруй меню/футер/повторы.
- Нормализуй смыслы в категории, сохраняй 1 пример формулировки.
- Не выдумывай цифры/условия. Если нет — «не указано».
- Не используй таблицы.
- Встречаемость: X/N.

Теги
A) Ценность/результат
B) УТП
C) Оффер
D) Условия
E) Преимущества
F) Доказательства доверия
G) Снятие рисков/возражений

Выход (строго)
0) Краткий вывод по смыслам (обязательно)
- 3–7 пунктов: доминирующие обещания/офферы, типовые условия, что чаще всего используют как доверие, что уникального, где «вода» и как улучшать.

1) Смыслы по каждой странице
- Список смыслов по A–G
- 5–10 «цитат-фрагментов» (до 12–15 слов) с сильными формулировками

2) Частотный анализ смыслов по массиву
- По A–G: категории + X/N + 1 пример
- Редкие/уникальные: категория + X/N + почему это может быть преимуществом

3) Качество формулировок
- Топ-10 сильных
- Топ-10 слабых + улучшение

Контроль качества
- Нет карты блоков/структуры.
- Нет сборки итоговой структуры страницы.

Начинай анализ сразу после получения страниц, если иное не указано мастер-промптом.
"""

COMPETITOR_PROMPT_3 = """ПРОМПТ 3. Формирование оптимальной структуры страницы (подзадача) + краткий вывод

Внутренняя роль (не выводи в ответе): продуктовый маркетолог и UX-архитектор, лауреат Webby.
Задача: составить оптимальную структуру страницы (сверху вниз) и описать каждый блок так, чтобы по этому можно было собрать прототип и тексты.

Вход
Вариант А: результаты анализа структуры (блоки + X/N) и анализа смыслов (категории смыслов + X/N).
Вариант Б: страницы конкурентов (скриншоты + текст подряд) — тогда извлеки минимально достаточные частотности коротко.

Принципы сборки
- Основа — частые блоки.
- Добавь редкие полезные, если усиливают доверие/снятие рисков/ясность/конверсию.
- Сценарий: что это → для кого → почему верить → как работает/что внутри → условия → доказательства → снятие рисков → CTA.
- 3–5 точек входа (CTA) под разные намерения.
- Не выдумывай факты/цифры: «[указать]».
- Не используй таблицы.

Выход (строго)
0) Краткий вывод по предложенной структуре (обязательно)
- 3–7 пунктов: какие блоки «обязательные», чем усилили доверие, какие редкие блоки добавили и зачем, как распределили CTA, где закрыли риски/возражения.

1) Итоговая структура (12–18 блоков)
- Блоки сверху вниз + обоснование («частый 8/10», «редкий полезный 2/10», «закрывает риск»).

2) Спецификация каждого блока (для прототипа)
- Цель
- Элементы
- Смыслы/офферы (с привязкой к A–G)
- CTA + тип конверсии
- Формат

3) Редкие блоки, которые стоит добавить
- 5–10 пунктов: куда вставить + что внутри + какую проблему решает

4) Чек-лист рисков структуры (5–10)
- Что может «просесть» и как предотвратить

Контроль качества
- Структура = частые + обоснованные редкие полезные.
- Смыслы распределены по блокам и привязаны к A–G.
- Нет выдуманных фактов/условий.

Начинай работу сразу после получения входных данных, если иное не указано мастер-промптом.
"""

COMPETITOR_TABLE_BLOCKS_PROMPT = """ПРОМПТ 4. Нормализованный список блоков конкурентов

Задача: на основании результата «Анализ структуры» подготовить компактный нормализованный список блоков по каждому сайту для сравнения.

Правила:
- Нормализуй названия: один блок = одно единое имя.
- Убирай дубли и синонимы.
- Не добавляй объяснения, выводы и рекомендации.
- Только фактические блоки, которые реально есть в анализе.

Формат ответа (строго):
Для каждого сайта:
<полный URL сайта>
1. <Стандартизированное название блока>
2. <Стандартизированное название блока>
...
"""

COMPETITOR_RUNTIME_GUARD = """Служебное уточнение для выполнения в приложении:
- Начинай анализ сразу по переданным данным.
- Не жди дополнительных команд пользователя («СТАРТ», «продолжай» и т.п.).
- Не задавай встречные вопросы; если данных не хватает, явно отметь ограничения в выводе.
- Строго соблюдай границы текущего этапа и формат ответа этого промпта.
"""

COMPETITOR_STAGE_1_GUARD = """ГРАНИЦЫ ЭТАПА 1 (обязательно):
- Это только вкладка «Анализ сайтов» (структура).
- Запрещено: анализ смыслов/офферов/УТП и любые рекомендации итоговой структуры.
- Не выводи заголовки «ВКЛАДКА 1/2/3».
- Если в черновике есть смысловые выводы, удаляй их перед финальным ответом.
"""

COMPETITOR_STAGE_2_GUARD = """ГРАНИЦЫ ЭТАПА 2 (обязательно):
- Это только вкладка «Анализ смыслов».
- Запрещено: карта/последовательность блоков, структура страницы, рекомендации по итоговой структуре.
- Не выводи заголовки «ВКЛАДКА 1/2/3».
- Если в черновике есть структурный разбор (блоки сверху вниз, паттерны секций), удаляй его перед финальным ответом.
"""

COMPETITOR_STAGE_3_GUARD = """ГРАНИЦЫ ЭТАПА 3 (обязательно):
- Это только вкладка «Предложение по структуре».
- Основание: результаты этапов 1 и 2.
- Разрешены структурные рекомендации и сборка итоговой структуры.
- Не выводи заголовки «ВКЛАДКА 1/2/3».
"""

TOP10_PROMPT_1 = """ПРОМПТ 1. Анализ структуры страниц по top-10 из поисковой выдачи

Внутренняя роль (не выводи в ответе): ведущий UX-аналитик и конверсионный исследователь, лауреат Red Dot.
Задача: по страницам из top-10 (полученным по списку поисковых запросов) выделить и нормализовать блоки/секции, описать структуру каждой страницы и сделать частотный срез по блокам.
НЕ анализируй УТП/офферы/преимущества. НЕ предлагай итоговую структуру «нашей» страницы.

Вход:
- список поисковых запросов;
- итоговый список URL;
- скриншоты и текст страниц.

Выход (строго):
0) Краткий вывод по структуре (3–7 пунктов)
1) Нормализованный словарь блоков (12–30)
2) Структура по каждой странице
3) Частотность блоков по массиву (X/N)
"""

TOP10_PROMPT_2 = """ПРОМПТ 2. Формирование оптимальной структуры страницы по top-10

Внутренняя роль (не выводи в ответе): продуктовый маркетолог и UX-архитектор, лауреат Webby.
Задача: на основании анализа структуры страниц из top-10 собрать оптимальную структуру страницы.

Вход:
- список поисковых запросов;
- итоговый список URL;
- результаты анализа структуры страниц.

Выход (строго):
0) Краткий вывод по предложенной структуре (3–7 пунктов)
1) Итоговая структура (12–18 блоков) с обоснованием (X/N или причина)
2) Спецификация каждого блока (цель, элементы, смыслы/офферы, CTA, формат)
3) Редкие блоки, которые стоит добавить (5–10)
4) Чек-лист рисков структуры (5–10)
"""

TOP10_TABLE_BLOCKS_PROMPT = """ПРОМПТ 3. Подготовка данных для табличного сравнения блоков

Задача: на основании результата анализа структуры подготовь компактный вывод для таблицы в СТРОГОМ формате.
Цель: чтобы можно было корректно сравнить сайты по единым (стандартизированным) названиям блоков.

Правила:
- Нормализуй названия блоков: одно и то же называть одинаково.
- Убирай дубли и синонимы.
- Без лишних объяснений и воды.
- Используй только блоки, которые реально встречаются в анализе.

Формат ответа (строго):
Для каждого сайта:
<полный URL сайта>
1. <Стандартизированное название блока>
2. <Стандартизированное название блока>
...

Требования к названиям:
- Коротко и однозначно.
- Одна сущность — одно имя.
- Примеры корректных имён:
  «Глобальная шапка», «Хлебные крошки», «Заголовок категории», «Подкатегории», «Фильтры», «Сортировка», «Счётчик товаров», «Список товаров», «Карточка товара», «Кнопка в корзину», «Пагинация», «Футер».
"""

TOP10_TABLE_STRUCTURE_PROMPT = """ПРОМПТ 4. Подготовка структуры для таблицы (блок + комментарий)

Задача: на основании предложенной структуры сделать строгий список строк для таблицы:
столбец 1 — название блока, столбец 2 — комментарий по блоку.

Правила:
- Только итоговая структура, без вступлений и без общих рассуждений.
- Названия блоков стандартизированы и без дублей.
- Комментарии короткие, практичные, 1 строка на блок.

Формат ответа (строго):
1. <Название блока> — <Комментарий по блоку>
2. <Название блока> — <Комментарий по блоку>
...
"""

TOP10_REGION_SUGGESTIONS = [
    {"id": 213, "name": "Москва"},
    {"id": 2, "name": "Санкт-Петербург"},
    {"id": 50, "name": "Пермь"},
    {"id": 54, "name": "Екатеринбург"},
    {"id": 43, "name": "Казань"},
    {"id": 51, "name": "Самара"},
    {"id": 47, "name": "Нижний Новгород"},
    {"id": 65, "name": "Новосибирск"},
    {"id": 66, "name": "Омск"},
    {"id": 62, "name": "Красноярск"},
    {"id": 38, "name": "Краснодар"},
    {"id": 56, "name": "Челябинск"},
    {"id": 39, "name": "Ростов-на-Дону"},
    {"id": 193, "name": "Воронеж"},
    {"id": 194, "name": "Волгоград"},
    {"id": 67, "name": "Томск"},
    {"id": 53, "name": "Тюмень"},
    {"id": 172, "name": "Уфа"},
    {"id": 22, "name": "Владивосток"},
    {"id": 10, "name": "Архангельск"},
    {"id": 20, "name": "Калининград"},
    {"id": 157, "name": "Ярославль"},
    {"id": 7, "name": "Астрахань"},
    {"id": 205, "name": "Ижевск"},
    {"id": 969, "name": "Алматы"},
    {"id": 143, "name": "Киев"},
]


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "result": None,
            "asset_version": datetime.now().strftime("%Y%m%d%H%M%S"),
            "default_site_prompts": DEFAULT_SITE_PROMPTS,
            "default_site_prompt_enabled": DEFAULT_SITE_PROMPT_ENABLED,
        },
    )


@app.get("/competitors", response_class=HTMLResponse)
async def competitors(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "competitors.html",
        {
            "asset_version": datetime.now().strftime("%Y%m%d%H%M%S"),
            "default_competitor_prompt_1": COMPETITOR_PROMPT_1,
            "default_competitor_prompt_2": COMPETITOR_PROMPT_2,
            "default_competitor_prompt_3": COMPETITOR_PROMPT_3,
            "default_competitor_table_blocks_prompt": COMPETITOR_TABLE_BLOCKS_PROMPT,
        },
    )


@app.get("/top10-structure", response_class=HTMLResponse)
async def top10_structure(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "top10_structure.html",
        {
            "asset_version": datetime.now().strftime("%Y%m%d%H%M%S"),
            "default_top10_prompt_1": TOP10_PROMPT_1,
            "default_top10_prompt_2": TOP10_PROMPT_2,
            "default_top10_table_blocks_prompt": TOP10_TABLE_BLOCKS_PROMPT,
            "default_top10_table_structure_prompt": TOP10_TABLE_STRUCTURE_PROMPT,
            "default_region_id": "225",
            "default_region_name": "Россия",
        },
    )


def _is_enabled(raw: str | None, default: bool = True) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "on", "yes"}


def _strip_technical_notes(raw_text: str) -> tuple[str, str]:
    text = raw_text.strip()
    marker = "Технические заметки"
    if marker not in text:
        return text, ""
    body, notes = text.split(marker, 1)
    cleaned_notes = notes.strip().lstrip(":").strip()
    return body.strip(), cleaned_notes


def _validate_prompt_text(prompt_text: str, prompt_key: str) -> tuple[str, list[str]]:
    warnings: list[str] = []
    text = (prompt_text or "").strip()
    lowered = text.lower()

    if len(text) < 80:
        warnings.append("слишком короткий промт — возможен слабый контроль формата")

    if prompt_key == "section_3":
        if "проблема:" not in lowered or "почему важно:" not in lowered:
            warnings.append("в разделе UX нет явного 4-строчного формата проблемы")

    if prompt_key == "section_1" and "220" not in lowered:
        warnings.append("в саммари не зафиксирован лимит 220 слов")

    leakage_markers = [
        "используй каркас",
        "формат вывода",
        "ты — ",
        "лимит:",
        "(5–7",
    ]
    if not any(marker in lowered for marker in leakage_markers):
        warnings.append("нет явных анти-утечек мета-инструкций в текст результата")

    status = "ok" if not warnings else "warn"
    return status, warnings


async def _run_analysis(
    files: list[UploadFile] | None = File(None),
    audit_mode: str = Form("full"),
    page_url: str = Form(""),
    metrika_counter_id: int = Form(0),
    top_n: int = Form(5),
    prompt_contract: str = Form(""),
    prompt_section_0: str = Form(""),
    prompt_section_2: str = Form(""),
    prompt_section_3: str = Form(""),
    prompt_section_4: str = Form(""),
    prompt_section_5: str = Form(""),
    prompt_section_6: str = Form(""),
    prompt_section_1: str = Form(""),
    prompt_service_assemble: str = Form(""),
    prompt_service_repair: str = Form(""),
    prompt_jtbd_research: str = Form(""),
    enabled_contract: str | None = Form(None),
    enabled_section_0: str | None = Form(None),
    enabled_section_2: str | None = Form(None),
    enabled_section_3: str | None = Form(None),
    enabled_section_4: str | None = Form(None),
    enabled_section_5: str | None = Form(None),
    enabled_section_6: str | None = Form(None),
    enabled_section_1: str | None = Form(None),
    enabled_service_assemble: str | None = Form(None),
    enabled_service_repair: str | None = Form(None),
    enabled_jtbd_research: str | None = Form(None),
    # Backward compatibility with legacy fields.
    role_prompt: str = Form(""),
    metrics_prompt: str = Form(""),
    audience_prompt: str = Form(""),
    pages_prompt: str = Form(""),
) -> dict:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_upload_dir = UPLOADS_DIR / run_id
    run_screens_dir = SCREENSHOTS_DIR / run_id
    run_upload_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "run_id": run_id,
        "audit_mode": audit_mode,
        "metrika_counter_id": metrika_counter_id,
        "metrika_counter_name": "",
        "metrika_effective_period_days": None,
        "metrika_effective_accuracy": "",
        "metrika_query_profile": {},
        "top_pages": [],
        "final_summary": "",
        "metrics_analysis": "",
        "audience_analysis": "",
        "pages_analysis": "",
        "report_md": "",
        "report_sections": {},
        "violations": "",
        "errors": [],
    }

    try:
        llm = LLMClient()
        prompt_values = {
            "contract": (prompt_contract or role_prompt or DEFAULT_SITE_PROMPTS["contract"]).strip(),
            "section_0": (prompt_section_0 or DEFAULT_SITE_PROMPTS["section_0"]).strip(),
            "section_2": (prompt_section_2 or metrics_prompt or DEFAULT_SITE_PROMPTS["section_2"]).strip(),
            "section_3": (prompt_section_3 or pages_prompt or DEFAULT_SITE_PROMPTS["section_3"]).strip(),
            "section_4": (prompt_section_4 or DEFAULT_SITE_PROMPTS["section_4"]).strip(),
            "section_5": (prompt_section_5 or DEFAULT_SITE_PROMPTS["section_5"]).strip(),
            "section_6": (prompt_section_6 or DEFAULT_SITE_PROMPTS["section_6"]).strip(),
            "section_1": (prompt_section_1 or FINAL_SUMMARY_PROMPT or DEFAULT_SITE_PROMPTS["section_1"]).strip(),
            "service_assemble": (prompt_service_assemble or DEFAULT_SITE_PROMPTS["service_assemble"]).strip(),
            "service_repair": (prompt_service_repair or DEFAULT_SITE_PROMPTS["service_repair"]).strip(),
            "jtbd_research": (prompt_jtbd_research or audience_prompt or DEFAULT_SITE_PROMPTS["jtbd_research"]).strip(),
        }
        enabled = {
            "contract": _is_enabled(enabled_contract, DEFAULT_SITE_PROMPT_ENABLED["contract"]),
            "section_0": _is_enabled(enabled_section_0, DEFAULT_SITE_PROMPT_ENABLED["section_0"]),
            "section_2": _is_enabled(enabled_section_2, DEFAULT_SITE_PROMPT_ENABLED["section_2"]),
            "section_3": _is_enabled(enabled_section_3, DEFAULT_SITE_PROMPT_ENABLED["section_3"]),
            "section_4": _is_enabled(enabled_section_4, DEFAULT_SITE_PROMPT_ENABLED["section_4"]),
            "section_5": _is_enabled(enabled_section_5, DEFAULT_SITE_PROMPT_ENABLED["section_5"]),
            "section_6": _is_enabled(enabled_section_6, DEFAULT_SITE_PROMPT_ENABLED["section_6"]),
            "section_1": _is_enabled(enabled_section_1, DEFAULT_SITE_PROMPT_ENABLED["section_1"]),
            "service_assemble": _is_enabled(enabled_service_assemble, DEFAULT_SITE_PROMPT_ENABLED["service_assemble"]),
            "service_repair": _is_enabled(enabled_service_repair, DEFAULT_SITE_PROMPT_ENABLED["service_repair"]),
            "jtbd_research": _is_enabled(enabled_jtbd_research, DEFAULT_SITE_PROMPT_ENABLED["jtbd_research"]),
        }

        async def run_prompt(prompt_key: str, payload: dict) -> str:
            base_prompt = prompt_values[prompt_key]
            if enabled["contract"] and prompt_key != "contract":
                system_prompt = f"{prompt_values['contract']}\n\n{base_prompt}"
            else:
                system_prompt = base_prompt
            return await llm.analyze(system_prompt, payload)

        combined_df = None
        metrics_payload: dict[str, object] = {}
        sources: list[str] = []
        period = "не указан"
        saved_paths: list[Path] = []

        if audit_mode == "screenshot":
            normalized_url = page_url.strip()
            if not normalized_url.startswith(("http://", "https://")):
                result["errors"].append("Для режима «Аудит по ссылке» укажите корректную ссылку на страницу (http/https).")
                return result
            top_pages = [TopPage(url=normalized_url, visits=0)]
            metrics_payload = {"table_preview": "В данных нет метрики для режима «Аудит по ссылке».", "top_pages": []}
            sources = ["Скриншоты desktop/mobile", "Текст страниц"]
            period = "не применимо (аудит по ссылке)"
        elif audit_mode == "metrika":
            if metrika_counter_id <= 0:
                result["errors"].append("Для режима «Аудит по Метрике» выберите счётчик.")
                return result
            metrika_client = MetrikaClient()
            combined_df, top_pages, counter_meta = metrika_client.load_metrics_snapshot(
                counter_id=metrika_counter_id,
                top_n=top_n,
            )
            result["metrika_counter_id"] = counter_meta.get("counter_id", metrika_counter_id)
            result["metrika_counter_name"] = str(counter_meta.get("counter_name", ""))
            result["metrika_effective_period_days"] = counter_meta.get("effective_period_days")
            result["metrika_effective_accuracy"] = str(counter_meta.get("effective_accuracy", ""))
            result["metrika_query_profile"] = counter_meta.get("query_profile", {})
            if not top_pages:
                result["errors"].append("Не удалось получить топовые страницы входа из API Яндекс.Метрики.")
                return result

            metrics_payload = {
                "source": "yandex_metrika_api",
                "counter": counter_meta,
                "top_pages": [{"url": item.url, "visits": item.visits} for item in top_pages],
                "table_preview": dataframe_preview(combined_df, limit=80),
            }
            sources = ["Метрика API", "Скриншоты desktop/mobile", "Текст страниц"]
            days = int(counter_meta.get("effective_period_days") or 90)
            accuracy = str(counter_meta.get("effective_accuracy") or "full")
            period = f"последние {days} дней (API Метрики, accuracy={accuracy})"
        else:
            if not files:
                result["errors"].append("Для режима «Аудит по выгрузке» загрузите Excel-файлы Метрики.")
                return result

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
                "files": [path.name for path in saved_paths],
            }
            sources = ["Выгрузки Яндекс.Метрики (Excel)", "Скриншоты desktop/mobile", "Текст страниц"]
            period = "из загруженной выгрузки"

        artifacts = await collect_page_artifacts(top_pages, run_screens_dir)
        for item in artifacts:
            item["desktop_screenshot"] = str(Path(item["desktop_screenshot"]).relative_to(BASE_DIR)).replace("\\", "/")
            item["mobile_screenshot"] = str(Path(item["mobile_screenshot"]).relative_to(BASE_DIR)).replace("\\", "/")
        result["top_pages"] = artifacts
        site = "-"
        if artifacts:
            try:
                site = re.sub(r"^www\.", "", artifacts[0]["url"].split("//", 1)[1].split("/", 1)[0])
            except Exception:  # noqa: BLE001
                site = artifacts[0].get("url", "-")

        sections: dict[str, str] = {}

        if enabled["section_0"]:
            sections["0"] = await run_prompt(
                "section_0",
                {
                    "site": site,
                    "pages": [item["url"] for item in artifacts],
                    "period": period,
                    "sources": sources,
                },
            )

        if enabled["section_2"]:
            sections["2"] = await run_prompt(
                "section_2",
                {
                    "metrika_data": metrics_payload,
                    "top_pages": [{"url": item["url"], "visits": item["visits"]} for item in artifacts],
                },
            )
        result["metrics_analysis"] = sections.get("2", "")

        if enabled["section_3"]:
            sections["3"] = await run_prompt(
                "section_3",
                {
                    "pages": artifacts,
                    "screenshots_payload": [
                        {
                            "url": item["url"],
                            "desktop_screenshot": item.get("desktop_screenshot", ""),
                            "mobile_screenshot": item.get("mobile_screenshot", ""),
                        }
                        for item in artifacts
                    ],
                },
            )
        result["pages_analysis"] = sections.get("3", "")

        if enabled["section_4"]:
            sections["4"] = await run_prompt(
                "section_4",
                {
                    "section2_md": sections.get("2", ""),
                    "section3_md": sections.get("3", ""),
                },
            )
        result["audience_analysis"] = sections.get("4", "")

        if enabled["section_5"]:
            sections["5"] = await run_prompt(
                "section_5",
                {
                    "section2_md": sections.get("2", ""),
                    "section3_md": sections.get("3", ""),
                    "section4_md": sections.get("4", ""),
                },
            )

        if enabled["section_6"]:
            sections["6"] = await run_prompt(
                "section_6",
                {
                    "pages": [item["url"] for item in artifacts],
                    "screenshots_index": [
                        {
                            "url": item["url"],
                            "desktop": item.get("desktop_screenshot", ""),
                            "mobile": item.get("mobile_screenshot", ""),
                        }
                        for item in artifacts
                    ],
                    "exports_index": [path.name for path in saved_paths],
                },
            )

        if enabled["section_1"]:
            sections["1"] = await run_prompt(
                "section_1",
                {
                    "section2_md": sections.get("2", ""),
                    "section3_md": sections.get("3", ""),
                    "section5_md": sections.get("5", ""),
                },
            )
        result["final_summary"] = sections.get("1", "")

        if enabled["jtbd_research"]:
            result["jtbd_research"] = await run_prompt(
                "jtbd_research",
                {
                    "metrika_data": metrics_payload,
                    "pages": artifacts,
                },
            )

        final_report_md = ""
        if enabled["service_assemble"]:
            final_report_md = await run_prompt(
                "service_assemble",
                {
                    "site": site,
                    "pages_count": len(artifacts),
                    "section0_md": sections.get("0", ""),
                    "section1_md": sections.get("1", ""),
                    "section2_md": sections.get("2", ""),
                    "section3_md": sections.get("3", ""),
                    "section4_md": sections.get("4", ""),
                    "section5_md": sections.get("5", ""),
                    "section6_md": sections.get("6", ""),
                },
            )
        else:
            ordered = ["0", "1", "2", "3", "4", "5", "6"]
            final_report_md = "\n\n".join(sections[key] for key in ordered if sections.get(key))

        repaired_report_md = final_report_md
        violations = ""
        if enabled["service_repair"] and final_report_md.strip():
            repaired_raw = await run_prompt("service_repair", {"final_report_md": final_report_md})
            repaired_report_md, violations = _strip_technical_notes(repaired_raw)

        result["report_sections"] = sections
        result["report_md"] = repaired_report_md or final_report_md
        result["violations"] = violations

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
    metrika_counter_id: int = Form(0),
    top_n: int = Form(5),
    prompt_contract: str = Form(""),
    prompt_section_0: str = Form(""),
    prompt_section_2: str = Form(""),
    prompt_section_3: str = Form(""),
    prompt_section_4: str = Form(""),
    prompt_section_5: str = Form(""),
    prompt_section_6: str = Form(""),
    prompt_section_1: str = Form(""),
    prompt_service_assemble: str = Form(""),
    prompt_service_repair: str = Form(""),
    prompt_jtbd_research: str = Form(""),
    enabled_contract: str | None = Form(None),
    enabled_section_0: str | None = Form(None),
    enabled_section_2: str | None = Form(None),
    enabled_section_3: str | None = Form(None),
    enabled_section_4: str | None = Form(None),
    enabled_section_5: str | None = Form(None),
    enabled_section_6: str | None = Form(None),
    enabled_section_1: str | None = Form(None),
    enabled_service_assemble: str | None = Form(None),
    enabled_service_repair: str | None = Form(None),
    enabled_jtbd_research: str | None = Form(None),
    role_prompt: str = Form(""),
    metrics_prompt: str = Form(""),
    audience_prompt: str = Form(""),
    pages_prompt: str = Form(""),
) -> JSONResponse:
    result = await _run_analysis(
        files=files,
        audit_mode=audit_mode,
        page_url=page_url,
        metrika_counter_id=metrika_counter_id,
        top_n=top_n,
        prompt_contract=prompt_contract,
        prompt_section_0=prompt_section_0,
        prompt_section_2=prompt_section_2,
        prompt_section_3=prompt_section_3,
        prompt_section_4=prompt_section_4,
        prompt_section_5=prompt_section_5,
        prompt_section_6=prompt_section_6,
        prompt_section_1=prompt_section_1,
        prompt_service_assemble=prompt_service_assemble,
        prompt_service_repair=prompt_service_repair,
        prompt_jtbd_research=prompt_jtbd_research,
        enabled_contract=enabled_contract,
        enabled_section_0=enabled_section_0,
        enabled_section_2=enabled_section_2,
        enabled_section_3=enabled_section_3,
        enabled_section_4=enabled_section_4,
        enabled_section_5=enabled_section_5,
        enabled_section_6=enabled_section_6,
        enabled_section_1=enabled_section_1,
        enabled_service_assemble=enabled_service_assemble,
        enabled_service_repair=enabled_service_repair,
        enabled_jtbd_research=enabled_jtbd_research,
        role_prompt=role_prompt,
        metrics_prompt=metrics_prompt,
        audience_prompt=audience_prompt,
        pages_prompt=pages_prompt,
    )
    return JSONResponse(content=result)


@app.get("/metrika/counters")
async def metrika_counters() -> JSONResponse:
    try:
        client = MetrikaClient()
        counters = client.list_counters()
        payload = {
            "items": [
                {
                    "id": counter.id,
                    "name": counter.name,
                    "site": counter.site,
                }
                for counter in counters
            ],
        }
        return JSONResponse(content=payload)
    except (MetrikaApiError, RuntimeError) as exc:
        return JSONResponse(status_code=400, content={"items": [], "error": str(exc)})


@app.post("/prompt/validate")
async def prompt_validate(payload: dict = Body(...)) -> JSONResponse:
    prompt_key = str(payload.get("key", "")).strip()
    prompt_text = str(payload.get("prompt", "")).strip()
    if not prompt_key:
        return JSONResponse(status_code=400, content={"error": "Не передан ключ промта"})
    if not prompt_text:
        return JSONResponse(status_code=400, content={"error": "Промт пустой"})

    status, warnings = _validate_prompt_text(prompt_text, prompt_key)
    return JSONResponse(
        content={
            "status": status,
            "warnings": warnings,
        }
    )


def _parse_urls(raw: str) -> list[str]:
    candidates = [item.strip() for item in raw.replace(",", "\n").splitlines()]
    urls: list[str] = []
    for url in candidates:
        if not url:
            continue
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        urls.append(url)
    unique_urls: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        unique_urls.append(url)
    return unique_urls


def _parse_queries(raw: str) -> list[str]:
    return [item.strip() for item in raw.splitlines() if item.strip()]


def _top10_cache_file() -> Path:
    return DATA_DIR / "top10_cache.json"


def _top10_cache_key(queries: list[str], region_id: int) -> str:
    normalized = "\n".join(query.strip().lower() for query in queries if query.strip())
    raw = f"{region_id}|{normalized}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _load_top10_cache(queries: list[str], region_id: int) -> list[dict[str, int | str]]:
    cache_path = _top10_cache_file()
    if not cache_path.exists():
        return []
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    key = _top10_cache_key(queries, region_id)
    rows = raw.get(key, []) if isinstance(raw, dict) else []
    if not isinstance(rows, list):
        return []
    cleaned: list[dict[str, int | str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url", "")).strip()
        if not url:
            continue
        try:
            count = int(row.get("count", 0) or 0)
        except (TypeError, ValueError):
            count = 0
        cleaned.append({"url": url, "count": count})
    return cleaned


def _save_top10_cache(queries: list[str], region_id: int, rows: list[dict[str, int | str]]) -> None:
    cache_path = _top10_cache_file()
    key = _top10_cache_key(queries, region_id)
    data: dict[str, list[dict[str, int | str]]] = {}
    if cache_path.exists():
        try:
            loaded = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:  # noqa: BLE001
            data = {}
    data[key] = rows[:10]
    cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _suggest_regions(raw_query: str, limit: int = 12) -> list[dict[str, int | str]]:
    query = raw_query.strip().lower()
    if not query:
        return TOP10_REGION_SUGGESTIONS[:limit]

    def rank(item: dict[str, int | str]) -> tuple[int, str]:
        name = str(item["name"]).lower()
        if name.startswith(query):
            return (0, name)
        if query in name:
            return (1, name)
        return (2, name)

    matched = [item for item in TOP10_REGION_SUGGESTIONS if query in str(item["name"]).lower()]
    matched.sort(key=rank)
    return matched[:limit]


@app.get("/top10-region-suggest")
async def top10_region_suggest(q: str = Query("", min_length=0, max_length=100)) -> JSONResponse:
    return JSONResponse(content={"items": _suggest_regions(q)})


def _normalize_competitor_prompt(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    # Убираем инструкции для ручного чат-режима, конфликтующие с автоматическим запуском в приложении.
    patterns = [
        r"(?im)^.*\bжд[аи]\b.*\bстарт\b.*$",
        r"(?im)^.*\bпосле того как я напишу\b.*$",
        r"(?im)^.*\bдалее жди\b.*$",
        r"(?im)^.*\bне задавай вопросов\b.*$",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


@app.post("/top10-urls")
async def top10_urls(
    search_queries: str = Form(...),
    region_id: str = Form("225"),
    top_n: int = Form(10),
) -> JSONResponse:
    result = {
        "queries": [],
        "urls": [],
        "errors": [],
    }
    queries = _parse_queries(search_queries)
    result["queries"] = queries
    if not queries:
        result["errors"].append("Добавьте хотя бы один поисковый запрос.")
        return JSONResponse(content=result)
    try:
        normalized_region = int((region_id or "225").strip())
    except ValueError:
        normalized_region = 225
    try:
        client = KeysoClient()
        top_rows = await asyncio.to_thread(client.get_top_urls, queries, normalized_region, max(1, min(top_n, 10)))
        result["urls"] = [{"url": row.url, "count": row.count} for row in top_rows]
        if result["urls"]:
            _save_top10_cache(queries, normalized_region, result["urls"])
    except Exception as exc:  # noqa: BLE001
        cached = _load_top10_cache(queries, normalized_region)
        if cached:
            result["urls"] = cached[: max(1, min(top_n, 10))]
            result["errors"].append(f"{exc}. Использован кэш последней успешной выборки.")
        else:
            result["errors"].append(str(exc))
    return JSONResponse(content=result)


@app.post("/analyze-competitors")
async def analyze_competitors(
    competitor_urls: str = Form(...),
    competitor_prompt_1: str = Form(...),
    competitor_prompt_2: str = Form(...),
    competitor_prompt_3: str = Form(...),
    competitor_table_blocks_prompt: str = Form(...),
) -> JSONResponse:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_screens_dir = SCREENSHOTS_DIR / f"competitors_{run_id}"
    result = {
        "run_id": run_id,
        "pages": [],
        "analysis_sites": "",
        "normalized_blocks": "",
        "analysis_meanings": "",
        "structure_proposal": "",
        "table_blocks_output": "",
        "errors": [],
    }

    urls = _parse_urls(competitor_urls)
    if not urls:
        result["errors"].append("Добавьте хотя бы одну корректную ссылку для анализа конкурентов.")
        return JSONResponse(content=result)
    competitor_prompt_1 = _normalize_competitor_prompt(competitor_prompt_1)
    competitor_prompt_2 = _normalize_competitor_prompt(competitor_prompt_2)
    competitor_prompt_3 = _normalize_competitor_prompt(competitor_prompt_3)
    competitor_table_blocks_prompt = _normalize_competitor_prompt(competitor_table_blocks_prompt)

    if not competitor_prompt_1 or not competitor_prompt_2 or not competitor_prompt_3 or not competitor_table_blocks_prompt:
        result["errors"].append("Заполните все промпты для анализа конкурентов.")
        return JSONResponse(content=result)

    try:
        top_pages = [TopPage(url=url, visits=0) for url in urls]
        artifacts = await collect_page_artifacts(top_pages, run_screens_dir)
        for item in artifacts:
            item["desktop_screenshot"] = str(Path(item["desktop_screenshot"]).relative_to(BASE_DIR)).replace("\\", "/")
            item["mobile_screenshot"] = str(Path(item["mobile_screenshot"]).relative_to(BASE_DIR)).replace("\\", "/")
        result["pages"] = artifacts

        llm = LLMClient()
        common_payload = {
            "pages": artifacts,
            "input_urls": urls,
        }
        result["analysis_sites"] = await llm.analyze(
            f"{COMPETITOR_RUNTIME_GUARD}\n\n{COMPETITOR_STAGE_1_GUARD}\n\n{competitor_prompt_1}",
            common_payload,
        )
        result["normalized_blocks"] = await llm.analyze(
            f"{COMPETITOR_RUNTIME_GUARD}\n\n{COMPETITOR_STAGE_1_GUARD}\n\n{competitor_table_blocks_prompt}",
            {
                **common_payload,
                "analysis_sites": result["analysis_sites"],
            },
        )
        result["table_blocks_output"] = result["normalized_blocks"]
        result["analysis_meanings"] = await llm.analyze(
            f"{COMPETITOR_RUNTIME_GUARD}\n\n{COMPETITOR_STAGE_2_GUARD}\n\n{competitor_prompt_2}",
            common_payload,
        )
        result["structure_proposal"] = await llm.analyze(
            f"{COMPETITOR_RUNTIME_GUARD}\n\n{COMPETITOR_STAGE_3_GUARD}\n\n{competitor_prompt_3}",
            {
                **common_payload,
                "analysis_sites": result["analysis_sites"],
                "analysis_meanings": result["analysis_meanings"],
            },
        )

        (DATA_DIR / f"competitors_report_{run_id}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))

    return JSONResponse(content=result)


@app.post("/analyze-top10-structure")
async def analyze_top10_structure(
    search_queries: str = Form(...),
    region_id: str = Form("225"),
    top10_urls: str = Form(""),
    top10_prompt_1: str = Form(...),
    top10_prompt_2: str = Form(...),
    top10_table_blocks_prompt: str = Form(...),
    top10_table_structure_prompt: str = Form(...),
) -> JSONResponse:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_screens_dir = SCREENSHOTS_DIR / f"top10_{run_id}"
    result = {
        "run_id": run_id,
        "queries": [],
        "urls": [],
        "pages": [],
        "analysis_structure": "",
        "structure_proposal": "",
        "table_blocks_output": "",
        "table_structure_output": "",
        "errors": [],
    }
    queries = _parse_queries(search_queries)
    result["queries"] = queries
    if not queries:
        result["errors"].append("Добавьте хотя бы один поисковый запрос.")
        return JSONResponse(content=result)

    top10_prompt_1 = _normalize_competitor_prompt(top10_prompt_1)
    top10_prompt_2 = _normalize_competitor_prompt(top10_prompt_2)
    top10_table_blocks_prompt = _normalize_competitor_prompt(top10_table_blocks_prompt)
    top10_table_structure_prompt = _normalize_competitor_prompt(top10_table_structure_prompt)
    if not top10_prompt_1 or not top10_prompt_2 or not top10_table_blocks_prompt or not top10_table_structure_prompt:
        result["errors"].append("Заполните все промпты top-10: анализ, структура и табличные форматы.")
        return JSONResponse(content=result)

    manual_urls = _parse_urls(top10_urls)
    url_counts: list[dict[str, int | str]] = []
    if manual_urls:
        url_counts = [{"url": url, "count": 0} for url in manual_urls[:10]]
    else:
        try:
            normalized_region = int((region_id or "225").strip())
        except ValueError:
            normalized_region = 225
        try:
            client = KeysoClient()
            top_rows = await asyncio.to_thread(client.get_top_urls, queries, normalized_region, 10)
            url_counts = [{"url": row.url, "count": row.count} for row in top_rows]
            if url_counts:
                _save_top10_cache(queries, normalized_region, url_counts)
        except Exception as exc:  # noqa: BLE001
            cached = _load_top10_cache(queries, normalized_region)
            if cached:
                url_counts = cached[:10]
                result["errors"].append(f"{exc}. Использован кэш последней успешной выборки.")
            else:
                result["errors"].append(str(exc))
                return JSONResponse(content=result)

    if not url_counts:
        result["errors"].append("Не удалось получить URL для анализа. Проверьте запросы или заполните список вручную.")
        return JSONResponse(content=result)
    result["urls"] = url_counts

    try:
        top_pages = [TopPage(url=str(item["url"]), visits=int(item.get("count") or 0)) for item in url_counts]
        artifacts = await collect_page_artifacts(top_pages, run_screens_dir)
        for item in artifacts:
            item["desktop_screenshot"] = str(Path(item["desktop_screenshot"]).relative_to(BASE_DIR)).replace("\\", "/")
            item["mobile_screenshot"] = str(Path(item["mobile_screenshot"]).relative_to(BASE_DIR)).replace("\\", "/")
        result["pages"] = artifacts

        llm = LLMClient()
        common_payload = {
            "queries": queries,
            "top_urls": url_counts,
            "pages": artifacts,
        }
        result["analysis_structure"] = await llm.analyze(
            f"{COMPETITOR_RUNTIME_GUARD}\n\n{COMPETITOR_STAGE_1_GUARD}\n\n{top10_prompt_1}",
            common_payload,
        )
        result["structure_proposal"] = await llm.analyze(
            f"{COMPETITOR_RUNTIME_GUARD}\n\n{COMPETITOR_STAGE_3_GUARD}\n\n{top10_prompt_2}",
            {
                **common_payload,
                "analysis_structure": result["analysis_structure"],
            },
        )
        result["table_blocks_output"] = await llm.analyze(
            f"{COMPETITOR_RUNTIME_GUARD}\n\n{COMPETITOR_STAGE_1_GUARD}\n\n{top10_table_blocks_prompt}",
            {
                **common_payload,
                "analysis_structure": result["analysis_structure"],
            },
        )
        result["table_structure_output"] = await llm.analyze(
            f"{COMPETITOR_RUNTIME_GUARD}\n\n{COMPETITOR_STAGE_3_GUARD}\n\n{top10_table_structure_prompt}",
            {
                **common_payload,
                "structure_proposal": result["structure_proposal"],
            },
        )

        (DATA_DIR / f"top10_report_{run_id}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))

    return JSONResponse(content=result)


def _build_report_html(result: dict) -> str:
    def _md_inline(line: str) -> str:
        text = escape(line)
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', text)
        text = re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', text)
        return text

    def _markdown_to_html(md: str) -> str:
        lines = md.replace("\r", "").split("\n")
        out: list[str] = []
        in_ul = False
        in_ol = False

        def close_lists() -> None:
            nonlocal in_ul, in_ol
            if in_ul:
                out.append("</ul>")
                in_ul = False
            if in_ol:
                out.append("</ol>")
                in_ol = False

        for raw in lines:
            line = raw.strip()
            if not line:
                close_lists()
                continue

            h2 = re.match(r"^##\s+(.+)$", line)
            if h2:
                close_lists()
                out.append(f"<h2>{_md_inline(h2.group(1))}</h2>")
                continue

            h3 = re.match(r"^###\s+(.+)$", line)
            if h3:
                close_lists()
                out.append(f"<h3>{_md_inline(h3.group(1))}</h3>")
                continue

            ul = re.match(r"^[-•]\s+(.+)$", line)
            if ul:
                if in_ol:
                    out.append("</ol>")
                    in_ol = False
                if not in_ul:
                    out.append("<ul>")
                    in_ul = True
                out.append(f"<li>{_md_inline(ul.group(1))}</li>")
                continue

            ol = re.match(r"^\d+\.\s+(.+)$", line)
            if ol:
                if in_ul:
                    out.append("</ul>")
                    in_ul = False
                if not in_ol:
                    out.append("<ol>")
                    in_ol = True
                out.append(f"<li>{_md_inline(ol.group(1))}</li>")
                continue

            close_lists()
            out.append(f"<p>{_md_inline(line)}</p>")

        close_lists()
        return "".join(out)

    report_md = str(result.get("report_md", "")).strip()
    if not report_md:
        top_pages = result.get("top_pages") or []
        pages_lines: list[str] = []
        for idx, page in enumerate(top_pages, start=1):
            url = str(page.get("url", "-"))
            visits = str(page.get("visits", "-"))
            title = str(page.get("title", "-"))
            pages_lines.append(f"{idx}. [{url}]({url}) — визиты: {visits}; title: {title}")

        report_md = (
            f"## 0) Паспорт отчёта\n\n"
            f"- Run ID: {result.get('run_id', '-')}\n"
            f"- Режим: {result.get('audit_mode', '-')}\n\n"
            f"## 1) Саммари\n\n{result.get('final_summary', '')}\n\n"
            f"## 2) Данные: диагностика спроса и входов\n\n{result.get('metrics_analysis', '')}\n\n"
            f"## 3) Экспертный UX-аудит по скриншотам\n\n{result.get('pages_analysis', '')}\n\n"
            f"## 4) Карта соответствия «интенты → посадочные → UX-узкие места»\n\n{result.get('audience_analysis', '')}\n\n"
            f"## 5) План действий и контроль результата\n\n"
            f"1. Сформировать backlog правок по приоритету.\n"
            f"2. Зафиксировать KPI и контрольные точки.\n"
            f"3. Провести повторный замер после внедрения.\n\n"
            f"## 6) Приложения\n\n"
            f"### Список проанализированных страниц\n"
            f"{chr(10).join(pages_lines) if pages_lines else 'Нет страниц.'}\n"
        )

    document_html = _markdown_to_html(report_md)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <style>
    body {{
      font-family: Inter, system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif;
      margin: 0;
      color: #111827;
      background: #fff;
      font-size: 15px;
      line-height: 1.6;
    }}
    .doc {{
      max-width: 860px;
      margin: 0 auto;
      padding: 28px;
    }}
    h1 {{ margin: 0 0 10px; font-size: 32px; line-height: 1.2; }}
    h2 {{ margin: 28px 0 10px; font-size: 22px; line-height: 1.3; }}
    h3 {{ margin: 20px 0 8px; font-size: 17px; line-height: 1.35; }}
    p {{ margin: 10px 0; }}
    ul, ol {{ margin: 8px 0 14px; padding-left: 22px; }}
    li {{ margin: 6px 0; }}
    a {{ color: #1f5f53; word-break: break-word; }}
    code {{ background: #f3f4f6; border-radius: 4px; padding: 0 4px; }}
  </style>
</head>
<body>
  <main class="doc">
    <h1>Отчёт аудита сайта</h1>
    {document_html}
  </main>
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


@app.post("/export/google-sheets")
async def export_google_sheets(payload: dict = Body(...)) -> JSONResponse:
    report_type = str(payload.get("report_type", "site"))
    report_payload = payload.get("payload")
    if not isinstance(report_payload, dict):
        return JSONResponse(content={"errors": ["Неверный формат payload для экспорта."]}, status_code=400)

    webhook_url = ""
    if report_type == "site":
        webhook_url = settings.google_sheets_webhook_url_site.strip()
    elif report_type == "competitors":
        webhook_url = settings.google_sheets_webhook_url_competitors.strip()
    elif report_type == "top10":
        webhook_url = settings.google_sheets_webhook_url_structure.strip()

    # Legacy fallback: if specific URL is not set, use generic webhook URL.
    if not webhook_url:
        webhook_url = settings.google_sheets_webhook_url.strip()

    if not webhook_url:
        return JSONResponse(
            content={"errors": [f"Для инструмента report_type={report_type} не настроен webhook Google Sheets."]},
            status_code=400,
        )
    if report_type == "top10":
        # Для таблицы используем отдельные, строго нормализованные представления, если они есть.
        table_blocks = str(report_payload.get("table_blocks_output", "") or "").strip()
        table_structure = str(report_payload.get("table_structure_output", "") or "").strip()
        if table_blocks:
            report_payload["analysis_structure"] = table_blocks
        if table_structure:
            report_payload["structure_proposal"] = table_structure
    elif report_type == "competitors":
        # Экспорт конкурентов совместим с таблицей top10: лист 1 (блоки), лист 2 (структура).
        table_blocks = str(report_payload.get("table_blocks_output", "") or report_payload.get("normalized_blocks", "")).strip()
        if table_blocks:
            report_payload["analysis_structure"] = table_blocks
        elif report_payload.get("analysis_sites"):
            report_payload["analysis_structure"] = str(report_payload.get("analysis_sites"))
        if report_payload.get("structure_proposal"):
            report_payload["structure_proposal"] = str(report_payload.get("structure_proposal"))
        report_type = "top10"
    try:
        from app.apps_script_sheets import AppsScriptSheetsExporter

        exporter = AppsScriptSheetsExporter(webhook_url=webhook_url)
        result = exporter.export(report_type, report_payload)
        return JSONResponse(content=result)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(content={"errors": [str(exc)]}, status_code=400)

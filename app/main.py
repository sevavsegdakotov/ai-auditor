from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from html import escape
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

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
from app.competitor_prompts import DEFAULT_COMPETITOR_PROMPTS, DEFAULT_COMPETITOR_PROMPT_ENABLED
from app.top10_prompts import DEFAULT_TOP10_PROMPTS, DEFAULT_TOP10_PROMPTS_LIGHT, DEFAULT_TOP10_PROMPT_ENABLED
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
logger = logging.getLogger(__name__)


def _build_info_payload() -> dict[str, str]:
    return {
        "build_sha": str(settings.app_build_sha or "").strip() or "unknown",
        "build_time": str(settings.app_build_time or "").strip() or "unknown",
        "top10_structure_parser": str(settings.top10_structure_parser_version or "v2_strict"),
    }

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
            "default_competitor_prompts": DEFAULT_COMPETITOR_PROMPTS,
            "default_competitor_prompt_enabled": DEFAULT_COMPETITOR_PROMPT_ENABLED,
        },
    )


@app.get("/top10-structure", response_class=HTMLResponse)
async def top10_structure(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "top10_structure.html",
        {
            "asset_version": datetime.now().strftime("%Y%m%d%H%M%S"),
            "default_top10_prompts": DEFAULT_TOP10_PROMPTS,
            "default_top10_prompt_enabled": DEFAULT_TOP10_PROMPT_ENABLED,
            "default_region_id": "225",
            "default_region_name": "Россия",
            "top10_variant": "normal",
        },
    )


@app.get("/top10-structure-light", response_class=HTMLResponse)
async def top10_structure_light(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "top10_structure.html",
        {
            "asset_version": datetime.now().strftime("%Y%m%d%H%M%S"),
            "default_top10_prompts": DEFAULT_TOP10_PROMPTS_LIGHT,
            "default_top10_prompt_enabled": DEFAULT_TOP10_PROMPT_ENABLED,
            "default_region_id": "225",
            "default_region_name": "Россия",
            "top10_variant": "light",
        },
    )


@app.get("/docs/user", response_class=HTMLResponse)
async def docs_user(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "docs_user.html",
        {
            "asset_version": datetime.now().strftime("%Y%m%d%H%M%S"),
        },
    )


@app.get("/docs/tech", response_class=HTMLResponse)
async def docs_tech(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "docs_tech.html",
        {
            "asset_version": datetime.now().strftime("%Y%m%d%H%M%S"),
        },
    )


@app.get("/build-info")
async def build_info() -> JSONResponse:
    return JSONResponse(content=_build_info_payload())


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


def _canonical_site(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    candidate = raw
    if candidate.startswith(("http://", "https://")) or "/" in candidate:
        try:
            parsed = urlparse(candidate)
            if parsed.netloc:
                candidate = parsed.netloc
        except Exception:  # noqa: BLE001
            pass
    candidate = candidate.lower().strip().strip("/")
    if candidate.startswith("www."):
        candidate = candidate[4:]
    return candidate


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


def _extract_json_array_block(text: str, marker: str) -> list[dict]:
    if not text:
        return []
    marker_pos = text.find(marker)
    if marker_pos < 0:
        return []
    start = text.find("[", marker_pos)
    if start < 0:
        return []
    depth = 0
    in_string = False
    escaped = False
    end = -1
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = idx + 1
                break
    if end < 0:
        return []
    raw = text[start:end]
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    except Exception:  # noqa: BLE001
        return []
    return []


def _extract_first_json_array_after(text: str, start_pos: int) -> list[dict]:
    if not text or start_pos < 0:
        return []
    start = text.find("[", start_pos)
    if start < 0:
        return []
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                raw = text[start : idx + 1]
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return [item for item in parsed if isinstance(item, dict)]
                except Exception:  # noqa: BLE001
                    return []
                return []
    return []


def _find_fenced_json_arrays(text: str) -> list[list[dict]]:
    if not text:
        return []
    matches = re.findall(r"```json\s*(\[[\s\S]*?\])\s*```", text, flags=re.IGNORECASE)
    arrays: list[list[dict]] = []
    for raw in matches:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                rows = [item for item in parsed if isinstance(item, dict)]
                if rows:
                    arrays.append(rows)
        except Exception:  # noqa: BLE001
            continue
    return arrays


def _looks_like_structures_rows(rows: list[dict]) -> bool:
    if not rows:
        return False
    required_hits = 0
    probe = rows[: min(8, len(rows))]
    for row in probe:
        if not isinstance(row, dict):
            continue
        has_site_or_url = bool(row.get("site") or row.get("page_url"))
        has_block = bool(row.get("l2_id") or row.get("block_name") or row.get("block_id"))
        has_index = row.get("block_index") is not None
        if has_site_or_url and has_block and has_index:
            required_hits += 1
    return required_hits >= max(1, len(probe) // 2)


def _extract_structures_rows_from_plain_text(text: str) -> list[dict]:
    """
    Fallback parser for LLM outputs in plain text format:
    <site-url>
    1. <RU name> (<system_id>)
    2. ...
    """
    if not text:
        return []
    lines = [str(line or "").strip() for line in text.replace("\r", "").split("\n")]
    rows: list[dict] = []
    current_site = ""
    current_page_url = ""
    auto_index = 0

    site_re = re.compile(r"^https?://[^\s]+$", flags=re.IGNORECASE)
    item_re = re.compile(r"^\s*(\d+)[\.\)]\s*(.+?)\s*$")

    def split_label_id(raw: str) -> tuple[str, str]:
        value = _normalize_ws(raw)
        m = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", value)
        if m:
            left = _normalize_ws(m.group(1))
            right = _normalize_ws(m.group(2))
            if right:
                return left or right, right
        return value, ""

    def _clean_line(value: str) -> str:
        cleaned = str(value or "").strip()
        cleaned = cleaned.strip("`")
        cleaned = cleaned.strip()
        # Убираем обрамляющие кавычки и запятые из текстового fallback.
        cleaned = re.sub(r'^[\"“”«»\']+', "", cleaned)
        cleaned = re.sub(r'[\"“”«»\']+$', "", cleaned)
        cleaned = cleaned.rstrip(",")
        return cleaned.strip()

    for raw_line in lines:
        line = _clean_line(raw_line)
        if not line:
            continue
        if site_re.match(line):
            current_site = _canonical_site(line)
            current_page_url = line.rstrip("/")
            auto_index = 0
            continue
        if not current_site:
            continue
        m = item_re.match(line)
        if not m:
            continue
        auto_index += 1
        try:
            block_index = int(m.group(1))
        except Exception:  # noqa: BLE001
            block_index = auto_index
        raw_block = _clean_line(_normalize_ws(m.group(2)))
        label_ru, system_id = split_label_id(raw_block)
        block_name = label_ru or system_id or raw_block
        l2_id = system_id or block_name
        l2_label_ru = label_ru or block_name
        if not l2_id or not current_page_url:
            continue
        rows.append(
            {
                "site": current_site,
                "page_url": current_page_url,
                "page_type": "",
                "l1_id": "",
                "l1_label_ru": "",
                "l2_id": l2_id,
                "l2_label_ru": l2_label_ru,
                "l3_id": "",
                "l3_label_ru": "",
                "block_name": block_name,
                "block_index": block_index,
                "notes": "",
                "confidence": 0.55,
            }
        )
    return rows


def _extract_marked_sheet_text(raw: str, marker: str) -> str:
    text = (raw or "").replace("\r", "")
    lines = text.split("\n")
    header = marker.strip().upper()
    current: list[str] = []
    active = False
    for line in lines:
        normalized = line.strip().upper()
        if normalized == header:
            active = True
            current = []
            continue
        if normalized.startswith("### SHEET_") and active:
            break
        if active:
            current.append(line)
    return "\n".join(current).strip()


def _build_blocks_comparison_from_rows(rows: list[dict]) -> str:
    if not rows:
        return ""
    page_keys: dict[tuple[str, str], str] = {}
    block_sites: dict[str, set[str]] = {}
    block_display: dict[str, str] = {}
    for row in rows:
        site = str(row.get("site") or "").strip()
        page_url = str(row.get("page_url") or "").strip()
        l2_id = str(row.get("l2_id") or "").strip()
        l2_label_ru = str(row.get("l2_label_ru") or "").strip()
        block_name = str(row.get("block_name") or "").strip()
        key_name = l2_id or block_name
        if not site or not page_url or not key_name:
            continue
        key = (site, page_url)
        page_keys[key] = site
        block_sites.setdefault(key_name, set()).add(site)
        if key_name not in block_display:
            block_display[key_name] = f"{l2_label_ru} ({l2_id})" if l2_label_ru and l2_id else (l2_label_ru or key_name)

    total_pages = len(page_keys)
    if total_pages == 0:
        return ""

    items = sorted(block_sites.items(), key=lambda item: (-len(item[1]), item[0].lower()))
    lines: list[str] = []
    for idx, (block, sites) in enumerate(items, start=1):
        site_list = ", ".join(sorted(sites))
        label = block_display.get(block, block)
        lines.append(f"{idx}. {label} — сайты: {site_list} — встречаемость: {len(sites)}/{total_pages}")
    return "\n".join(lines)


def _extract_competitor_structures_rows(normalized_blocks_text: str) -> tuple[list[dict], str]:
    rows: list[dict] = []
    source = "no_structures_rows_found"

    rows = _extract_json_array_block(normalized_blocks_text, "structures_rows")
    if rows:
        source = "marker_structures_rows"
    if not rows:
        rows = _extract_json_array_block(normalized_blocks_text, "Sheet1_structures")
        if rows:
            source = "marker_sheet1_structures"
    if not rows:
        marker_positions = [
            normalized_blocks_text.find("ЧАСТЬ B"),
            normalized_blocks_text.find("СЛУЖЕБНЫЕ ДАННЫЕ"),
            normalized_blocks_text.find("SERVICE DATA"),
        ]
        marker_positions = [pos for pos in marker_positions if pos >= 0]
        for pos in marker_positions:
            candidate = _extract_first_json_array_after(normalized_blocks_text, pos)
            if candidate and _looks_like_structures_rows(candidate):
                rows = candidate
                source = "service_data_json_block"
                break
    if not rows:
        for candidate in _find_fenced_json_arrays(normalized_blocks_text):
            if _looks_like_structures_rows(candidate):
                rows = candidate
                source = "fenced_json_block"
                break
    if not rows:
        candidate = _extract_first_json_array_after(normalized_blocks_text, 0)
        if candidate and _looks_like_structures_rows(candidate):
            rows = candidate
            source = "raw_json_array_scan"
    if not rows:
        candidate_rows = _extract_structures_rows_from_plain_text(normalized_blocks_text)
        if candidate_rows:
            return candidate_rows, "plain_text_scan"

    prepared: list[dict] = []
    for row in rows:
        site = _canonical_site(row.get("site") or "")
        page_url = str(row.get("page_url") or "").strip()
        l1_id = str(row.get("l1_id") or row.get("l1") or "").strip()
        l1_label_ru = str(row.get("l1_label_ru") or "").strip()
        l2_id = str(row.get("l2_id") or row.get("block_id") or "").strip()
        l2_label_ru = str(row.get("l2_label_ru") or "").strip()
        l3_id = str(row.get("l3_id") or row.get("block_variant") or "").strip()
        l3_label_ru = str(row.get("l3_label_ru") or "").strip()
        block_name = str(
            row.get("block_name")
            or row.get("canonical_block_name")
            or l2_label_ru
            or l2_id
            or ""
        ).strip()
        if not l2_id:
            l2_id = block_name
        if not l2_label_ru:
            l2_label_ru = block_name
        if not site and page_url:
            site = _canonical_site(page_url)
        if not site or not page_url or not (l2_id or block_name):
            continue
        prepared.append(
            {
                "site": site,
                "page_url": page_url,
                "page_type": str(row.get("page_type") or "").strip(),
                "l1_id": l1_id,
                "l1_label_ru": l1_label_ru,
                "l2_id": l2_id,
                "l2_label_ru": l2_label_ru,
                "l3_id": l3_id,
                "l3_label_ru": l3_label_ru,
                "block_name": block_name,
                "block_index": int(row.get("block_index") or 0),
                "notes": str(row.get("notes") or "").strip(),
                "confidence": float(row.get("confidence") or 0),
            }
        )
    if prepared and source == "no_structures_rows_found":
        source = "raw_json_array_scan"
    return prepared, source


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("\u00a0", " ")).strip()


def _same_label_id(label: str, block_id: str) -> bool:
    def _canon(value: str) -> str:
        v = _normalize_ws(value).lower().replace("_", " ").replace("-", " ")
        v = re.sub(r"\s+", " ", v).strip()
        return v

    left = _canon(label)
    right = _canon(block_id)
    return bool(left and right and left == right)


def _format_block_display(label_ru: str, block_id: str, fallback: str) -> str:
    label = _normalize_ws(label_ru)
    identifier = _normalize_ws(block_id)
    fb = _normalize_ws(fallback)

    if settings.strict_block_display_format:
        if label and identifier:
            if _same_label_id(label, identifier):
                return label
            return f"{label} ({identifier})"
        if label:
            return label
        if identifier:
            return identifier
        return fb

    # Rollback mode: previous behavior.
    if label and identifier:
        return f"{label} ({identifier})"
    return label or identifier or fb


def _validate_sheet1_matrix_rows(rows: object) -> tuple[bool, str]:
    if not isinstance(rows, list) or len(rows) < 2:
        return False, "Лист сравнения: ожидается минимум 2 строки."
    header = rows[0]
    if not isinstance(header, list) or len(header) < 2:
        return False, "Лист сравнения: некорректный header."
    if str(header[0]).strip() != "Блоки / Сайты":
        return False, "Лист сравнения: первая ячейка должна быть «Блоки / Сайты»."
    width = len(header)
    for i, row in enumerate(rows[1:], start=2):
        if not isinstance(row, list) or len(row) != width:
            return False, f"Лист сравнения: строка {i} имеет неверную ширину."
        for val in row[1:]:
            cell = str(val or "").strip()
            if cell not in {"", "✓"}:
                return False, f"Лист сравнения: недопустимое значение «{cell}» в строке {i}."
    return True, ""


def _validate_sheet2_site_columns_rows(rows: object) -> tuple[bool, str]:
    if not isinstance(rows, list) or len(rows) < 2:
        return False, "Лист структуры по сайтам: ожидается минимум 2 строки."
    header = rows[0]
    if not isinstance(header, list) or len(header) < 2:
        return False, "Лист структуры по сайтам: в header должно быть минимум 2 сайта."
    width = len(header)
    if any(not str(site or "").strip() for site in header):
        return False, "Лист структуры по сайтам: пустые названия сайтов в header."
    for i, row in enumerate(rows[1:], start=2):
        if not isinstance(row, list) or len(row) != width:
            return False, f"Лист структуры по сайтам: строка {i} имеет неверную ширину."
    return True, ""


def _validate_sheet3_proposed_rows(rows: object) -> tuple[bool, str]:
    if not isinstance(rows, list) or len(rows) < 2:
        return False, "Лист предложенной структуры: ожидается минимум 2 строки."
    header = rows[0]
    if not isinstance(header, list) or len(header) < 2:
        return False, "Лист предложенной структуры: некорректный header."
    h0 = str(header[0]).strip()
    h1 = str(header[1]).strip()
    if h0 == "Блок" and h1 == "Комментарии по блоку":
        min_width = 2
    elif (
        h0 == "Блок (человекочитаемый)"
        and h1 == "Блок (системный)"
        and len(header) >= 3
        and str(header[2]).strip() == "Комментарии по блоку"
    ):
        min_width = 3
    else:
        return False, (
            "Лист предложенной структуры: header должен быть "
            "«Блок | Комментарии по блоку» (старый) или "
            "«Блок (человекочитаемый) | Блок (системный) | Комментарии по блоку» (новый)."
        )
    for i, row in enumerate(rows[1:], start=2):
        if not isinstance(row, list) or len(row) < min_width:
            return False, f"Лист предложенной структуры: строка {i} имеет неверный формат."
    return True, ""


def _validate_top10_export_bundle(bundle: object) -> tuple[bool, str]:
    if not isinstance(bundle, dict):
        return False, "export_bundle отсутствует или имеет неверный формат."
    matrix_rows = bundle.get("sheet1_matrix_rows")
    site_rows = bundle.get("sheet2_site_columns_rows")
    proposed_rows = bundle.get("sheet3_proposed_rows")
    matrix_ok, matrix_reason = _validate_sheet1_matrix_rows(matrix_rows)
    sites_ok, sites_reason = _validate_sheet2_site_columns_rows(site_rows)
    structure_ok, structure_reason = _validate_sheet3_proposed_rows(proposed_rows)
    if not matrix_ok:
        return False, matrix_reason
    if not sites_ok:
        return False, sites_reason
    if not structure_ok:
        return False, structure_reason
    return True, ""


def _payload_pages_as_artifacts(report_payload: dict) -> list[dict]:
    artifacts: list[dict] = []
    pages = report_payload.get("pages")
    if isinstance(pages, list):
        for item in pages:
            if isinstance(item, dict):
                url = str(item.get("url") or item.get("page_url") or "").strip()
                if url:
                    artifacts.append({"url": url})
    if artifacts:
        return artifacts
    urls = report_payload.get("urls")
    if isinstance(urls, list):
        for item in urls:
            if isinstance(item, dict):
                url = str(item.get("url") or "").strip()
            else:
                url = str(item or "").strip()
            if url:
                artifacts.append({"url": url})
    return artifacts


def _build_top10_proposed_structure_rows(
    table_structure_output: str,
    structure_proposal_fallback: str,
) -> tuple[list[list[str]], bool, str, dict, dict]:
    header = ["Блок (человекочитаемый)", "Блок (системный)", "Комментарии по блоку"]
    source_text = str(table_structure_output or "").strip() or str(structure_proposal_fallback or "").strip()
    fallback_rows = [header, ["Не удалось выделить структуру автоматически", "", "Недостаточно структурированных строк в ответе модели."]]
    parse_stats = {
        "rows_total": 0,
        "rows_with_system_id": 0,
        "dropped_as_reason_lines": 0,
        "parse_mode": "empty",
    }
    parse_examples = {"accepted": [], "dropped": []}
    if not source_text:
        return fallback_rows, False, "Пустой текст предложенной структуры.", parse_stats, parse_examples

    lines = [_normalize_ws(line) for line in source_text.replace("\r", "").split("\n")]
    id_pattern = re.compile(r"^[a-z0-9_/\-]{3,}$", flags=re.IGNORECASE)
    banned_start_pattern = re.compile(
        r"^(почему|зачем|обоснование|цель|что внутри|cta|proof_type|снимает|конверсионный|закрывает вопрос)\b",
        flags=re.IGNORECASE,
    )

    def _strip_markup(value: str) -> str:
        text = _normalize_ws(value)
        text = text.strip("*`_")
        text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
        text = re.sub(r"^#+\s*", "", text)
        return _normalize_ws(text)

    def _extract_human_system(raw_value: str) -> tuple[str, str] | None:
        text = _strip_markup(raw_value)
        if not text:
            return None
        text = re.sub(r"^\d+[.)]\s*", "", text).strip()
        ru_then_id = re.match(r"^(.+?)\s*\(([a-z0-9_/\-]{3,})\)\s*$", text, flags=re.IGNORECASE)
        if ru_then_id:
            human = _normalize_ws(ru_then_id.group(1))
            system = _normalize_ws(ru_then_id.group(2))
            if human and system:
                return human, system
        id_then_ru = re.match(r"^([a-z0-9_/\-]{3,})\s*\((.+?)\)\s*$", text, flags=re.IGNORECASE)
        if id_then_ru:
            system = _normalize_ws(id_then_ru.group(1))
            human = _normalize_ws(id_then_ru.group(2))
            if human and system:
                return human, system
        return None

    def _is_reason_line(value: str) -> bool:
        text = _strip_markup(value).lower()
        return bool(banned_start_pattern.match(text))

    def _collect_comment(start_idx: int, next_block_idx: int | None) -> str:
        end = next_block_idx if next_block_idx is not None else len(lines)
        preferred = ""
        fallback = ""
        for idx in range(start_idx + 1, min(end, start_idx + 8)):
            candidate = _strip_markup(lines[idx]).lstrip("—-: ").strip()
            if not candidate:
                continue
            if re.match(r"^\d+[.)]\s+", candidate):
                continue
            if candidate.lower().startswith(("по каждому шагу", "контроль cta", "чек-лист рисков")):
                break
            if _is_reason_line(candidate):
                cleaned = re.sub(
                    r"^(почему|зачем|обоснование|цель)\s*[:\-—]?\s*",
                    "",
                    candidate,
                    flags=re.IGNORECASE,
                ).strip()
                if cleaned and cleaned.lower() not in {"(1 строка)", "1 строка", "кратко"}:
                    preferred = cleaned
                    break
                continue
            if not fallback and not re.search(r"\([a-z0-9_/\-]{3,}\)\s*$", candidate, flags=re.IGNORECASE):
                fallback = candidate
        comment = preferred or fallback
        comment = _normalize_ws(comment)
        if len(comment) > 300:
            comment = comment[:297].rstrip() + "..."
        return comment

    def _phase_candidates(phase: str) -> list[tuple[int, str, str, str]]:
        candidates: list[tuple[int, str, str, str]] = []
        for idx, raw in enumerate(lines):
            line = _strip_markup(raw)
            if not line:
                continue
            lower = line.lower()
            if lower.startswith(("https://", "http://")):
                continue
            if line.startswith("```"):
                continue
            if _is_reason_line(line):
                parse_stats["dropped_as_reason_lines"] += 1
                if len(parse_examples["dropped"]) < 3:
                    parse_examples["dropped"].append(line)
                continue
            numbered = re.match(r"^\d+[.)]\s+(.+)$", line)
            target = numbered.group(1) if numbered else line
            inline_comment = ""
            split_match = re.match(r"^(.+?)\s*[—–-]\s+(.+)$", target)
            if split_match:
                left = _strip_markup(split_match.group(1))
                right = _strip_markup(split_match.group(2))
                if left and right:
                    target = left
                    inline_comment = right
            extracted = _extract_human_system(target)
            if not extracted:
                if phase == "phase_b":
                    generic = re.search(r"\(([^\)]+)\)\s*$", target)
                    if generic:
                        candidate_id = _normalize_ws(generic.group(1)).lower()
                        if id_pattern.fullmatch(candidate_id):
                            human = _normalize_ws(target[: generic.start()].strip())
                            if human:
                                extracted = (human, candidate_id)
                if not extracted:
                    continue
            human, system = extracted
            if not id_pattern.fullmatch(system):
                continue
            if phase == "phase_a" and not numbered:
                continue
            comment = inline_comment or _collect_comment(idx, None)
            candidates.append((idx, human, system, comment))
        return candidates

    chosen = _phase_candidates("phase_a")
    parse_mode = "phase_a"
    if len(chosen) < 6:
        chosen = _phase_candidates("phase_b")
        parse_mode = "phase_b"

    # Recompute comments with next block boundary.
    chosen.sort(key=lambda item: item[0])
    rebuilt: list[list[str]] = [header]
    for i, (idx, human, system, inline_comment) in enumerate(chosen):
        next_idx = chosen[i + 1][0] if i + 1 < len(chosen) else None
        comment = inline_comment or _collect_comment(idx, next_idx)
        rebuilt.append([human, system, comment])
        if len(parse_examples["accepted"]) < 3:
            parse_examples["accepted"].append(f"{human} ({system})")

    rows_total = len(rebuilt) - 1
    rows_with_system_id = sum(1 for row in rebuilt[1:] if _normalize_ws(row[1]))
    parse_stats.update(
        {
            "rows_total": rows_total,
            "rows_with_system_id": rows_with_system_id,
            "parse_mode": parse_mode,
        }
    )

    banned_single_pattern = re.compile(
        r"^(снимает тревогу|конверсионный инструмент|закрывает вопрос)\b",
        flags=re.IGNORECASE,
    )
    bad_single_rows = [
        row for row in rebuilt[1:]
        if banned_single_pattern.match(_normalize_ws(row[0])) and not _normalize_ws(row[1])
    ]
    share_with_id = (rows_with_system_id / rows_total) if rows_total else 0.0
    if rows_total < 6 or share_with_id < 0.7 or bad_single_rows:
        reason = "Не удалось извлечь валидные блоки предложенной структуры (quality gate failed)."
        return fallback_rows, False, reason, parse_stats, parse_examples

    return rebuilt, True, "", parse_stats, parse_examples


def _build_competitors_compare_and_site_text(
    rows: list[dict], pages: list[dict]
) -> tuple[str, str, list[list[str]], list[list[str]]]:
    """Build deterministic 2-sheet payloads.

    Returns:
    - sheet1_text: per-site sections (for matrix parser fallback).
    - sheet2_text: per-site sections with comments.
    - sheet1_matrix_rows: ready matrix rows (row=block, col=site, value=checkmark).
    - sheet2_site_columns_rows: ready rows (header=sites, rows=site structures).
    """
    if not rows:
        return "", "", [], []

    site_order: list[str] = []
    site_to_rows: dict[str, list[dict]] = {}
    for page in pages:
        url = str(page.get("url") or "").strip()
        if not url:
            continue
        site = _canonical_site(url)
        if site and site not in site_order:
            site_order.append(site)
    for row in rows:
        site = _canonical_site(row.get("site") or "")
        if not site:
            site = _canonical_site(row.get("page_url") or "")
        if not site:
            continue
        site_to_rows.setdefault(site, []).append(row)
        if site not in site_order:
            site_order.append(site)

    block_sites: dict[str, set[str]] = {}
    block_display_by_id: dict[str, str] = {}
    site_blocks_ordered: dict[str, list[tuple[str, str, str]]] = {}
    for row in rows:
        l2_id = str(row.get("l2_id") or "").strip()
        l2_label_ru = str(row.get("l2_label_ru") or "").strip()
        block_name = str(row.get("block_name") or "").strip()
        block = l2_id or block_name
        site = _canonical_site(row.get("site") or "")
        if not site:
            site = _canonical_site(row.get("page_url") or "")
        notes = str(row.get("notes") or "").strip()
        if not block or not site:
            continue
        block_sites.setdefault(block, set()).add(site)
        if block not in block_display_by_id:
            block_display_by_id[block] = _format_block_display(l2_label_ru, l2_id, block)
        site_blocks_ordered.setdefault(site, []).append((block, block_display_by_id[block], notes))

    total_sites = len(site_order) if site_order else len({str(r.get("site") or "") for r in rows})
    total_sites = max(total_sites, 1)
    sorted_blocks = sorted(block_sites.items(), key=lambda item: (-len(item[1]), item[0].lower()))
    sheet1_lines: list[str] = []
    for site in site_order:
        blocks = site_blocks_ordered.get(site, [])
        if not blocks:
            continue
        seen_blocks: set[str] = set()
        sheet1_lines.append(f"https://{site}/")
        idx = 1
        for block, _display, _notes in blocks:
            if block in seen_blocks:
                continue
            seen_blocks.add(block)
            sheet1_lines.append(f"{idx}. {block_display_by_id.get(block, block)}")
            idx += 1
        sheet1_lines.append("")

    # Matrix rows for direct sheet rendering.
    matrix_rows: list[list[str]] = [["Блоки / Сайты", *site_order]]
    for idx, (block, sites) in enumerate(sorted_blocks, start=1):
        block_display = block_display_by_id.get(block, block)
        row = [block_display]
        for site in site_order:
            row.append("✓" if site in sites else "")
        matrix_rows.append(row)
        # optional compact comparative line for raw fallback
        sites_sorted = sorted(sites, key=lambda s: site_order.index(s) if s in site_order else 10_000)
        sites_str = ", ".join(sites_sorted)
        sheet1_lines.append(f"{idx}. {block_display} — сайты: {sites_str} — встречаемость: {len(sites)}/{total_sites}")
    sheet1_text = "\n".join(sheet1_lines).strip()

    # Sheet 2: per-site normalized structure with comments.
    sheet2_lines: list[str] = []
    site_columns_rows: list[list[str]] = [site_order]
    site_columns_values: dict[str, list[str]] = {}
    for site in site_order:
        rows_for_site = site_to_rows.get(site, [])
        if not rows_for_site:
            continue
        # Deduplicate by (l2_id, page_url) with fallback to block_name.
        rows_sorted = sorted(rows_for_site, key=lambda r: (str(r.get("page_url") or ""), int(r.get("block_index") or 0)))
        seen: set[tuple[str, str]] = set()
        compact: list[dict] = []
        for row in rows_sorted:
            key = (
                str(row.get("page_url") or ""),
                str(row.get("l2_id") or row.get("block_name") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            compact.append(row)
        sheet2_lines.append(site)
        col_values: list[str] = []
        for idx, row in enumerate(compact, start=1):
            block_id = str(row.get("l2_id") or "").strip()
            block_label_ru = str(row.get("l2_label_ru") or "").strip()
            block_name = str(row.get("block_name") or "").strip()
            block = _format_block_display(block_label_ru, block_id, block_name)
            notes = str(row.get("notes") or "").strip()
            if notes:
                line = f"{idx}. {block} — {notes}"
                sheet2_lines.append(line)
                col_values.append(f"{block} — {notes}")
            else:
                line = f"{idx}. {block}"
                sheet2_lines.append(line)
                col_values.append(block)
        sheet2_lines.append("")
        site_columns_values[site] = col_values

    max_len = max((len(values) for values in site_columns_values.values()), default=0)
    for i in range(max_len):
        row: list[str] = []
        for site in site_order:
            values = site_columns_values.get(site, [])
            row.append(values[i] if i < len(values) else "")
        site_columns_rows.append(row)
    sheet2_text = "\n".join(sheet2_lines).strip()

    return sheet1_text, sheet2_text, matrix_rows, site_columns_rows


def _matrix_has_non_first_site_checks(matrix_rows: list[list[str]]) -> bool:
    if not matrix_rows or len(matrix_rows) < 2:
        return False
    header = matrix_rows[0]
    if len(header) < 3:
        return False
    for row in matrix_rows[1:]:
        if len(row) < len(header):
            continue
        for cell in row[2:]:
            if str(cell or "").strip() == "✓":
                return True
    return False


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
        top_rows = await asyncio.wait_for(
            asyncio.to_thread(client.get_top_urls, queries, normalized_region, max(1, min(top_n, 10))),
            timeout=max(30, settings.top10_urls_timeout_seconds),
        )
        result["urls"] = [{"url": row.url, "count": row.count} for row in top_rows]
        if result["urls"]:
            _save_top10_cache(queries, normalized_region, result["urls"])
    except TimeoutError:
        cached = _load_top10_cache(queries, normalized_region)
        if cached:
            result["urls"] = cached[: max(1, min(top_n, 10))]
            result["errors"].append(
                "KeySo отвечает слишком долго. Использован кэш последней успешной выборки."
            )
        else:
            result["errors"].append(
                "KeySo отвечает слишком долго. Попробуйте ещё раз через 1-2 минуты."
            )
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
    prompt_master: str = Form(""),
    prompt_blocks: str = Form(""),
    prompt_messages: str = Form(""),
    prompt_summary: str = Form(""),
    prompt_export_table: str = Form(""),
    enabled_master: str | None = Form(None),
    enabled_blocks: str | None = Form(None),
    enabled_messages: str | None = Form(None),
    enabled_summary: str | None = Form(None),
    enabled_export: str | None = Form(None),
    # Backward compatibility.
    competitor_prompt_1: str = Form(""),
    competitor_prompt_2: str = Form(""),
    competitor_table_blocks_prompt: str = Form(""),
) -> JSONResponse:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_screens_dir = SCREENSHOTS_DIR / f"competitors_{run_id}"
    run_datetime = datetime.now().isoformat(timespec="seconds")
    result = {
        "run_id": run_id,
        "run_datetime": run_datetime,
        "pages": [],
        "summary_general": "",
        "normalized_blocks": "",
        "analysis_meanings": "",
        "structures_rows": [],
        "structures_rows_source": "no_structures_rows_found",
        "structures_rows_count": 0,
        "sheet1_matrix_rows": [],
        "sheet2_site_columns_rows": [],
        "export_matrix_ready": False,
        "export_matrix_reason": "Матрица ещё не сформирована.",
        "table_export_raw": "",
        "table_blocks_output": "",
        "table_structure_output": "",
        "export_stage_status": {"ok": True, "error": ""},
        "errors": [],
        "section_status": {
            "summary": {"enabled": False, "has_content": False, "reason": "disabled_by_user"},
            "blocks": {"enabled": False, "has_content": False, "reason": "disabled_by_user"},
            "meanings": {"enabled": False, "has_content": False, "reason": "disabled_by_user"},
        },
    }

    urls = _parse_urls(competitor_urls)
    if not urls:
        result["errors"].append("Добавьте хотя бы одну корректную ссылку для анализа конкурентов.")
        return JSONResponse(content=result)
    prompt_values = {
        "master": _normalize_competitor_prompt(prompt_master or DEFAULT_COMPETITOR_PROMPTS["master"]),
        "blocks": _normalize_competitor_prompt(prompt_blocks or competitor_prompt_1 or DEFAULT_COMPETITOR_PROMPTS["blocks"]),
        "messages": _normalize_competitor_prompt(prompt_messages or competitor_prompt_2 or DEFAULT_COMPETITOR_PROMPTS["messages"]),
        "summary": _normalize_competitor_prompt(prompt_summary or DEFAULT_COMPETITOR_PROMPTS["summary"]),
        "export": _normalize_competitor_prompt(prompt_export_table or competitor_table_blocks_prompt or DEFAULT_COMPETITOR_PROMPTS["export"]),
    }
    enabled = {
        "master": _is_enabled(enabled_master, DEFAULT_COMPETITOR_PROMPT_ENABLED["master"]),
        "blocks": _is_enabled(enabled_blocks, DEFAULT_COMPETITOR_PROMPT_ENABLED["blocks"]),
        "messages": _is_enabled(enabled_messages, DEFAULT_COMPETITOR_PROMPT_ENABLED["messages"]),
        "summary": _is_enabled(enabled_summary, DEFAULT_COMPETITOR_PROMPT_ENABLED["summary"]),
        "export": _is_enabled(enabled_export, DEFAULT_COMPETITOR_PROMPT_ENABLED["export"]),
    }
    result["section_status"]["blocks"]["enabled"] = enabled["blocks"]
    result["section_status"]["meanings"]["enabled"] = enabled["messages"]
    result["section_status"]["summary"]["enabled"] = enabled["summary"]
    if enabled["blocks"]:
        result["section_status"]["blocks"]["reason"] = "ok"
    if enabled["messages"]:
        result["section_status"]["meanings"]["reason"] = "ok"
    if enabled["summary"]:
        result["section_status"]["summary"]["reason"] = "ok"

    if not (enabled["blocks"] or enabled["messages"] or enabled["summary"]):
        result["errors"].append(
            "Включите хотя бы одну секцию отчёта (Общие выводы/Нормализованные блоки/Анализ смыслов)."
        )
        return JSONResponse(content=result, status_code=400)
    required_prompts = {
        "blocks": "Нормализованные блоки",
        "messages": "Анализ смыслов",
        "summary": "Общие выводы",
        "export": "Экспорт в таблицу",
    }
    missing = [title for key, title in required_prompts.items() if enabled[key] and not prompt_values[key]]
    if missing:
        result["errors"].append(f"Пустые промпты: {', '.join(missing)}.")
        return JSONResponse(content=result)

    def _map_section_reason_from_error(exc: Exception) -> str:
        text = str(exc).lower()
        if "insufficient_quota" in text or ("quota" in text and "429" in text):
            return "openai_quota_exceeded"
        if "429" in text:
            return "openai_rate_limited"
        return "llm_error"

    def _reason_text(reason: str) -> str:
        mapping = {
            "ok": "ok",
            "disabled_by_user": "секция отключена в расширенных настройках",
            "llm_error": "ошибка генерации",
            "empty_model_output": "модель вернула пустой ответ",
            "openai_quota_exceeded": "превышена квота API OpenAI",
            "openai_rate_limited": "превышен лимит запросов API OpenAI",
        }
        return mapping.get(reason, reason or "неизвестная причина")

    def _section_placeholder(title: str, reason: str) -> str:
        return f"## {title}\n\nРаздел не сформирован: {_reason_text(reason)}."

    def _finalize_section(status_key: str, title: str, content_key: str) -> None:
        section_state = result["section_status"][status_key]
        content = str(result.get(content_key, "") or "").strip()
        if content:
            section_state["has_content"] = True
            section_state["reason"] = "ok"
            result[content_key] = content
            return
        if not section_state["enabled"]:
            section_state["reason"] = "disabled_by_user"
        elif section_state["reason"] == "ok":
            section_state["reason"] = "empty_model_output"
        section_state["has_content"] = False
        result[content_key] = _section_placeholder(title, section_state["reason"])

    try:
        top_pages = [TopPage(url=url, visits=0) for url in urls]
        artifacts = await collect_page_artifacts(top_pages, run_screens_dir)
        for item in artifacts:
            item["desktop_screenshot"] = str(Path(item["desktop_screenshot"]).relative_to(BASE_DIR)).replace("\\", "/")
            item["mobile_screenshot"] = str(Path(item["mobile_screenshot"]).relative_to(BASE_DIR)).replace("\\", "/")
        result["pages"] = artifacts

        llm = LLMClient()
        common_payload = {
            "run_id": run_id,
            "run_datetime": run_datetime,
            "pages": artifacts,
            "input_urls": urls,
        }
        if enabled["blocks"]:
            blocks_prompt_parts = []
            if enabled["master"] and prompt_values["master"]:
                blocks_prompt_parts.append(
                    "Контекст оркестрации: это подзадача нормализованных блоков. "
                    "Выполняй только подзадачу блоков и не переходи к смысловому анализу."
                )
            blocks_prompt_parts.extend([COMPETITOR_RUNTIME_GUARD, COMPETITOR_STAGE_1_GUARD, prompt_values["blocks"]])
            blocks_prompt = "\n\n".join(part for part in blocks_prompt_parts if part)
            try:
                result["normalized_blocks"] = await llm.analyze(blocks_prompt, common_payload)
                extracted_rows, rows_source = _extract_competitor_structures_rows(result["normalized_blocks"])
                result["structures_rows"] = extracted_rows
                result["structures_rows_source"] = rows_source
                result["structures_rows_count"] = len(extracted_rows)
                if not result["structures_rows"]:
                    result["export_matrix_ready"] = False
                    result["export_matrix_reason"] = (
                        "Не удалось выделить structures_rows из ответа блока «Нормализованные блоки». "
                        "Будет использован текстовый fallback."
                    )
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(f"Нормализованные блоки: {exc}")
                result["section_status"]["blocks"]["reason"] = _map_section_reason_from_error(exc)

        if enabled["messages"]:
            messages_prompt_parts = []
            if enabled["master"] and prompt_values["master"]:
                messages_prompt_parts.append(
                    "Контекст оркестрации: это подзадача анализа смыслов. "
                    "Запрещено описывать структуру блоков, последовательность секций и итоговую архитектуру."
                )
            messages_prompt_parts.extend([COMPETITOR_RUNTIME_GUARD, COMPETITOR_STAGE_2_GUARD, prompt_values["messages"]])
            messages_prompt = "\n\n".join(part for part in messages_prompt_parts if part)
            try:
                result["analysis_meanings"] = await llm.analyze(messages_prompt, common_payload)
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(f"Анализ смыслов: {exc}")
                result["section_status"]["meanings"]["reason"] = _map_section_reason_from_error(exc)

        if enabled["summary"]:
            summary_prompt_parts = [COMPETITOR_RUNTIME_GUARD, prompt_values["summary"]]
            if enabled["master"] and prompt_values["master"]:
                summary_prompt_parts.insert(
                    0,
                    "Контекст оркестрации: это подзадача общих выводов. "
                    "Используй результаты блоков и смыслов, не дублируй их дословно.",
                )
            summary_prompt = "\n\n".join(part for part in summary_prompt_parts if part)
            try:
                result["summary_general"] = await llm.analyze(
                    summary_prompt,
                    {
                        **common_payload,
                        "normalized_blocks": result["normalized_blocks"],
                        "analysis_meanings": result["analysis_meanings"],
                    },
                )
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(f"Общие выводы: {exc}")
                result["section_status"]["summary"]["reason"] = _map_section_reason_from_error(exc)

        _finalize_section("summary", "Общие выводы", "summary_general")
        _finalize_section("blocks", "Нормализованные блоки", "normalized_blocks")
        _finalize_section("meanings", "Анализ смыслов", "analysis_meanings")

        if enabled["export"]:
            try:
                export_prompt_parts = [COMPETITOR_RUNTIME_GUARD, prompt_values["export"]]
                if enabled["master"] and prompt_values["master"]:
                    export_prompt_parts.insert(
                        0,
                        "Контекст оркестрации: это подзадача экспорта таблицы. "
                        "Верни только данные для листов в требуемом формате.",
                    )
                export_prompt = "\n\n".join(part for part in export_prompt_parts if part)
                result["table_export_raw"] = await llm.analyze(
                    export_prompt,
                    {
                        **common_payload,
                        "normalized_blocks": result["normalized_blocks"],
                        "analysis_meanings": result["analysis_meanings"],
                        "summary_general": result["summary_general"],
                    },
                )
                sheet1_text = _extract_marked_sheet_text(result["table_export_raw"], "### SHEET_1")
                sheet2_text = _extract_marked_sheet_text(result["table_export_raw"], "### SHEET_2")
                if sheet1_text:
                    result["table_blocks_output"] = sheet1_text
                if sheet2_text:
                    result["table_structure_output"] = sheet2_text
            except Exception as exc:  # noqa: BLE001
                result["export_stage_status"] = {"ok": False, "error": str(exc)}
                result["errors"].append(f"Экспорт таблицы: {exc}")
                result["export_matrix_ready"] = False
                result["export_matrix_reason"] = "Ошибка шага экспорта таблицы. Выгружен fallback."

        deterministic_sheet1, deterministic_sheet2, matrix_rows, site_columns_rows = _build_competitors_compare_and_site_text(
            result["structures_rows"], artifacts
        )
        result["sheet1_matrix_rows"] = matrix_rows
        result["sheet2_site_columns_rows"] = site_columns_rows
        if matrix_rows and len(matrix_rows) > 1 and site_columns_rows and len(site_columns_rows) > 1:
            result["export_matrix_ready"] = True
            if result["structures_rows_source"] in {"service_data_json_block", "fenced_json_block", "raw_json_array_scan"}:
                result["export_matrix_reason"] = "Восстановлен structures_rows из JSON-блока без маркера."
            elif result["structures_rows_source"] == "plain_text_scan":
                result["export_matrix_reason"] = "Восстановлен structures_rows из текстового списка блоков."
            else:
                result["export_matrix_reason"] = ""
        else:
            result["export_matrix_ready"] = False
            if not result["export_matrix_reason"]:
                result["export_matrix_reason"] = (
                    "Недостаточно структурированных данных для матрицы. "
                    "Выгрузка будет текстовым fallback."
                )
        if deterministic_sheet1:
            result["table_blocks_output"] = deterministic_sheet1
        elif not result.get("table_blocks_output"):
            result["table_blocks_output"] = result["normalized_blocks"]

        if deterministic_sheet2:
            result["table_structure_output"] = deterministic_sheet2
        elif not result.get("table_structure_output"):
            # В fallback используем summary, чтобы второй лист не был пустым.
            result["table_structure_output"] = result["summary_general"] or result["analysis_meanings"]

        logger.info(
            "competitors_run run_id=%s enabled_summary=%s enabled_blocks=%s enabled_meanings=%s enabled_export=%s "
            "len_summary=%s len_blocks=%s len_meanings=%s structures_rows_count=%s export_matrix_ready=%s "
            "export_matrix_reason=%s errors_count=%s reason_summary=%s reason_blocks=%s reason_meanings=%s "
            "export_stage_ok=%s",
            run_id,
            enabled["summary"],
            enabled["blocks"],
            enabled["messages"],
            enabled["export"],
            len(result.get("summary_general", "")),
            len(result.get("normalized_blocks", "")),
            len(result.get("analysis_meanings", "")),
            len(result.get("structures_rows", [])),
            result.get("export_matrix_ready"),
            result.get("export_matrix_reason", ""),
            len(result.get("errors", [])),
            result["section_status"]["summary"]["reason"],
            result["section_status"]["blocks"]["reason"],
            result["section_status"]["meanings"]["reason"],
            result["export_stage_status"].get("ok"),
        )
    except Exception as exc:  # noqa: BLE001
        result["export_matrix_ready"] = False
        if not result.get("export_matrix_reason"):
            result["export_matrix_reason"] = "Ошибка на этапе подготовки структурированных листов."
        result["errors"].append(str(exc))
        logger.info(
            "competitors_run_failed run_id=%s structures_rows_count=%s export_matrix_ready=%s "
            "export_matrix_reason=%s errors_count=%s",
            run_id,
            len(result.get("structures_rows", [])),
            result.get("export_matrix_ready"),
            result.get("export_matrix_reason", ""),
            len(result.get("errors", [])),
        )
    finally:
        try:
            (DATA_DIR / f"competitors_report_{run_id}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as write_exc:  # noqa: BLE001
            logger.warning("Failed to persist competitors report run_id=%s: %s", run_id, write_exc)

    return JSONResponse(content=result)


@app.post("/analyze-top10-structure")
async def analyze_top10_structure(
    search_queries: str = Form(""),
    region_id: str = Form("225"),
    top10_urls: str = Form(""),
    top10_variant: str = Form("normal"),
    prompt_master_top10: str = Form(""),
    prompt_blocks_core: str = Form(""),
    prompt_summary_norms: str = Form(""),
    prompt_proposed_structure: str = Form(""),
    prompt_export_optional: str = Form(""),
    enabled_master_top10: str | None = Form(None),
    enabled_blocks_core: str | None = Form(None),
    enabled_summary_norms: str | None = Form(None),
    enabled_proposed_structure: str | None = Form(None),
    enabled_export_optional: str | None = Form(None),
    # Backward compatibility.
    top10_prompt_1: str = Form(""),
    top10_prompt_2: str = Form(""),
    top10_table_blocks_prompt: str = Form(""),
    top10_table_structure_prompt: str = Form(""),
) -> JSONResponse:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_datetime = datetime.now().isoformat(timespec="seconds")
    run_screens_dir = SCREENSHOTS_DIR / f"top10_{run_id}"
    normalized_variant = "light" if str(top10_variant or "").strip().lower() == "light" else "normal"
    active_top10_prompts = DEFAULT_TOP10_PROMPTS_LIGHT if normalized_variant == "light" else DEFAULT_TOP10_PROMPTS
    result = {
        "run_id": run_id,
        "run_datetime": run_datetime,
        **_build_info_payload(),
        "top10_variant": normalized_variant,
        "top10_variant_label": "Light" if normalized_variant == "light" else "Обычный",
        "queries": [],
        "urls": [],
        "pages": [],
        "summary_report": "",
        "normalized_blocks": "",
        "structure_proposal": "",
        "structures_rows": [],
        "structures_rows_source": "no_structures_rows_found",
        "structures_rows_count": 0,
        "sheet1_matrix_rows": [],
        "sheet2_site_columns_rows": [],
        "sheet3_proposed_rows": [],
        "structure_parse_stats": {
            "rows_total": 0,
            "rows_with_system_id": 0,
            "dropped_as_reason_lines": 0,
            "parse_mode": "not_started",
        },
        "structure_parse_examples": {"accepted": [], "dropped": []},
        "export_bundle": {},
        "export_schema_version": "top10.v4.strict",
        "export_matrix_ready": False,
        "export_matrix_reason": "Матрица ещё не сформирована.",
        "export_structure_ready": False,
        "export_structure_reason": "Лист предложенной структуры ещё не сформирован.",
        "export_stage_status": {"ok": False, "error": "", "state": "analysis_pending"},
        "export_table_raw": "",
        "table_blocks_output": "",
        "table_structure_output": "",
        "table_proposed_output": "",
        "errors": [],
    }
    queries = _parse_queries(search_queries)
    result["queries"] = queries
    manual_urls = _parse_urls(top10_urls)
    if not queries and not manual_urls:
        result["errors"].append("Добавьте поисковые запросы или заполните список URL вручную.")
        return JSONResponse(content=result)

    prompt_values = {
        "master": _normalize_competitor_prompt(prompt_master_top10 or active_top10_prompts["master"]),
        "blocks_core": _normalize_competitor_prompt(prompt_blocks_core or top10_prompt_1 or active_top10_prompts["blocks_core"]),
        "summary_norms": _normalize_competitor_prompt(prompt_summary_norms or active_top10_prompts["summary_norms"]),
        "proposed_structure": _normalize_competitor_prompt(prompt_proposed_structure or top10_prompt_2 or active_top10_prompts["proposed_structure"]),
        "export_optional": _normalize_competitor_prompt(
            prompt_export_optional
            or top10_table_blocks_prompt
            or top10_table_structure_prompt
            or active_top10_prompts["export_optional"]
        ),
    }
    enabled = {
        "master": _is_enabled(enabled_master_top10, DEFAULT_TOP10_PROMPT_ENABLED["master"]),
        "blocks_core": _is_enabled(enabled_blocks_core, DEFAULT_TOP10_PROMPT_ENABLED["blocks_core"]),
        "summary_norms": _is_enabled(enabled_summary_norms, DEFAULT_TOP10_PROMPT_ENABLED["summary_norms"]),
        "proposed_structure": _is_enabled(enabled_proposed_structure, DEFAULT_TOP10_PROMPT_ENABLED["proposed_structure"]),
        "export_optional": _is_enabled(enabled_export_optional, DEFAULT_TOP10_PROMPT_ENABLED["export_optional"]),
    }
    required = {
        "blocks_core": "Нормализованные блоки",
        "summary_norms": "Общие выводы",
        "proposed_structure": "Предложение по структуре",
    }
    missing = [title for key, title in required.items() if enabled[key] and not prompt_values[key]]
    if missing:
        result["errors"].append(f"Пустые промпты: {', '.join(missing)}.")
        return JSONResponse(content=result)

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
            "run_id": run_id,
            "run_datetime": run_datetime,
            "queries": queries,
            "top_urls": url_counts,
            "pages": artifacts,
        }
        if enabled["blocks_core"]:
            blocks_prompt_parts = [COMPETITOR_RUNTIME_GUARD, COMPETITOR_STAGE_1_GUARD, prompt_values["blocks_core"]]
            if enabled["master"] and prompt_values["master"]:
                blocks_prompt_parts.insert(0, "Контекст оркестрации: шаг 1 из master-пайплайна top10.")
            blocks_prompt = "\n\n".join(part for part in blocks_prompt_parts if part)
            result["normalized_blocks"] = await llm.analyze(blocks_prompt, common_payload)
            extracted_rows, rows_source = _extract_competitor_structures_rows(result["normalized_blocks"])
            result["structures_rows"] = extracted_rows
            result["structures_rows_source"] = rows_source
            result["structures_rows_count"] = len(extracted_rows)
            if not result["structures_rows"]:
                result["export_matrix_ready"] = False
                result["export_matrix_reason"] = (
                    "Не удалось выделить structures_rows из ответа блока «Нормализованные блоки». "
                    "Будет использован текстовый fallback."
                )

        if enabled["summary_norms"]:
            summary_prompt_parts = [COMPETITOR_RUNTIME_GUARD, prompt_values["summary_norms"]]
            if enabled["master"] and prompt_values["master"]:
                summary_prompt_parts.insert(0, "Контекст оркестрации: шаг 2 из master-пайплайна top10.")
            summary_prompt = "\n\n".join(part for part in summary_prompt_parts if part)
            result["summary_report"] = await llm.analyze(
                summary_prompt,
                {
                    **common_payload,
                    "structures_rows": result["structures_rows"],
                    "normalized_blocks": result["normalized_blocks"],
                },
            )

        if enabled["proposed_structure"]:
            proposal_prompt_parts = [COMPETITOR_RUNTIME_GUARD, prompt_values["proposed_structure"]]
            if enabled["master"] and prompt_values["master"]:
                proposal_prompt_parts.insert(0, "Контекст оркестрации: шаг 3 из master-пайплайна top10.")
            proposal_prompt = "\n\n".join(part for part in proposal_prompt_parts if part)
            result["structure_proposal"] = await llm.analyze(
                proposal_prompt,
                {
                    **common_payload,
                    "structures_rows": result["structures_rows"],
                    "normalized_blocks": result["normalized_blocks"],
                    "summary_report": result["summary_report"],
                },
            )

        if enabled["export_optional"]:
            export_prompt_parts = [COMPETITOR_RUNTIME_GUARD, prompt_values["export_optional"]]
            if enabled["master"] and prompt_values["master"]:
                export_prompt_parts.insert(0, "Контекст оркестрации: шаг 4 (опциональный экспорт).")
            export_prompt = "\n\n".join(part for part in export_prompt_parts if part)
            result["export_table_raw"] = await llm.analyze(
                export_prompt,
                {
                    **common_payload,
                    "structures_rows": result["structures_rows"],
                    "proposed_structure_text": result["structure_proposal"],
                },
            )

            compare_text = _extract_marked_sheet_text(result["export_table_raw"], "### SHEET_1_COMPARE")
            proposal_table_text = _extract_marked_sheet_text(result["export_table_raw"], "### SHEET_2_PROPOSAL")
            if compare_text:
                result["table_blocks_output"] = compare_text
            # Для листа "Предложенная структура" приоритет у полноценной вкладки structure_proposal.
            # SHEET_2_PROPOSAL используем только как запасной источник, если основной отсутствует.
            if proposal_table_text and not result.get("structure_proposal"):
                result["table_proposed_output"] = proposal_table_text

        deterministic_sheet1, deterministic_sheet2, matrix_rows, site_columns_rows = _build_competitors_compare_and_site_text(
            result["structures_rows"], artifacts
        )
        result["sheet1_matrix_rows"] = matrix_rows
        result["sheet2_site_columns_rows"] = site_columns_rows
        expected_sites = len({_canonical_site(item.get("url") or "") for item in url_counts if item.get("url")})
        matrix_has_sites = bool(matrix_rows and matrix_rows[0] and len(matrix_rows[0]) >= 2)
        matrix_site_count = len(matrix_rows[0]) - 1 if matrix_has_sites else 0
        matrix_has_rows = bool(len(matrix_rows) > 1 and site_columns_rows and len(site_columns_rows) > 1)
        non_first_site_checks_ok = True
        if expected_sites > 1:
            if matrix_site_count < 2:
                non_first_site_checks_ok = False
            else:
                non_first_site_checks_ok = _matrix_has_non_first_site_checks(matrix_rows)

        if matrix_has_rows and non_first_site_checks_ok:
            result["export_matrix_ready"] = True
            if result["structures_rows_source"] in {"service_data_json_block", "fenced_json_block", "raw_json_array_scan"}:
                result["export_matrix_reason"] = "Восстановлен structures_rows из JSON-блока без маркера."
            elif result["structures_rows_source"] == "plain_text_scan":
                result["export_matrix_reason"] = "Восстановлен structures_rows из текстового списка блоков."
            else:
                result["export_matrix_reason"] = ""
        else:
            result["export_matrix_ready"] = False
            if expected_sites > 1 and matrix_site_count >= 2 and not non_first_site_checks_ok:
                result["export_matrix_reason"] = (
                    "Матрица собрана некорректно: галочки не распределились по всем сайтам. "
                    "Выгрузка будет текстовым fallback."
                )
            elif not result["export_matrix_reason"]:
                result["export_matrix_reason"] = (
                    "Недостаточно структурированных данных для матрицы. "
                    "Выгрузка будет текстовым fallback."
                )

        if deterministic_sheet1:
            result["table_blocks_output"] = deterministic_sheet1
        elif not result["table_blocks_output"]:
            result["table_blocks_output"] = (
                _build_blocks_comparison_from_rows(result["structures_rows"])
                or result["summary_report"]
                or result["normalized_blocks"]
            )
        if deterministic_sheet2:
            result["table_structure_output"] = deterministic_sheet2
        elif not result["table_structure_output"]:
            result["table_structure_output"] = result["structure_proposal"]

        # Явно фиксируем источник для листа "structure": только из вкладки "Предложение по структуре".
        # Это защищает от случаев, когда export_optional вернул посайтовый список вместо итогового конструктора.
        result["table_proposed_output"] = result["structure_proposal"] or result.get("table_proposed_output", "")

        proposed_rows, structure_ready, structure_reason, parse_stats, parse_examples = _build_top10_proposed_structure_rows(
            result.get("table_proposed_output", ""),
            result.get("structure_proposal", ""),
        )
        result["sheet3_proposed_rows"] = proposed_rows
        result["export_structure_ready"] = structure_ready
        result["export_structure_reason"] = structure_reason
        result["structure_parse_stats"] = parse_stats
        result["structure_parse_examples"] = parse_examples

        export_bundle = {
            "sheet1_matrix_rows": result["sheet1_matrix_rows"],
            "sheet2_site_columns_rows": result["sheet2_site_columns_rows"],
            "sheet3_proposed_rows": result["sheet3_proposed_rows"],
            "schema_version": "top10.v4.strict",
            "export_ready": bool(result["export_matrix_ready"] and result["export_structure_ready"]),
            "export_reason": (
                " ".join(
                    part for part in [
                        str(result.get("export_matrix_reason") or "").strip(),
                        str(result.get("export_structure_reason") or "").strip(),
                    ] if part
                ).strip()
            ),
        }
        bundle_ok, bundle_reason = _validate_top10_export_bundle(export_bundle)
        if not bundle_ok:
            result["export_matrix_ready"] = False
            result["export_structure_ready"] = False
            result["export_matrix_reason"] = bundle_reason
            result["export_structure_reason"] = bundle_reason
            export_bundle["export_ready"] = False
            export_bundle["export_reason"] = bundle_reason
            result["export_stage_status"] = {
                "ok": False,
                "error": bundle_reason,
                "state": "analysis_export_bundle_failed",
            }
        else:
            result["export_stage_status"] = {
                "ok": True,
                "error": "",
                "state": "analysis_export_bundle_ready",
            }
        result["export_bundle"] = export_bundle

        (DATA_DIR / f"top10_report_{run_id}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        result["export_matrix_ready"] = False
        if not result.get("export_matrix_reason"):
            result["export_matrix_reason"] = "Ошибка на этапе подготовки структурированных листов."
        result["export_stage_status"] = {
            "ok": False,
            "error": str(exc),
            "state": "analysis_exception",
        }
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
    requested_report_type = str(payload.get("report_type", "site"))
    report_type = requested_report_type
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
        bundle = report_payload.get("export_bundle")
        if not isinstance(bundle, dict):
            bundle = {
                "sheet1_matrix_rows": report_payload.get("sheet1_matrix_rows") or [],
                "sheet2_site_columns_rows": report_payload.get("sheet2_site_columns_rows") or [],
                "sheet3_proposed_rows": report_payload.get("sheet3_proposed_rows") or [],
                "schema_version": str(report_payload.get("export_schema_version") or "top10.v4.strict"),
                "export_ready": bool(report_payload.get("export_matrix_ready") and report_payload.get("export_structure_ready")),
                "export_reason": (
                    str(report_payload.get("export_matrix_reason") or "")
                    or str(report_payload.get("export_structure_reason") or "")
                ),
            }
        report_payload["export_bundle"] = bundle
        report_payload["export_schema_version"] = "top10.v4.strict"

        bundle_ok, bundle_reason = _validate_top10_export_bundle(bundle)
        if not bundle_ok:
            return JSONResponse(
                content={"errors": [f"Экспорт top10.v4 strict отклонён: {bundle_reason}"]},
                status_code=400,
            )
        report_payload["sheet1_matrix_rows"] = bundle.get("sheet1_matrix_rows") or []
        report_payload["sheet2_site_columns_rows"] = bundle.get("sheet2_site_columns_rows") or []
        report_payload["sheet3_proposed_rows"] = bundle.get("sheet3_proposed_rows") or []
        report_payload["export_matrix_ready"] = True
        report_payload["export_structure_ready"] = True
        report_payload["export_matrix_reason"] = ""
        report_payload["export_structure_reason"] = ""
        report_payload["export_stage_status"] = {
            "ok": True,
            "error": "",
            "state": "sheets_export_request_sent",
        }
    elif report_type == "competitors":
        # Для Apps Script конкуренты отправляются в 2-листовом формате:
        # Лист 1: сравнительный анализ (нормализованные блоки x сайты).
        # Лист 2: структура каждого сайта (сайты в колонках, блоки под ними).
        # analysis_structure/structure_proposal оставляем как текстовый fallback.
        sheet1_text = str(report_payload.get("table_blocks_output", "") or report_payload.get("normalized_blocks", "")).strip()
        sheet2_text = str(report_payload.get("table_structure_output", "") or report_payload.get("summary_general", "")).strip()
        matrix_rows = report_payload.get("sheet1_matrix_rows") or []
        site_columns_rows = report_payload.get("sheet2_site_columns_rows") or []
        export_matrix_ready = bool(report_payload.get("export_matrix_ready"))
        export_matrix_reason = str(report_payload.get("export_matrix_reason") or "")

        matrix_ok, matrix_reason = _validate_sheet1_matrix_rows(matrix_rows)
        sites_ok, sites_reason = _validate_sheet2_site_columns_rows(site_columns_rows)
        if not (matrix_ok and sites_ok):
            export_matrix_ready = False
            details = " ".join(part for part in [matrix_reason, sites_reason] if part).strip()
            export_matrix_reason = (
                f"{export_matrix_reason} {details}".strip()
                if export_matrix_reason
                else details or "Валидация матрицы не пройдена. Выгружен текстовый fallback."
            )
            matrix_rows = []
            site_columns_rows = []

        if isinstance(matrix_rows, list):
            report_payload["sheet1_matrix_rows"] = matrix_rows
        if isinstance(site_columns_rows, list):
            report_payload["sheet2_site_columns_rows"] = site_columns_rows
        report_payload["export_matrix_ready"] = export_matrix_ready
        report_payload["export_matrix_reason"] = export_matrix_reason
        report_payload["analysis_structure"] = sheet1_text or sheet2_text
        report_payload["structure_proposal"] = sheet2_text or sheet1_text
        report_type = "top10"
    try:
        from app.apps_script_sheets import AppsScriptSheetsExporter

        exporter = AppsScriptSheetsExporter(webhook_url=webhook_url)
        result = exporter.export(report_type, report_payload)
        if requested_report_type == "top10":
            has_compare = bool(str(result.get("compare_sheet") or "").strip())
            has_sites = bool(str(result.get("sites_sheet") or "").strip())
            has_structure = bool(str(result.get("structure_sheet") or "").strip())
            if not (has_compare and has_sites and has_structure):
                return JSONResponse(
                    content={
                        "errors": [
                            "Скрипт Google Sheets не поддерживает экспорт top10.v4 strict "
                            "(нужны compare_sheet, sites_sheet и structure_sheet)."
                        ]
                    },
                    status_code=400,
                )
        return JSONResponse(content=result)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(content={"errors": [str(exc)]}, status_code=400)

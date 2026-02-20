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
from app.metrics import TopPage, dataframe_preview, parse_metrics_files

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
Используй каркас из SYSTEM_OUTPUT_STANDARD_v2_NO_ROLE_OUTPUT.
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
Используй каркас из SYSTEM_OUTPUT_STANDARD_v2_NO_ROLE_OUTPUT.
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
Используй каркас из SYSTEM_OUTPUT_STANDARD_v2_NO_ROLE_OUTPUT.
В «Детальном разборе» структура:
«Общие выводы по всем страницам» → далее для каждой страницы блоки A/B/C.
</формат_вывода>

</PROMPT_TOP_ENTRY_PAGES_SCREENSHOTS_UX_v1>""",
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

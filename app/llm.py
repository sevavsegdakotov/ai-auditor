from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from app.config import settings


class LLMClient:
    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def analyze(self, system_prompt: str, payload: dict[str, Any]) -> str:
        response = await self.client.responses.create(
            model=settings.openai_model,
            input=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        "Проанализируй входные данные и выдай структурированный вывод.\\n\\n"
                        f"DATA_JSON:\\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
                    ),
                },
            ],
        )
        return response.output_text or "Модель не вернула текстовый ответ."

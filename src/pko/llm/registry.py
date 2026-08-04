"""Реестр моделей: две роли, два endpoint'а.

Подход повторяет тот, что уже принят в анализируемом проекте (`ModelSpec`,
OpenAI-совместимый base_url, ключ и upstream-имя модели из окружения). Ключи
берутся только из переменных окружения и никогда не сохраняются в отчётах,
кеше и логах.

Роли:
  * `assembler` — собирает PKO-модель из кандидатов, возвращает только JSON;
  * `writer`    — пишет русский текст по готовой модели и кода не видит.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

# Для каждой роли: (переменная base_url, переменная модели, переменная ключа).
# Первый вариант — «родные» переменные PKO, дальше — уже принятые в проекте имена.
_ENV = {
    "assembler": {
        "base_url": ("PKO_ASSEMBLER_BASE_URL", "GLM_52_API", "GLM_51_API", "LLM_BASE_URL"),
        "model": ("PKO_ASSEMBLER_MODEL", "GLM_MODEL_NAME", "LLM_MODEL_NAME"),
        "api_key": ("PKO_ASSEMBLER_API_KEY", "GLM_API_KEY", "LLM_API_KEY"),
        "default_model": "GLM-5.2",
    },
    "writer": {
        "base_url": ("PKO_WRITER_BASE_URL", "DEEPSEEK_FLASH_API_BASE", "DEEPSEEK_BASE_URL"),
        "model": ("PKO_WRITER_MODEL", "DEEPSEEK_FLASH_MODEL_NAME", "DEEPSEEK_FLASH_MODEL"),
        "api_key": ("PKO_WRITER_API_KEY", "DEEPSEEK_API_KEY"),
        "default_model": "DeepSeek-V4-Flash",
    },
}


@dataclass(frozen=True)
class ModelSpec:
    role: str
    base_url: str
    model: str
    api_key: str
    extra_body: dict[str, Any] | None = None

    @property
    def upstream_model(self) -> str:
        """DeepSeek-endpoint игнорирует имя модели — как и в анализируемом проекте, шлём пустое."""
        return "" if "deep" in self.model.lower() else self.model

    def masked(self) -> dict[str, str]:
        return {"role": self.role, "base_url": self.base_url, "model": self.model,
                "api_key": "<задан>" if self.api_key else "<пусто>"}


def get_spec(role: str, thinking: bool = False) -> ModelSpec | None:
    """Вернуть настройки роли или None, если endpoint не задан в окружении."""
    conf = _ENV.get(role)
    if not conf:
        raise ValueError(f"неизвестная роль модели: {role}")

    base_url = _first_env(conf["base_url"])
    if not base_url:
        return None

    model = _first_env(conf["model"]) or conf["default_model"]
    api_key = _first_env(conf["api_key"]) or "not-needed"
    extra: dict[str, Any] | None = None
    if thinking and "deep" in model.lower():
        extra = {"chat_template_kwargs": {"enable_thinking": True}}

    return ModelSpec(role=role, base_url=base_url.rstrip("/"), model=model,
                     api_key=api_key, extra_body=extra)


def _first_env(names: tuple[str, ...]) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return ""

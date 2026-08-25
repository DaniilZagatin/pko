"""Реестр моделей: роли пайплайна прогресса.

Ключи берутся только из переменных окружения и никогда не сохраняются в
отчётах, кеше и логах.

Роли:
  * `planner` — текст слайдов PPTX → JSON плана;
  * `matcher` — пункт плана + кандидаты кода целевого репозитория → JSON вердикта.

Обе по умолчанию используют общие `PKO_ASSEMBLER_*` (унаследованное имя —
исторически это была роль сборщика в другом инструменте; здесь это просто
общий дефолт для «вернуть JSON по структурированному входу», чтобы обе роли
работали сразу на одной уже настроенной паре endpoint/ключ). `PKO_PLANNER_*`/
`PKO_MATCHER_*` переопределяют его, если ролям нужны разные модели.

Endpoint роли можно переопределить с командной строки, но ключ берётся только
из переменной окружения: значение, переданное флагом, осело бы в истории
оболочки и в списке процессов.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from pko.errors import PkoError

# Для каждой роли: (переменная base_url, переменная модели, переменная ключа).
_ENV = {
    "planner": {
        "base_url": ("PKO_PLANNER_BASE_URL", "PKO_ASSEMBLER_BASE_URL", "GLM_52_API", "GLM_51_API"),
        "model": ("PKO_PLANNER_MODEL", "PKO_ASSEMBLER_MODEL", "GLM_MODEL_NAME"),
        "api_key": ("PKO_PLANNER_API_KEY", "PKO_ASSEMBLER_API_KEY", "GLM_API_KEY"),
        "default_model": "GLM-5.2",
    },
    "matcher": {
        "base_url": ("PKO_MATCHER_BASE_URL", "PKO_ASSEMBLER_BASE_URL", "GLM_52_API", "GLM_51_API"),
        "model": ("PKO_MATCHER_MODEL", "PKO_ASSEMBLER_MODEL", "GLM_MODEL_NAME"),
        "api_key": ("PKO_MATCHER_API_KEY", "PKO_ASSEMBLER_API_KEY", "GLM_API_KEY"),
        "default_model": "GLM-5.2",
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
        """DeepSeek-endpoint игнорирует имя модели — если роль сконфигурирована под него."""
        return "" if "deep" in self.model.lower() else self.model

    def masked(self) -> dict[str, str]:
        return {"role": self.role, "base_url": self.base_url, "model": self.model,
                "api_key": "<задан>" if self.api_key else "<пусто>"}


def get_spec(
    role: str,
    thinking: bool = False,
    base_url: str = "",
    model: str = "",
    api_key_env: str = "",
) -> ModelSpec | None:
    """Вернуть настройки роли или None, если endpoint нигде не задан.

    `base_url` и `model` приходят с командной строки и имеют приоритет над
    окружением. `api_key_env` — имя переменной, а не сам ключ: значение во флаге
    попало бы в историю оболочки и в вывод `ps`.
    """
    conf = _ENV.get(role)
    if not conf:
        raise ValueError(f"неизвестная роль модели: {role}")

    base_url = (base_url or "").strip() or _first_env(conf["base_url"])
    if not base_url:
        return None

    model = (model or "").strip() or _first_env(conf["model"]) or conf["default_model"]
    if api_key_env:
        # Оператор назвал переменную явно. Если она пуста, молчаливый откат
        # отправил бы ключ внутреннего контура на чужой endpoint — это утечка,
        # а не удобство. Лучше остановиться и сказать, чего не хватает.
        api_key = (os.environ.get(api_key_env) or "").strip()
        if not api_key:
            raise PkoError(
                f"переменная окружения {api_key_env} пуста или не задана",
                hint=f"экспортируйте ключ перед запуском: export {api_key_env}=...",
            )
    else:
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

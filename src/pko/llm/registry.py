"""Реестр моделей: две роли, два endpoint'а.

Подход повторяет тот, что уже принят в анализируемом проекте (`ModelSpec`,
OpenAI-совместимый base_url, ключ и upstream-имя модели из окружения). Ключи
берутся только из переменных окружения и никогда не сохраняются в отчётах,
кеше и логах.

Роли:
  * `assembler` — собирает PKO-модель из кандидатов, возвращает только JSON;
  * `writer`    — пишет русский текст по готовой модели и кода не видит;
  * `scout`     — агент разведки: читает файлы репозитория и предлагает факты;
  * `planner`   — пайплайн прогресса (`pko.progress`): текст слайдов → JSON плана;
  * `matcher`   — пайплайн прогресса: пункт плана + кандидаты кода → JSON вердикта.

Endpoint роли можно переопределить с командной строки (`overrides`), но ключ
берётся только из переменной окружения: значение, переданное флагом, осело бы в
истории оболочки и в списке процессов.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from pko.errors import PkoError

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
    "scout": {
        "base_url": ("PKO_SCOUT_BASE_URL", "PKO_ASSEMBLER_BASE_URL", "GLM_52_API", "GLM_51_API"),
        "model": ("PKO_SCOUT_MODEL", "PKO_ASSEMBLER_MODEL", "GLM_MODEL_NAME"),
        "api_key": ("PKO_SCOUT_API_KEY", "PKO_ASSEMBLER_API_KEY", "GLM_API_KEY"),
        "default_model": "GLM-5.2",
    },
    # Отдельных endpoint'ов для пайплайна прогресса на первом шаге не требуем:
    # роль по умолчанию использует уже настроенный `assembler` (та же задача —
    # вернуть JSON по структурированному входу), а PKO_PLANNER_* переопределяет
    # его, если для этой роли понадобится своя модель.
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
        """DeepSeek-endpoint игнорирует имя модели — как и в анализируемом проекте, шлём пустое."""
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
    allowed_hosts: str | list[str] | tuple[str, ...] | None = None,
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
    if role == "scout":
        _require_allowed_scout_host(base_url, allowed_hosts)

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


def _require_allowed_scout_host(
    base_url: str, allowed_hosts: str | list[str] | tuple[str, ...] | None = None
) -> None:
    """Разрешить отправку кода только на явно одобренный host scout-endpoint.

    По одному URL невозможно надёжно определить, внутренний endpoint или
    внешний. Поэтому граница закрыта по умолчанию: оператор перечисляет
    корпоративные hosts флагом либо `PKO_SCOUT_ALLOWED_HOSTS`. Значения —
    точные hostname или hostname:port, без wildcard.
    """
    raw = allowed_hosts
    if raw is None:
        raw = os.environ.get("PKO_SCOUT_ALLOWED_HOSTS", "")
    if isinstance(raw, str):
        allowed = {item.strip().lower() for item in raw.split(",") if item.strip()}
    else:
        allowed = {str(item).strip().lower() for item in (raw or ()) if str(item).strip()}

    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise PkoError(
            f"Некорректный scout endpoint: {base_url}",
            "Используйте http(s) URL без учётных данных в адресе.",
        )
    if not allowed:
        raise PkoError(
            "Не задан allowlist внутренних hosts для scout endpoint.",
            "Задайте PKO_SCOUT_ALLOWED_HOSTS=llm.company.local или передайте "
            "--scout-allowed-hosts; без этого PKO не отправляет код в модель.",
        )

    host = (parsed.hostname or "").lower()
    host_port = parsed.netloc.rsplit("@", 1)[-1].lower()
    if host not in allowed and host_port not in allowed:
        raise PkoError(
            f"Scout endpoint {host_port} отсутствует в allowlist внутренних hosts.",
            "Добавьте точный host в PKO_SCOUT_ALLOWED_HOSTS только после проверки, "
            "что endpoint находится внутри компании.",
        )

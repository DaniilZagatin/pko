"""Минимальный OpenAI-совместимый клиент на стандартной библиотеке.

Зависимостей нет намеренно: PKO должен запускаться во внутреннем контуре без
установки пакетов. Ответы кешируются по (модель, промпт) — при `temperature=0`
это даёт воспроизводимость отчёта, которой требует §5.2.2.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pko.errors import LlmError
from pko.llm.registry import ModelSpec
from pko.util.paths import harden_dir, harden_file

DEFAULT_CACHE_DIR = Path.home() / ".pko" / "llm-cache"


@dataclass
class ChatClient:
    spec: ModelSpec
    timeout: int = 120
    cache_dir: Path | None = None
    use_cache: bool = True

    def health(self) -> bool:
        """Проверка доступности endpoint'а: `GET {base_url}/models`."""
        try:
            self._request("GET", "/models", None)
            return True
        except LlmError:
            return False

    def complete(self, system: str, user: str, temperature: float = 0.0,
                 max_tokens: int = 2000) -> str:
        payload: dict[str, Any] = {
            "model": self.spec.upstream_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.spec.extra_body:
            payload.update(self.spec.extra_body)

        key = self._cache_key(payload)
        cached = self._read_cache(key)
        if cached is not None:
            return cached

        data = self._request("POST", "/chat/completions", payload)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError(
                f"Неожиданный ответ модели {self.spec.model}.",
                "Проверьте, что endpoint OpenAI-совместим.",
            ) from exc

        self._write_cache(key, text)
        return text

    # --- внутреннее -------------------------------------------------------
    def _request(self, method: str, path: str, payload: dict[str, Any] | None) -> Any:
        url = f"{self.spec.base_url}{path}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", "application/json")
        if self.spec.api_key:
            req.add_header("Authorization", f"Bearer {self.spec.api_key}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise LlmError(
                f"Модель {self.spec.model} вернула HTTP {exc.code}.",
                detail or "Проверьте base_url, имя модели и права доступа.",
            ) from exc
        except urllib.error.URLError as exc:
            raise LlmError(
                f"Не удалось обратиться к {self.spec.base_url}.",
                f"{exc.reason}. Проверьте подключение к внутренней сети.",
            ) from exc
        except json.JSONDecodeError as exc:
            raise LlmError(f"Модель {self.spec.model} вернула не JSON.") from exc

    def _cache_key(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(f"{self.spec.base_url}|{raw}".encode("utf-8")).hexdigest()

    def _cache_file(self, key: str) -> Path:
        root = self.cache_dir or DEFAULT_CACHE_DIR
        return root / f"{key}.txt"

    def _read_cache(self, key: str) -> str | None:
        if not self.use_cache:
            return None
        path = self._cache_file(key)
        if path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                return None
        return None

    def _write_cache(self, key: str, text: str) -> None:
        if not self.use_cache:
            return
        path = self._cache_file(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError:
            return
        # Кеш содержит описание корпоративной системы, полученное моделью, —
        # та же конфиденциальность, что и у зеркала репозитория.
        harden_dir(path.parent, path.parent.parent)
        harden_file(path)


def chat(spec: ModelSpec, system: str, user: str, **kwargs: Any) -> str:
    return ChatClient(spec=spec).complete(system=system, user=user, **kwargs)

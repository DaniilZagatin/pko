"""Трасса агентского прогона.

Это основной продукт режима: по трассе человек находит шаг, на котором агент
ошибся, и правит промпт или код. Поэтому результат `read_file` пишется целиком —
без него нельзя понять, что именно агент видел, когда сделал неверный вывод.

Плата за это: файл содержит код анализируемой системы, то есть конфиденциален
наравне с кешем зеркал. Права выставляются `0600` при записи.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pko.util.paths import harden_file


@dataclass
class TraceStep:
    """Один шаг цикла: что спросили, что ответили, что из этого вышло."""

    number: int
    request_messages: int
    raw_response: str
    action: str                      # tool | final | parse_error
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    result: str = ""                 # полный результат инструмента
    ok: bool = True
    seconds: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    from_cache: bool = False
    note: str = ""
    # Опись того, что реально ушло в модель на этом шаге: роль, размер и
    # начало каждого сообщения. Без этого по трассе не понять, видела ли
    # модель нужный кусок, — а именно это и тюнят. Целиком сообщения не
    # храним: окно истории повторяется на каждом шаге, и полная копия росла
    # бы квадратично от длины прогона. Полный текст результата инструмента
    # и так лежит в `result` того шага, где он был получен.
    request: list[dict[str, Any]] = field(default_factory=list)
    # Вердикты проверки фактов, записанных на этом шаге: шаг с отброшенным
    # фактом не должен выглядеть удачным.
    verdicts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "request_messages": self.request_messages,
            "request": self.request,
            "verdicts": self.verdicts,
            "raw_response": self.raw_response,
            "action": self.action,
            "tool": self.tool,
            "args": self.args,
            "result": self.result,
            "ok": self.ok,
            "seconds": round(self.seconds, 3),
            "usage": self.usage,
            "from_cache": self.from_cache,
            "note": self.note,
        }


@dataclass
class Trace:
    """Полный ход разведки одной версии."""

    repo: str = ""
    commit: str = ""
    version_label: str = ""
    endpoint: str = ""
    model: str = ""
    prompt_version: str = ""
    prompt_sha: str = ""
    # Какие паки промпта подключил детектор стека и почему. Без этого прогоны
    # несравнимы: разный набор паков — разные условия, а не разное качество.
    packs: list[str] = field(default_factory=list)
    stack: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    steps: list[TraceStep] = field(default_factory=list)
    stop_reason: str = ""
    incomplete: bool = False
    accepted_facts: list[dict[str, Any]] = field(default_factory=list)
    rejected_facts: list[dict[str, Any]] = field(default_factory=list)
    bytes_read: int = 0
    files_read: int = 0

    def add(self, step: TraceStep) -> TraceStep:
        self.steps.append(step)
        return step

    def totals(self) -> dict[str, Any]:
        return {
            "steps": len(self.steps),
            "tool_calls": sum(1 for s in self.steps if s.action == "tool"),
            "parse_errors": sum(1 for s in self.steps if s.action == "parse_error"),
            "accepted": len(self.accepted_facts),
            "rejected": len(self.rejected_facts),
            "bytes_read": self.bytes_read,
            "files_read": self.files_read,
            "seconds": round(sum(s.seconds for s in self.steps), 2),
            "prompt_tokens": _sum_usage(self.steps, "prompt_tokens"),
            "completion_tokens": _sum_usage(self.steps, "completion_tokens"),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "pko-agent-trace/0.1",
            "repo": self.repo,
            "commit": self.commit,
            "version_label": self.version_label,
            "endpoint": self.endpoint,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "prompt_sha": self.prompt_sha,
            "packs": self.packs,
            "stack": self.stack,
            "started_at": self.started_at,
            "stop_reason": self.stop_reason,
            "incomplete": self.incomplete,
            "totals": self.totals(),
            "accepted_facts": self.accepted_facts,
            "rejected_facts": self.rejected_facts,
            "steps": [s.to_dict() for s in self.steps],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        # В трассе лежит код анализируемой системы — доступ только владельцу.
        harden_file(path)
        return path


def _sum_usage(steps: list[TraceStep], key: str) -> int:
    total = 0
    for step in steps:
        value = step.usage.get(key)
        if isinstance(value, int):
            total += value
    return total

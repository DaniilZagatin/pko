"""Сторож текста отчёта.

Писатель (DeepSeek) получает только готовую модель и не видит кода, но проверять
его всё равно нужно: текст не должен вводить идентификаторы, счётчики и пути,
которых в модели нет. Нарушение не правится — текст целиком отбрасывается, и
отчёт собирается детерминированным шаблоном. Так «красивая» формулировка не может
подменить факт.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Идентификаторы объектов управления: NEED-…, CP-…, AP-…, BBB-001, AO-010, GRD-003.
# Взгляд назад нужен, чтобы не выхватывать «NEED-001» из середины «CHK-NEED-001».
ID_PATTERN = re.compile(r"(?<![A-Za-z0-9\-])(?:NEED|CP|AP-CP|AP|BBB|AO|GRD)-[A-Za-z0-9\-]{2,}\b")
PATH_PATTERN = re.compile(r"\b[\w./-]+\.(?:py|ts|tsx|yaml|yml|toml|json)\b")
CHECK_ID_PATTERN = re.compile(r"\bCHK-[A-Z]+-\d+\b")


@dataclass(frozen=True)
class GuardViolation:
    code: str
    detail: str

    def render(self) -> str:
        return f"{self.code}: {self.detail}"


def check_text(text: str, allowed_ids: set[str], allowed_paths: set[str] | None = None
               ) -> list[GuardViolation]:
    """Вернуть нарушения. Пустой список — текст можно публиковать."""
    violations: list[GuardViolation] = []
    allowed_paths = allowed_paths or set()

    unknown_ids = sorted(
        {m for m in ID_PATTERN.findall(text) if m not in allowed_ids and not _is_check_id(m)}
    )
    if unknown_ids:
        violations.append(
            GuardViolation(
                "UNKNOWN_ID",
                "в тексте есть идентификаторы, которых нет в модели: " + ", ".join(unknown_ids[:5]),
            )
        )

    unknown_paths = sorted({p for p in PATH_PATTERN.findall(text) if p not in allowed_paths})
    if unknown_paths:
        violations.append(
            GuardViolation(
                "UNKNOWN_PATH",
                "в тексте есть пути к файлам, не подтверждённые доказательствами: "
                + ", ".join(unknown_paths[:5]),
            )
        )
    return violations


def _is_check_id(value: str) -> bool:
    return bool(CHECK_ID_PATTERN.fullmatch(value))

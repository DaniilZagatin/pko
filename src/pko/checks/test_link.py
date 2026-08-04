"""Единственная точка связи «готовый тест ↔ ограничение».

Раньше связь вычислялась дважды: паспорт guardrail всегда писал «не подтверждён
тестом», а проверка Gate независимо сканировала имена тестов в JUnit. Результат
получался взаимоисключающим — карточка выдавала допуск со ссылкой на негативный
тест, а модель в том же прогоне называла то же ограничение неподтверждённым.
Теперь обе стороны спрашивают этот модуль.

Связь строится по именам тестов: запускать тесты анализируемого проекта PKO не
имеет права, поэтому единственное доступное доказательство — готовый JUnit XML.
Правило намеренно строгое, чтобы случайное совпадение слова не превращалось в
доказательство:

  * для ограничения-числа: в имени теста должны присутствовать **все** значащие
    части ключа (`sql_timeout` → и `sql`, и `timeout`) и хотя бы один маркер
    применения ограничения;
  * для инварианта «только чтение»: и глагол изменения данных, и маркер запрета.

Имя подтвердившего теста всегда выписывается в паспорт, чтобы человек мог
проверить связь, а не поверить ей.
"""

from __future__ import annotations

import re

from pko.extractors.runner import Extraction
from pko.extractors.test_reports import PASSED, SKIPPED

# Ключ, под которым живёт инвариант «данные не изменяются».
READ_ONLY_KEY = "read_only"

# Маркеры того, что тест проверяет именно срабатывание ограничения.
ENFORCEMENT_MARKERS = (
    "limit", "timeout", "exceed", "reject", "denied", "deny", "forbidden",
    "blocked", "applied", "enforc", "guard", "invalid", "negative", "raises",
    "not_allowed", "readonly", "read_only",
)

# Глаголы изменения данных и маркеры запрета — для инварианта «только чтение».
WRITE_VERBS = ("update", "insert", "delete", "drop", "truncate", "alter", "write", "modif")
DENIAL_MARKERS = ("forbidden", "reject", "denied", "deny", "blocked", "readonly",
                  "read_only", "not_allowed", "raises", "invalid", "guard")

# Части ключа, которые ничего не сужают и потому не требуются в имени теста.
_STOP_TOKENS = {"the", "app", "default", "value", "seconds", "sec", "ms"}

_SPLIT = re.compile(r"[^a-z0-9]+")


def junit_case_names(extraction: Extraction, outcome: str = PASSED) -> list[str]:
    """Имена тестов с заданным исходом. По умолчанию — только фактически прошедшие.

    Пропущенный тест не может ничего подтверждать: он не выполнялся. Раньше сюда
    попадали имена всех тестов подряд, и отчёт, где критический сценарий скипнут,
    засчитывался как доказательство.
    """
    names: list[str] = []
    for report in extraction.by_kind("TEST_REPORT"):
        for case in report.value.get("cases", []):
            if isinstance(case, dict):
                if case.get("outcome") == outcome:
                    names.append(str(case.get("name", "")))
            elif outcome == PASSED:
                # Отчёт из более старой версии PKO без исходов: считать его
                # доказательством нельзя, поэтому имя не берётся.
                continue
    return [n for n in names if n]


def skipped_case_names(extraction: Extraction) -> list[str]:
    return junit_case_names(extraction, outcome=SKIPPED)



def confirming_cases(limit_key: str, extraction: Extraction) -> list[str]:
    """Прошедшие тесты, которые доказывают это ограничение.

    Пустой список — доказательства нет. Пропущенные тесты сюда не попадают.
    """
    cases = junit_case_names(extraction, outcome=PASSED)
    if not cases:
        return []
    if limit_key == READ_ONLY_KEY:
        return [c for c in cases if _confirms_read_only(c)]
    tokens = _key_tokens(limit_key)
    if not tokens:
        return []
    return [c for c in cases if _confirms_limit(c, tokens)]


def _confirms_read_only(case_name: str) -> bool:
    low = case_name.lower()
    return any(v in low for v in WRITE_VERBS) and any(d in low for d in DENIAL_MARKERS)


def _confirms_limit(case_name: str, tokens: list[str]) -> bool:
    low = case_name.lower()
    if not all(t in low for t in tokens):
        return False
    return any(m in low for m in ENFORCEMENT_MARKERS)


def _key_tokens(limit_key: str) -> list[str]:
    parts = [p for p in _SPLIT.split(limit_key.lower()) if p]
    return [p for p in parts if len(p) >= 3 and p not in _STOP_TOKENS]

"""Чтение business_intent.yaml — единственного ручного входа PKO.

Стандарт (§4.0) запрещает выводить из кода потребность, целевое состояние,
границы автономности и владельца результата. Их подтверждает человек в этом
файле. Файла нет — PKO выпускает черновик: модель строится, но решение Gate не
принимается.

Файл ищется в анализируемом репозитории на конкретном коммите, чтобы
подтверждение было привязано к версии, а не к «текущему состоянию».
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pko.util.yamlmini import YamlSubsetError, loads

SEARCH_PATHS = (
    "business_intent.yaml",
    "business_intent.json",
    "docs/business_intent.yaml",
    ".pko/business_intent.yaml",
    "backend/business_intent.yaml",
)

# Поля, ради которых файл вообще существует.
REQUIRED_FIELDS = ("confirmed_need_id", "business_owner", "target_state", "success_criteria")

# Входы профилирования (§0.2.1) — закрытые перечни. Опечатка в них не должна
# молча превращаться в наименее строгую классификацию, поэтому неизвестное
# непустое значение делает intent непригодным, и решение Gate не выносится.
ENUM_FIELDS: dict[str, tuple[str, ...]] = {
    "maturity": ("idea", "pilot", "limited", "production", "auto"),
    "consequence": ("low", "medium", "high"),
    "requested_mode": ("HUMAN_ONLY", "ASSIST", "CONFIRM", "AUTO"),
    "scale": ("local", "limited", "mass"),
    "decision_boundary": ("END_TO_END_PROCESS", "COMPONENT_BBB"),
}


@dataclass
class IntentResult:
    data: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    missing: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def present(self) -> bool:
        return bool(self.data)

    @property
    def usable(self) -> bool:
        """Годен ли intent как вход Gate.

        Файл с опечаткой в перечислимом поле хуже отсутствующего: он выглядит
        заполненным, но задаёт неизвестный уровень риска. Такой вход к решению
        не допускается.
        """
        return self.present and not self.invalid and not self.error

    @property
    def complete(self) -> bool:
        """Годен ли intent для вынесения вердикта.

        Проверки Gate с последствием `DENY` спрашивают ровно эти поля. Если их нет,
        проверять клиентский результат не по чему: это отсутствие решения, а не
        отказ в допуске. Пустой шаблон с одними комментариями раньше считался
        заполненным файлом и давал `DENY`.
        """
        return self.usable and not self.missing

    def problem(self) -> str:
        """Короткая причина, по которой решение не выносится."""
        if self.error:
            return f"business_intent.yaml прочитан с ошибкой: {self.error}"
        if self.invalid:
            return (
                "В business_intent.yaml недопустимые значения полей профилирования: "
                + "; ".join(self.invalid)
            )
        if not self.present:
            return "business_intent.yaml не найден"
        if self.missing:
            return (
                "В business_intent.yaml не заполнены обязательные поля: "
                + ", ".join(self.missing)
            )
        return ""


def load_intent(tree, commit: str, override_path: str | Path | None = None) -> IntentResult:
    """Прочитать intent из репозитория или из файла, переданного флагом."""
    if override_path:
        # Путь приводится к абсолютному до того, как станет источником доказательства:
        # относительный `--intent config/business_intent.yaml` иначе выглядит как путь
        # внутри анализируемого коммита, не находится там и роняет проверку связности.
        p = Path(override_path).expanduser()
        if not p.exists():
            return IntentResult(error=f"Файл не найден: {p}")
        return _parse(p.read_text(encoding="utf-8"), str(p.resolve()), commit)

    for candidate in SEARCH_PATHS:
        text = tree.read(candidate)
        if text:
            return _parse(text, candidate, commit)
    return IntentResult()


def _parse(text: str, source: str, commit: str) -> IntentResult:
    notes: list[str] = []
    try:
        data = loads(text, notes)
    except YamlSubsetError as exc:
        return IntentResult(source=source, error=str(exc))
    if not isinstance(data, dict):
        return IntentResult(source=source, error="Ожидался набор полей «ключ: значение»")

    data = {k: v for k, v in data.items() if v not in (None, "", [])}
    data["__source__"] = source
    data["__commit__"] = commit
    missing = [f for f in REQUIRED_FIELDS if not data.get(f)]
    return IntentResult(
        data=data, source=source, missing=missing, invalid=_invalid_enums(data),
        warnings=notes,
    )


def _invalid_enums(data: dict[str, Any]) -> list[str]:
    """Проверить закрытые перечни. Пустое поле — не ошибка, неизвестное значение — ошибка."""
    problems: list[str] = []
    for field_name, allowed in ENUM_FIELDS.items():
        raw = data.get(field_name)
        if raw in (None, "", []):
            continue
        value = str(raw).strip()
        normalized = value.upper() if field_name in {"requested_mode", "decision_boundary"} \
            else value.lower().replace(" ", "_")
        if normalized not in allowed:
            problems.append(f"{field_name}={value!r} (допустимо: {', '.join(allowed)})")
    return problems

"""Профилирование по §0.2.1: BASIC или FULL, и есть ли блокер запуска.

Реализация буквальная: сначала зона матрицы 0.2 по значимости последствий и
зрелости жизненного цикла, затем независимые триггеры FULL. Правильность
проверяется тремя нормативными сценариями §Б.4 — они лежат в тестах и обязаны
совпадать при прямом и обратном проходе.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

BASIC = "BASIC"
FULL = "FULL"

ZONE_BASIC = "BASIC_REQUIREMENTS"
ZONE_FULL = "FULL_REQUIREMENTS"
ZONE_BLOCKER = "LAUNCH_BLOCKER"

MATURITY_EARLY = {"idea", "pilot", "идея", "пилот"}
MATURITY_LIMITED = {"limited", "limited_launch", "ограниченный", "ограниченный_запуск"}
MATURITY_INDUSTRIAL = {"production", "industrial", "auto", "промышленный", "промышленный_запуск"}

CONSEQUENCE_LOW = {"low", "низкая"}
CONSEQUENCE_MEDIUM = {"medium", "средняя"}
CONSEQUENCE_HIGH = {"high", "высокая"}

MODES = ("HUMAN_ONLY", "ASSIST", "CONFIRM", "AUTO")

# Уровни машинного соответствия §8.0. Уровень определяется отдельно от профиля
# и не может ослаблять его требования.
BASIC_RECORD = "BASIC_RECORD"
FULL_RESOURCE_MODEL = "FULL_RESOURCE_MODEL"

# Матрица 0.2: значимость последствий × зрелость жизненного цикла.
_MATRIX = {
    "low": (ZONE_BASIC, ZONE_BASIC, ZONE_FULL),
    "medium": (ZONE_BASIC, ZONE_FULL, ZONE_FULL),
    "high": (ZONE_FULL, ZONE_BLOCKER, ZONE_BLOCKER),
}


@dataclass
class Profile:
    value: str
    zone: str
    blocker: bool
    triggers: list[str] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)

    @property
    def required_machine_level(self) -> str:
        """Какой уровень машинного соответствия требует профиль (§8.0)."""
        return BASIC_RECORD if self.value == BASIC else FULL_RESOURCE_MODEL

    @property
    def achieved_machine_level(self) -> str:
        """Какой уровень PKO фактически выпускает.

        Всегда `BASIC_RECORD`: PKO собирает одну Gate Card и запись анализа, а
        ресурсной модели 8.1–8.14 с `ResourceRef`, конвертами, событиями и
        инвариантами связности у него нет. Раньше это поле называлось
        `machine_level` и для профиля FULL печатало `FULL_RESOURCE_MODEL` —
        отчёт заявлял уровень, которого не достиг, и читатель карточки видел
        подтверждение промышленного контура там, где его не существует.
        """
        return BASIC_RECORD

    @property
    def machine_level_satisfied(self) -> bool:
        return self.achieved_machine_level == self.required_machine_level

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.value,
            "zone": self.zone,
            "blocker": self.blocker,
            "triggers": self.triggers,
            "required_machine_level": self.required_machine_level,
            "achieved_machine_level": self.achieved_machine_level,
            "machine_level_satisfied": self.machine_level_satisfied,
            "inputs": self.inputs,
        }


def determine_profile(intent: dict[str, Any] | None) -> Profile:
    """Вычислить профиль по входам профилирования из business_intent.yaml."""
    data = intent or {}
    maturity = _norm(data.get("maturity"), "pilot")
    consequence = _norm(data.get("consequence"), "low")
    mode = str(data.get("requested_mode") or "ASSIST").upper()
    effects = _as_list(data.get("external_effects"))
    scale = _norm(data.get("scale"), "local")

    inputs = {
        "maturity": maturity,
        "consequence": consequence,
        "requested_mode": mode,
        "external_effects": effects,
        "scale": scale,
    }

    zone = _zone(consequence, maturity)
    triggers = _triggers(mode, effects, consequence, scale, data)
    unknown = unknown_inputs(inputs)
    if unknown:
        # Нераспознанный вход профилирования сам по себе является триггером FULL:
        # уровень риска не определён, а значит, не может считаться низким.
        triggers.append("нераспознанные входы профилирования: " + ", ".join(unknown))

    value = BASIC if (zone == ZONE_BASIC and not triggers) else FULL
    return Profile(
        value=value,
        zone=zone,
        blocker=(zone == ZONE_BLOCKER),
        triggers=triggers,
        inputs=inputs,
    )


def _zone(consequence: str, maturity: str) -> str:
    row = _MATRIX.get(_consequence_key(consequence), _MATRIX["high"])
    return row[_maturity_index(maturity)]


def _consequence_key(value: str) -> str:
    """Неизвестное значение трактуется как высокая значимость, а не как низкая.

    Опечатка вида `hgh` раньше давала «low» и переводила запуск с высокой
    значимостью в зону основных требований. Второй слой защиты после проверки
    перечней в `pko.intent.loader`: ошибаться нужно в сторону строгости.
    """
    if value in CONSEQUENCE_HIGH:
        return "high"
    if value in CONSEQUENCE_MEDIUM:
        return "medium"
    if value in CONSEQUENCE_LOW:
        return "low"
    return "high"


def _maturity_index(value: str) -> int:
    """Неизвестная зрелость трактуется как промышленный запуск — самая строгая колонка."""
    if value in MATURITY_INDUSTRIAL:
        return 2
    if value in MATURITY_LIMITED:
        return 1
    if value in MATURITY_EARLY:
        return 0
    return 2


def unknown_inputs(inputs: dict[str, Any]) -> list[str]:
    """Значения профилирования, которые PKO не смог распознать."""
    problems: list[str] = []
    known = {
        "maturity": MATURITY_EARLY | MATURITY_LIMITED | MATURITY_INDUSTRIAL,
        "consequence": CONSEQUENCE_LOW | CONSEQUENCE_MEDIUM | CONSEQUENCE_HIGH,
        "scale": {"local", "limited", "mass", "локальный", "ограниченный", "массовый",
                  "industrial", "промышленный"},
    }
    for name, allowed in known.items():
        value = inputs.get(name)
        if value and value not in allowed:
            problems.append(f"{name}={value!r}")
    mode = inputs.get("requested_mode")
    if mode and mode not in MODES:
        problems.append(f"requested_mode={mode!r}")
    return problems


def _triggers(
    mode: str, effects: list[str], consequence: str, scale: str, data: dict[str, Any]
) -> list[str]:
    """Триггеры FULL действуют независимо от исходной зоны матрицы."""
    out: list[str] = []
    significant = _significant_effects(effects)

    if mode == "AUTO":
        out.append("запрошен режим AUTO")
    if mode == "CONFIRM" and significant:
        out.append("режим CONFIRM при значимом внешнем эффекте")
    if significant:
        out.append("значимый внешний эффект: " + ", ".join(significant))
    if _consequence_key(consequence) == "high":
        out.append("высокая значимость последствий")
    if scale in {"mass", "массовый", "industrial", "промышленный"}:
        out.append("массовое или промышленное исполнение")
    if data.get("modifies_client_data"):
        out.append("изменение клиентских или защищаемых данных")
    if data.get("mandatory_sla"):
        out.append("обязательный SLA")
    return out


def _significant_effects(effects: list[str]) -> list[str]:
    significant_markers = (
        "financial", "финанс", "legal", "юрид", "status", "статус",
        "obligation", "обязательств", "client_data", "клиентск",
    )
    out: list[str] = []
    for e in effects:
        low = e.lower()
        if low in {"none", "нет", "no", "read_only", "read-only"}:
            continue
        if any(m in low for m in significant_markers):
            out.append(e)
    return out


def _as_list(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def _norm(value: Any, default: str) -> str:
    if value in (None, ""):
        return default
    return str(value).strip().lower().replace(" ", "_")

"""Чтение business_intent.yaml — единственного ручного входа PKO.

Стандарт (§4.0) запрещает выводить из кода потребность, целевое состояние,
границы автономности и владельца результата. Их подтверждает человек в этом
файле. Файла нет — PKO выпускает черновик: модель строится, но решение Gate не
принимается.

Файл ищется в анализируемом репозитории на конкретном коммите, чтобы
подтверждение было привязано к версии, а не к «текущему состоянию».

Форматов два — YAML и JSON, — и разбор выбирается по суффиксу источника, а не по
содержимому: угадывание формата по первому символу молча меняло бы смысл входа,
от которого зависит решение о допуске.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pko.errors import PkoError
from pko.util.sources import portable_source
from pko.util.yamlmini import YamlSubsetError, loads

SEARCH_PATHS = (
    "business_intent.yaml",
    "business_intent.json",
    "docs/business_intent.yaml",
    ".pko/business_intent.yaml",
    "backend/business_intent.yaml",
)

# Поля клиентского результата, ради которых файл вообще существует.
RESULT_REQUIRED_FIELDS = (
    "confirmed_need_id", "business_owner", "target_state", "success_criteria",
)

# Граница полномочий — не описание «для полноты карточки», а обязательный вход
# решения. Без неё ALLOW не имеет области применения и неизбежно читается шире,
# чем подтверждал владелец. `forbidden_effects` должен содержать явный перечень
# либо строку `none`: пустое/отсутствующее значение нельзя отличить от забытого.
AUTHORIZATION_REQUIRED_FIELDS = (
    "decision_boundary", "in_scope", "forbidden_effects",
)

REQUIRED_FIELDS = RESULT_REQUIRED_FIELDS + AUTHORIZATION_REQUIRED_FIELDS

# Поля §8.0.1, без которых запись допуска неполна. Отсутствие каждого — это
# конкретная задача владельцу, а не вклад в общий процент неизвестности:
# «границу решения не задали» починить можно, «34% полей не установлено» —
# нет. Большинство пустых полей здесь не мешает черновику, но три поля границы
# выше входят и в REQUIRED_FIELDS: без них решение не выносится.
RECORD_FIELDS: dict[str, str] = {
    "decision_boundary": "граница решения: весь процесс или отдельный компонент",
    "in_scope": "что входит в допуск",
    "out_of_scope": "что из допуска исключено",
    "forbidden_effects": "какие внешние эффекты запрещены",
    "external_effects": "какие внешние эффекты допускаются",
    "requested_mode": "запрошенный максимальный режим",
    "owner_confirmed_at": "когда владелец подтвердил намерение",
}

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
    record_gaps: list[str] = field(default_factory=list)
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

        Нужны и критерии клиентского результата, и явная граница полномочий.
        Если их нет, проверять результат или понимать область ALLOW не по чему:
        это отсутствие решения, а не отказ в допуске. Пустой шаблон с одними
        комментариями раньше считался заполненным файлом и давал `DENY`, а
        заполненные только четыре поля результата могли дать безграничный ALLOW.
        """
        return self.usable and not self.missing

    def problem(self) -> str:
        """Короткая причина, по которой решение не выносится.

        Файл называется своим именем: входов несколько (`.yaml` и `.json`,
        в репозитории и по `--intent`), и сообщение про «business_intent.yaml»
        отправляло владельца править не тот файл.
        """
        name = self.source or "business_intent.yaml"
        if self.error:
            return f"{name} прочитан с ошибкой: {self.error}"
        if self.invalid:
            return (
                f"В {name} недопустимые значения полей: "
                + "; ".join(self.invalid)
            )
        if not self.present:
            return "business_intent.yaml не найден"
        if self.missing:
            return (
                f"В {name} не заполнены обязательные поля: "
                + ", ".join(self.missing)
            )
        return ""


def external_source(path: Path, content: str) -> str:
    """Переносимый идентификатор внешнего источника с отпечатком содержимого.

    Один basename неоднозначен: два разных `business_intent.yaml` раньше
    выглядели одним доказательством. Короткий SHA-256 не раскрывает локальный
    путь и при этом позволяет отличить и сверить конкретный вход.
    """
    return portable_source(path, content)


def load_intent(tree, commit: str, override_path: str | Path | None = None) -> IntentResult:
    """Прочитать intent из репозитория или из файла, переданного флагом."""
    if override_path:
        # Читаем по реальному пути, а в доказательство выпускаем переносимое
        # имя: относительный `--intent config/business_intent.yaml` иначе
        # выглядел бы как путь внутри анализируемого коммита, не находился бы
        # там и ронял проверку связности.
        return _parse_override(Path(override_path).expanduser(), commit)

    for candidate in SEARCH_PATHS:
        text = tree.read(candidate)
        if text:
            return _parse(text, candidate, commit)
    return IntentResult()


def _parse_override(path: Path, commit: str) -> IntentResult:
    """Прочитать файл, названный флагом `--intent`.

    Отказ здесь — ошибка запуска, а не свойство намерения. Проверялось только
    существование, поэтому каталог или файл без прав на чтение доходили до
    `read_text` и роняли прогон трассировкой `IsADirectoryError`: CLI ловит
    `PkoError`, а не `OSError`.

    Возвращать вместо этого «черновик по коду» тоже нельзя. Оператор явно
    попросил учесть подтверждение владельца; отчёт с `NO_DECISION` и опечаткой
    в пути выглядит как вывод о системе, хотя это промах в аргументе команды.
    Поэтому здесь `PkoError` с подсказкой — она уходит на stderr, в отчёт не
    попадает, и абсолютный путь оператора не оседает в артефактах.
    """
    if not path.exists():
        raise PkoError(
            f"Файл намерения не найден: {path}",
            hint="проверьте путь в --intent или уберите флаг, чтобы взять "
                 "business_intent.yaml из самого репозитория",
        )
    if not path.is_file():
        raise PkoError(
            f"--intent указывает не на файл: {path}",
            hint="ожидается business_intent.yaml или .json, а не каталог",
        )
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PkoError(
            f"Файл намерения не читается как текст UTF-8: {path}",
            hint=f"{exc.reason} в позиции {exc.start}; ожидается YAML или JSON",
        ) from exc
    except OSError as exc:
        raise PkoError(
            f"Файл намерения не прочитан: {path}",
            hint=f"{exc.strerror or exc}: проверьте права доступа",
        ) from exc
    return _parse(text, external_source(path, text), commit)


def _load(text: str, source: str, notes: list[str]) -> Any:
    """Разобрать вход тем разбором, которого он требует.

    `business_intent.json` объявлен входом наравне с YAML, но разбирался
    YAML-подмножеством. Открывающая скобка JSON — не «ключ: значение», поэтому
    полностью заполненное намерение отвергалось целиком: все обязательные поля
    выглядели незаполненными, и Gate возвращал `NO_DECISION` при том, что
    владелец подтвердил всё, что от него требуется.
    """
    if source.lower().endswith(".json"):
        return json.loads(text)
    return loads(text, notes)


def _parse(text: str, source: str, commit: str) -> IntentResult:
    notes: list[str] = []
    try:
        data = _load(text, source, notes)
    except YamlSubsetError as exc:
        return IntentResult(source=source, error=str(exc))
    except json.JSONDecodeError as exc:
        # Формулировка та же по смыслу, что у YAML-разбора: читателю нужна
        # строка, на которой разбор остановился, а не имя исключения.
        return IntentResult(
            source=source,
            error=f"строка {exc.lineno}: JSON не разобран — {exc.msg}",
        )
    if not isinstance(data, dict):
        return IntentResult(source=source, error="Ожидался набор полей «ключ: значение»")

    data = {k: v for k, v in data.items() if v not in (None, "", [])}
    data["__source__"] = source
    data["__commit__"] = commit
    missing = [f for f in REQUIRED_FIELDS if not data.get(f)]
    return IntentResult(
        data=data, source=source, missing=missing,
        invalid=_invalid_enums(data) + _invalid_authorization(data),
        warnings=notes, record_gaps=record_gaps(data),
    )


def record_gaps(data: dict[str, Any]) -> list[str]:
    """Незаполненные поля записи допуска — списком задач, а не процентом."""
    return [
        f"{name}: {purpose}"
        for name, purpose in RECORD_FIELDS.items()
        if not data.get(name)
    ]


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


def _invalid_authorization(data: dict[str, Any]) -> list[str]:
    """Проверить форму открытых полей границы полномочий.

    Их словарь значений предметно-зависим, поэтому закрытого enum здесь нет.
    Но число, boolean или вложенный объект не образуют воспроизводимый scope.
    Пустой перечень тоже не является явной политикой: чтобы подтвердить
    отсутствие запретов, владелец пишет `forbidden_effects: none`.
    """
    problems: list[str] = []
    for field_name in ("in_scope", "forbidden_effects"):
        raw = data.get(field_name)
        if raw is None:
            continue
        if isinstance(raw, str):
            if raw.strip():
                continue
        elif isinstance(raw, list):
            if raw and all(isinstance(item, str) and item.strip() for item in raw):
                continue
        hint = "текст или непустой список строк"
        if field_name == "forbidden_effects":
            hint += "; если запретов нет — строка 'none'"
        problems.append(f"{field_name} (ожидается {hint})")
    return problems

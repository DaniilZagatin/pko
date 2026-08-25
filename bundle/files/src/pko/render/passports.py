"""Паспорта объектов управления — картотека и карточки.

Паспорт здесь — представление, а не документ (§4.0): он целиком собирается из
модели. Вид повторяет `passports_v1_1.html`: сначала картотека коротких
карточек, паспорт открывается по клику отдельной страницей.

Почему не одна длинная страница со всеми паспортами: на реальной цели она
занимала 99 КБ против 37 КБ у эталона при меньшем числе объектов. Читатель
искал нужный блок прокруткой сквозь чужие таблицы, доказательства и бейджи.
Картотека отвечает на первый вопрос — «что здесь вообще есть» — одной строкой
на объект, а подробности показывает тогда, когда их спросили.

Доказательства (`path:line` и основание) остаются в раскрытом паспорте: в
обзоре они заслоняли смысл, но убирать их из отчёта совсем нельзя — без них
утверждение нечем проверить.
"""

from __future__ import annotations

import json

from pko.model.schema import REFERENCE_LINKS, PkoModel, PkoObject
from pko.render.base import esc, field_html, gaps_section, meta_bar, page, tag

_CARD_CSS = """
.lead { font-size: 1rem; line-height: 1.7; }
.note {
  font-size: 0.95rem; line-height: 1.65; color: var(--text);
  background: var(--accent-light); border-left: 3px solid var(--accent);
  padding: 0.75rem 1rem; border-radius: 0 8px 8px 0; margin-bottom: 1.1rem;
}
"""

_GROUPS = (
    ("NEED", "Потребность"),
    ("JOURNEY", "Клиентские пути"),
    ("PROCESS", "Автономные процессы"),
    ("BBB", "BBB"),
    ("AO", "Атомарные операции"),
    ("GUARDRAIL", "Guardrails"),
)

_ICON_STYLE = {
    "NEED": "background:var(--accent-light);color:var(--accent);",
    "JOURNEY": "background:var(--green-light);color:var(--green);",
    "PROCESS": "background:var(--purple-light);color:var(--purple);",
    "BBB": "background:var(--amber-light);color:var(--amber);",
    "AO": "background:var(--cyan-light);color:var(--cyan);",
    "GUARDRAIL": "background:var(--pink-light);color:var(--pink);",
}

# Какие поля отвечают на вопрос «что это такое» для каждого типа объекта.
# Правило названо явно, а не «первое поле по порядку»: порядок полей — деталь
# сборки модели, и описание карточки не должно от неё зависеть.
#
# Полей несколько, потому что первое часто повторяет название объекта: у блока
# «Бизнес-смысл» и есть его имя, у операции «Проверяемый эффект» — тоже. Карточка,
# которая дважды говорит одно и то же, не сообщает читателю ничего, поэтому
# берётся первое поле, которое добавляет к названию что-то новое.
_DESCRIPTION_FIELDS = {
    "NEED": ("Бизнес-смысл", "Клиент", "Признаки распознавания"),
    "JOURNEY": ("Целевое состояние", "Критерии результата", "Бизнес-смысл"),
    "PROCESS": ("Правила сборки траектории", "Условия запуска"),
    "BBB": ("Бизнес-смысл", "Контракт входа", "Способы исполнения"),
    "AO": ("Проверяемый эффект", "Компоненты", "Количество мест вызова"),
    "GUARDRAIL": ("Защищаемый инвариант", "Значение", "Тип"),
}

# Что показать в правом нижнем углу карточки: одна короткая характеристика.
_META_FIELD = {
    "BBB": "Способы исполнения",
    "AO": "Механизм",
    "GUARDRAIL": "Severity",
}


def render_passports(
    model: PkoModel,
    notes: dict[str, str] | None = None,
    overview: str = "",
) -> str:
    notes = notes or {}
    cards: list[str] = []
    data: dict[str, dict[str, object]] = {}

    for kind, title in _GROUPS:
        objects = model.by_kind(kind)
        if not objects:
            continue
        cards.append(f'<div class="section-title">{esc(title)}</div><div class="grid">')
        for number, obj in enumerate(objects, start=1):
            cards.append(_card(obj, number, notes.get(obj.id, "")))
            data[obj.id] = _passport_data(obj, notes.get(obj.id, ""))
        cards.append("</div>")

    body = (
        meta_bar(model)
        + f'<div id="cardsView">{_overview_section(overview)}{"".join(cards)}'
        + gaps_section(model, "!")
        + "</div>"
        + _script(data)
    )

    meta = model.meta
    html_doc = page(
        title="Паспорта объектов управления",
        subtitle=f"{meta.get('repo', '')} · сгенерировано из реализации",
        badge=f"Стандарт v1.1 · версия {esc(meta.get('version_label', ''))} · "
              f"коммит {esc(str(meta.get('commit', ''))[:8])}",
        body=body,
        footer="Паспорт является представлением реализации: ручное ведение полей "
               "не предусмотрено. Значения без пометки найдены в коде.",
    )
    return html_doc.replace("</style>", _CARD_CSS + "</style>")


def _overview_section(text: str) -> str:
    """Обзор перед картотекой: читатель начинает с картины, а не с карточек."""
    if not text:
        return ""
    return f"""
  <div class="section">
    <div class="section-header">
      <div class="icon" style="background:var(--accent-light);color:var(--accent);">≡</div>
      <h2>О системе</h2>
    </div>
    <div class="section-body"><div class="lead">{esc(text)}</div></div>
  </div>
"""


def _card(obj: PkoObject, number: int, note: str) -> str:
    icon = f"{obj.kind[0]}{number}"
    meta = _card_meta(obj)
    meta_html = f'<div class="cmeta">{esc(meta)}</div>' if meta else ""
    return (
        f'<div class="card" onclick="showPassport(\'{esc(obj.id)}\')">'
        f'<div class="icon" style="{_ICON_STYLE.get(obj.kind, "")}">{esc(icon)}</div>'
        f'<div class="ctitle" title="{esc(obj.name)}">{esc(_short(obj.name, 48))}</div>'
        f'<span class="cid" title="{esc(obj.id)}">{esc(_short(obj.id, 28))}</span>'
        f'<div class="cdesc">{esc(_description(obj, note))}</div>'
        f"{meta_html}</div>"
    )


def _description(obj: PkoObject, note: str) -> str:
    """Одна строка о том, что это за объект.

    Сначала пояснение писателя: оно написано для человека и отвечает на вопрос
    «зачем это владельцу процесса». Если модель не запускали, берём первое
    назначенное поле, которое не повторяет название. Если ничего нового нет —
    говорим прямо, а не показываем служебную заглушку вроде «не восстановлен
    статически».
    """
    if note:
        return _short(note, 150)
    name = _normal(obj.name)
    for label in _DESCRIPTION_FIELDS.get(obj.kind, ()):
        field = obj.fields.get(label)
        if field is None or field.origin == "UNKNOWN" or not field.text():
            continue
        value = field.text()
        if _normal(value) == name or _normal(value) in name:
            continue
        prefix = "" if label in ("Бизнес-смысл", "Целевое состояние",
                                 "Защищаемый инвариант", "Проверяемый эффект") else f"{label}: "
        return _short(prefix + value, 150)
    return "описание из кода не восстановлено"


def _normal(text: str) -> str:
    """Сравнение по существу: регистр и пробелы значения не имеют."""
    return " ".join(str(text).split()).strip(" .").lower()


def _card_meta(obj: PkoObject) -> str:
    label = _META_FIELD.get(obj.kind, "")
    field = obj.fields.get(label) if label else None
    if field is not None and field.origin != "UNKNOWN" and field.text():
        # У Severity полная формулировка объясняет последствие («error —
        # отсутствие ограничивает режим до ASSIST»); в углу карточки нужна
        # только степень, объяснение читатель увидит в паспорте.
        return _short(field.text().split(" — ")[0], 46)
    links = [t for rel, targets in obj.links.items() if rel in REFERENCE_LINKS
             for t in targets]
    return _short(" · ".join(links), 46)


def _passport_data(obj: PkoObject, note: str) -> dict[str, object]:
    """Содержимое раскрытого паспорта: заголовок, пояснение и строки таблицы."""
    # Два доказательства на поле, а не три: третье почти всегда указывает на
    # тот же файл и добавляет вес, не добавляя проверяемости. Полный перечень
    # остаётся в `pko_<версия>.json` и `semantic_facts.json`.
    rows = [[label, field_html(fld, max_evidence=2)] for label, fld in obj.fields.items()]
    links = " ".join(
        tag(t, _kind_of_id(t))
        for rel, targets in obj.links.items()
        if rel in REFERENCE_LINKS
        for t in targets
    )
    if links:
        rows.append(["Связи", links])
    # Всё, что уйдёт в `innerHTML`, экранируется здесь: значения полей уже
    # прошли через `field_html`, а имя, идентификатор и пояснение — нет, и
    # угловая скобка в названии сломала бы разметку страницы.
    return {"id": esc(obj.id), "name": esc(obj.name), "kind": esc(obj.kind_title),
            "note": esc(note), "rows": rows}


def _script(data: dict[str, dict[str, object]]) -> str:
    """Данные паспортов и переключение вида.

    Значения сериализуются через `json.dumps`: в них есть кавычки, переводы
    строк и угловые скобки, и ручная сборка строки молча сломала бы страницу.
    Закрывающий тег внутри данных экранируется отдельно — иначе браузер решит,
    что скрипт кончился.
    """
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f"""
<div id="passportView" class="page"></div>
<script>
const passports = {payload};
function showPassport(id) {{
  const d = passports[id];
  if (!d) return;
  const rows = d.rows.map(r => '<tr><td class="pl">' + r[0] + '</td><td>' + r[1] + '</td></tr>').join('');
  const note = d.note ? '<div class="note">' + d.note + '</div>' : '';
  const view = document.getElementById('passportView');
  view.innerHTML =
    '<div class="page-bar"><a class="back" onclick="showCards()">← Назад к картотеке</a>'
    + '<span class="pbc">' + d.id + ' · ' + d.kind + '</span></div>'
    + '<div class="section"><div class="section-header"><h2>' + d.name + '</h2></div>'
    + '<div class="section-body">' + note + '<table class="ptable">' + rows + '</table></div></div>';
  document.getElementById('cardsView').classList.add('hidden');
  view.classList.add('active');
  window.scrollTo(0, 0);
}}
function showCards() {{
  document.getElementById('passportView').classList.remove('active');
  document.getElementById('cardsView').classList.remove('hidden');
  window.scrollTo(0, 0);
}}
</script>
"""


def _short(text: str, limit: int = 34) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _kind_of_id(obj_id: str) -> str:
    if obj_id.startswith("NEED"):
        return "NEED"
    if obj_id.startswith("CP-"):
        return "JOURNEY"
    if obj_id.startswith("AP-"):
        return "PROCESS"
    if obj_id.startswith("BBB"):
        return "BBB"
    if obj_id.startswith("AO"):
        return "AO"
    return "GUARDRAIL"

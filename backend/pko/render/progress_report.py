"""HTML-отчёт о прогрессе: план (PPTX) сверен с кодом целевого репозитория.

В том же стиле, что и остальные отчёты PKO (`render/base.py`: `page()`, `esc()`,
общая CSS) — один самодостаточный файл без внешних ресурсов. Домен другой
(вердикт по пункту плана, а не паспорт объекта управления), поэтому вёрстка
собственная, а не переиспользование `render/passports.py`/`render/taxonomy.py`.

Секция «Путь» — исключение из «всё генерирует Python»: её рисует собранный
React-бандл (`frontend-app/`, см. `_load_journey_bundle`) — там нужна точная
геометрия PowerPoint-пресета `chevron` и нормальный перенос текста браузером,
для чего строковый Python-рендер не годился. Бандл собирается один раз
(`npm run build`) и не коммитится — читается с диска при каждом вызове.

Проверка evidence (`verify_evidence`) и текста (`_guard_explanation`) в
`progress.matcher` по-прежнему решает, что вообще может попасть сюда —
неподтверждённая evidence в отчёт просто не проходит. Но сам факт «это было
проверено» здесь никак не показывается: ни бейджей «подтверждено/не
подтверждено», ни зачёркивания, ни причины отказа рядом со ссылкой. Читателю
показывается вердикт и то, чем он подкреплён, а не механика проверки.
"""

from __future__ import annotations

import json
from pathlib import Path

from pko.errors import PkoError
from pko.progress.schema import ItemVerdict, ProgressModel
from pko.render.base import page

# frontend-app/dist/index.html — собранный React-бандл секции «Путь».
# backend/pko/render/progress_report.py -> parents[3] == корень репозитория,
# frontend-app/ живёт рядом с ним по построению.
_JOURNEY_BUNDLE_PATH = Path(__file__).resolve().parents[3] / "frontend-app" / "dist" / "index.html"

STATUS_LABELS = {
    "DONE": ("Сделано", "green"),
    "PARTIAL": ("Частично", "amber"),
    "NOT_STARTED": ("Не начато", "red"),
    "UNCLEAR": ("Неясно", "purple"),
}

_EXTRA_CSS = """
:root {
  /* Акцент и рамки — под стиль слайда-образца ("Образ результата: клиентский
     путь"): фиолетовый вместо синего, тонкие лавандовые линии вместо серых.
     `--journey-panel` — цвет светлой подложки под шевронами, взят напрямую
     из XML презентации (F3E5FD), а не подобран на глаз. Переопределяем сам
     `--accent`/`--border`, а не заводим отдельные имена: так перекрашиваются
     разом бейдж, иконки секций и все рамки, использующие общий CSS `page()`
     из render/base.py, без правки самого base.py (он общий для всех
     отчётов PKO, не только progress). Цвета статуса (green/amber/red/
     purple у DONE/PARTIAL/NOT_STARTED/UNCLEAR) не трогаем — это осмысленная
     раскраска по смыслу, её меняли бы отдельно и осознанно. */
  --accent: #7c3aed;
  --accent-light: #f3e5fd;
  --border: #e4d6fa;
  --journey-panel: #f3e5fd;
}
"""
# Стили самой секции «Путь» (.journey-panel/.journey-row/.journey-chevron/…)
# больше не здесь — их несёт инлайн `<style>` из собранного React-бандла
# (см. `_load_journey_bundle`), эта страница только объявляет переменные
# (`--journey-panel`, `--accent` и т.д.), которые тот `<style>` использует.


def render_progress_report(model: ProgressModel) -> str:
    ratio = model.progress_ratio()
    title = "Отчёт о прогрессе"
    subtitle = f"{model.meta.get('repo', '—')} · план: {model.meta.get('plan_source', '—')}"
    badge = f"{ratio:.0%} пунктов плана сделано"

    # По просьбе пользователя отчёт показывает только дашборд «Путь» —
    # «Итог» (текст агента), «Возможно, сделано сверх плана» и «Пробелы и
    # ограничения анализа» здесь больше не рендерятся. Сами данные
    # (`model.summary`/`model.unclaimed`/`model.gaps`) никуда не делись —
    # они по-прежнему в JSON-модели (`progress_model.json` из CLI), просто
    # не выводятся в HTML.
    body = _journey_section(model)
    footer = (
        f"PKO progress · коммит {str(model.meta.get('commit', ''))[:8] or '—'} · "
        f"{model.meta.get('generated_at', '')}"
    )
    html = page(title=title, subtitle=subtitle, badge=badge, body=body, footer=footer)
    # count=1: только первый `</style>` — закрывающий тег общего CSS в <head>
    # из render/base.py::page(). Без ограничения replace() задевал бы и
    # `</style>` внутри бандла «Пути» (`_journey_section` кладёт свой
    # `<style>` прямо в body) — вставлял бы туда общий CSS ещё раз.
    return html.replace("</style>", _EXTRA_CSS + "</style>", 1)


def display_percent(verdict: ItemVerdict) -> int:
    """Процент заливки шеврона — не всегда сырое `verdict.progress`.

    `DONE`/`NOT_STARTED` показывают крайние значения независимо от того, что
    прислал агент (статус уже однозначен), а для `PARTIAL`/`UNCLEAR` без
    собственной оценки агента (`progress <= 0`) подставляется 50 — иначе
    «частично сделано» рисовалось бы пустым шевроном, что противоречит
    самому статусу. Презентационное решение, `ProgressModel`/`to_dict()`
    отдают сырое значение агента как есть.
    """
    if verdict.status == "DONE":
        return 100
    if verdict.status == "NOT_STARTED":
        return 0
    return verdict.progress if verdict.progress > 0 else 50


def _load_journey_bundle() -> tuple[str, str]:
    """Читает собранный React-бандл и возвращает `(<style>…</style>, <script>…</script>)`.

    Не кэшируется на уровне модуля: чтение ~150КБ с диска на один прогон
    отчёта не заметно рядом со стоимостью самих LLM-вызовов, а без кэша
    тесты и повторная сборка бандла подхватываются без танцев с инвалидацией.

    Ищем literal `</style>`/`</script>` — не regex по `<script...>`: минифицированный
    React-бандл сам содержит строку `"<script><\\/script>"` внутри кода (у него
    экранированный слэш), это ломает наивный поиск открывающих тегов, но не
    поиск закрывающего `</script>` без экранирования — он в файле ровно один.
    """
    if not _JOURNEY_BUNDLE_PATH.exists():
        raise PkoError(
            "Дашборд-путь не собран.",
            hint=f"выполните: cd frontend-app && npm install && npm run build "
                 f"(ожидается {_JOURNEY_BUNDLE_PATH})",
        )
    html = _JOURNEY_BUNDLE_PATH.read_text(encoding="utf-8")

    # `<style` (без `>`), не точное `<style>` — сборка вешает на тег атрибуты
    # (`rel="stylesheet" crossorigin`), не всегда голый `<style>`.
    style_start = html.index("<style")
    style_end = html.index("</style>", style_start) + len("</style>")
    style_tag = html[style_start:style_end]

    script_start = html.index('<script type="module"')
    script_end = html.index("</script>", script_start) + len("</script>")
    script_tag = html[script_start:script_end]

    return style_tag, script_tag


def _journey_section(model: ProgressModel) -> str:
    """Дашборд-путь: точную геометрию шеврона и раскладку рисует React-бандл
    (`frontend-app/`) — здесь только данные и точка монтирования.

    Порядок пунктов — порядок `model.verdicts` (в котором агент отправлял
    `submit_verdict`); другого сигнала об исходном порядке на слайде нет.
    """
    if not model.verdicts:
        return ""
    style_tag, script_tag = _load_journey_bundle()

    items = []
    for verdict in model.verdicts:
        item = model.items.get(verdict.item_id)
        title = item.title if item else verdict.item_id
        label, color = STATUS_LABELS[verdict.status]
        items.append({
            "title": title,
            "status": verdict.status,
            "label": label,
            "color": color,
            "pct": display_percent(verdict),
            "explanation": verdict.explanation,
        })
    items_json = json.dumps(items, ensure_ascii=False)

    # Без обёртки `.section`/`.section-header` с заголовком "Путь": сам
    # React-дашборд уже несёт полноценную шапку (вкладки, бейдж, заголовок) —
    # второй заголовок поверх был бы дублирующим.
    return f"""
  <div id="journey-root"></div>
  {style_tag}
  <script>window.__JOURNEY_ITEMS__ = {items_json};</script>
  {script_tag}
"""



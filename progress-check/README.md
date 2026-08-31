# Перенос: Progress Mode (версионирование и сравнение проверок продукта)

Инструкция для переноса этого изменения на вторую копию агента (кодовая база
отличается в деталях — по образцу `CHANGES_project_source.md`, только здесь
рядом с описанием лежат ещё и сами файлы: почти все новые, копировать как
есть). Реализовано по `PKO_progress_versioning_spec.md` (приложен
пользователем), номера параграфов ниже — оттуда.

## Зачем

Раньше повторная проверка того же продукта никак не была связана с
предыдущей: `pko serve` держит анализ только в памяти (`AnalysisJob`), ничего
не переживает перезапуск сервера. Теперь: каждая проверка, привязанная к
продукту, сохраняется как immutable snapshot; можно сравнить любые два
снимка во времени и увидеть осмысленную бизнес-разницу — что улучшилось,
что стоит на месте, что ухудшилось, новые/исчезнувшие этапы, актуальные
риски, следующий фокус.

**Ключевая находка**, определившая реализацию: `ProgressModel`/`PlanItem`/
`ItemVerdict` (`backend/pko/progress/schema.py`) уже почти один в один —
это и есть `AnalysisSnapshot`/`StageSnapshot` из спека (status/readiness/
business_comment/evidence уже были). Не хватало персистентности вообще,
сущности `Product`, стабильного `canonical_stage_id` и всего diff/сравнения.

**Важное архитектурное решение**: это веб-фича (`pko serve` +
`frontend-web`). CLI (`pko progress`, разовый файловый отчёт) не тронут —
у него нет и не будет понятия истории.

**Решения по UX**, согласованные с пользователем при разработке:
- продукт выбирается/создаётся **явно** пользователем в форме анализа, не
  матчится автоматически по URL репозитория (репозиторий переименовывают,
  у файлового источника его вообще нет);
- привязка к продукту **опциональна** — разовый анализ без сохранения в
  историю остался ровно таким, каким был.

## ⚠ На второй базе другая архитектура агента — адаптировать 2 места

На этой базе LLM вызывается через именованные «роли» (`pko.llm.registry`:
`matcher`/`reporter` — разные необязательно-разные endpoint/модель под
каждую задачу, см. `progress/matcher.py`/`progress/summarize.py`). На
второй базе агент работает иначе: единый цикл без разделения на роли, с
дополнительными инструментами (tools) поверх него. Прямо портировать
понятие «роль» некуда — но, к счастью, зависимость от него в новом коде
предельно узкая и вынесена всего в **два места**, остальной код
(SQLite-хранилище, детерминированный diff, алгоритм матчинга, весь
frontend) от этого не зависит вообще.

Оба места устроены одинаково — принимают опциональный `spec` и делают ровно
один **одноходовый** вызов «system-промпт + user-промпт → текст», без
диалога, без tool-calling, без цикла:

```python
# versioning/canonical.py::_match_via_llm — batched LLM-матчинг этапов
chat_client = client if client is not None else ChatClient(spec=spec)
raw = chat_client.complete(system=_LLM_SYSTEM, user=user, max_tokens=1000)

# versioning/interpret.py::interpret_comparison — бизнес-интерпретация
chat_client = client if client is not None else ChatClient(spec=spec)
text = chat_client.complete(system=_SYSTEM, user=user, max_tokens=1500)
```

`assign_canonical_ids(product_id, model, spec, client=None, db_path=None)` и
`interpret_comparison(comparison, from_model, to_model, spec, client=None)`
— оба принимают `spec`/`client` именно параметром функции, не читают
глобальный реестр ролей сами. Поэтому портировать — не «завести роль
`matcher`/`reporter` в чужой системе ролей», а просто:

1. Заменить `ChatClient(spec=spec).complete(system=..., user=...)` в этих
   двух местах на то, чем на второй базе делается одноходовый вызов модели
   с системным и пользовательским текстом (или прогнать этот же промпт
   через дополнительный tool цикла, если так удобнее в их архитектуре —
   агенту не обязательно знать, что это «роль», важен только контракт
   входа/выхода ниже).
2. Единой LLM-конфигурации агента достаточно на оба места сразу — здесь
   исторически два разных endpoint'а (`matcher`/`reporter`), но ничего в
   логике не требует именно двух: можно передавать один и тот же `spec`/
   коннектор в оба вызова.
3. Контракт входа/выхода менять нельзя — это то, что реально проверяется
   тестами (`test_versioning_canonical.py`, `test_versioning_interpret.py`):
   - матчинг этапов — JSON-массив `{old_stage_id, new_stage_id, same_stage,
     confidence}` (план версионирования, §5);
   - интерпретация — один JSON-объект `{progress_summary,
     stage_business_deltas, risks, next_focus}` (см. `_SYSTEM` в
     `interpret.py` — точная форма продиктована промптом).
4. Оба вызова **необязательны** — `spec=None` пропускает LLM-шаг полностью
   (матчинг — как первый snapshot продукта, всё новые этапы; интерпретация —
   пустой результат с причиной в `notes`). Если на второй базе решат вообще
   не подключать LLM к этим двум местам сразу при переносе — код не
   сломается, просто матчинг ограничится точным/fuzzy-совпадением текста, а
   сравнение не получит бизнес-прозы поверх голых фактов.

## Структура этого пакета

```
progress-check/
├── README.md          — этот файл
├── new/                — файлы, которых на второй базе ещё нет: копировать
│                         как есть по тем же относительным путям
└── modified/           — итоговое содержимое файлов, которые НА ЭТОЙ базе
                          уже существовали и были изменены; вторая база могла
                          разойтись с ними по другим причинам — не копировать
                          вслепую поверх, а перенести изменения вручную по
                          описанию ниже (или взять этот файл целиком, если на
                          второй базе он не менялся ничем, кроме как этим же
                          изменением в прошлый раз)
```

## Backend

Новые зависимости не нужны: `sqlite3` — стандартная библиотека, тот же
принцип простоты, что и у остального PKO (ни ORM, ни отдельного сервиса БД).

### 1. `backend/pko/store/` — новый пакет, персистентность (copy as-is)

- `db.py` — путь к файлу БД: `PKO_DATA_DIR` env (по умолчанию `~/.pko`,
  рядом с `~/.pko/llm-cache`), файл `store.db`. `connect()` — новое
  соединение SQLite на каждое обращение (WAL + `busy_timeout`, без
  `check_same_thread=False`/держащегося процесса соединения), схема
  создаётся идемпотентно (`CREATE TABLE IF NOT EXISTS`) при каждом
  подключении.
  **Важно при запуске `pko serve` в контейнере (Podman/Docker)**:
  `PKO_DATA_DIR` обязательно должен указывать на смонтированный volume —
  домашний каталог внутри контейнера не персистентен между пересозданиями
  контейнера, история продукта иначе пропадёт.
- `products.py` — `Product`/`ProductSummary`, `create_product`,
  `get_product`, `list_products` (новые сверху, со сводкой по последнему
  snapshot). **Грабля, на которую наступили и исправили**: `with
  connect(db_path) as conn:` НЕ закрывает соединение SQLite (`__exit__`
  только коммитит/откатывает транзакцию) — на Windows это на тестах
  проявилось как `PermissionError` при удалении временного файла БД
  (файл оставался залоченным). Везде используется `with
  closing(connect(db_path)) as conn:` (`from contextlib import closing`).
- `snapshots.py` — `Snapshot`, `save_snapshot` (версия = `MAX+1` под
  `threading.Lock()` на продукт — два фоновых анализа одного продукта
  могут завершиться одновременно), `list_snapshots`, `get_snapshot`,
  `get_latest_snapshot`. Хранит `ProgressModel.to_json()` целиком —
  для этого модели понадобился `from_dict`/`from_json` (см. изменения в
  `progress/schema.py` ниже, их раньше не было — только `to_dict`).
- `canonical.py` — реестр canonical stage продукта (`CanonicalStage`:
  `id`, `canonical_name`, `aliases[]`), независимый от отдельных
  snapshot'ов — копит все формулировки этапа за всю историю продукта, не
  только за последний прогон (переименование «туда и обратно» через
  несколько проверок тоже матчится).
- `comparisons.py` — кэш сравнения по тройке
  (`product_id`, `from_snapshot_id`, `to_snapshot_id`): `facts_json`
  (детерминированный diff) сразу, `interpretation_json` (LLM-смысл) —
  только после успешного ответа reporter'а, `NULL` до этого (план, §32).

Схема (создаётся `db.py::connect()`):

```sql
products(id, name, created_at)
canonical_stages(id, product_id, canonical_name, aliases_json, created_at)
snapshots(id, product_id, version_number, created_at, overall_readiness,
          source_json, model_json, UNIQUE(product_id, version_number))
comparisons(product_id, from_snapshot_id, to_snapshot_id, facts_json,
            interpretation_json, PRIMARY KEY(product_id, from_snapshot_id, to_snapshot_id))
```

### 2. `backend/pko/progress/schema.py` — MODIFIED (см. `modified/`)

- `ItemVerdict` — новое поле `canonical_stage_id: str = ""` (в конец, после
  `progress`) + попадает в `to_dict()`. Безопасно для существующих вызовов
  дата-класса (значение по умолчанию), но проверьте на второй базе — если
  там где-то есть точное сравнение `verdict.to_dict() == {...}` без учёта
  новых ключей, тест придётся поправить.
- Добавлены `from_dict`/`from_json` для `EvidenceRef`, `ItemVerdict`,
  `UnclaimedGroup`, `ProgressModel` (раньше был только `to_dict`/`to_json` —
  моделью никто не пользовался как входом, только как выходом). Нужны,
  чтобы поднять сохранённый snapshot обратно в объект
  (`store/snapshots.py::get_snapshot`).

### 3. `backend/pko/versioning/` — новый пакет, сравнение снимков (copy as-is)

Не путать с `pko.progress` — тот делает один независимый прогон, этот
сравнивает уже готовые прогоны друг с другом.

- `canonical.py::assign_canonical_ids(product_id, model, spec, client=None,
  db_path=None)` — проставляет `verdict.canonical_stage_id`, мутирует
  `model.verdicts` на месте. Три уровня матчинга (план, §5):
  1. точное совпадение нормализованного текста (title+stage+description,
     lowercase, без пунктуации) среди алиасов реестра продукта;
  2. `difflib.SequenceMatcher` (stdlib, без новой зависимости) с порогом
     `FUZZY_THRESHOLD = 0.82`;
  3. остаток — **один** batched LLM-вызов на весь снимок (не по одному на
     этап), роль `matcher` (уже обязательная для самого пайплайна — под
     это отдельную роль в `llm/registry.py` заводить не стали), контракт
     ответа ровно как в плане §5: `{old_stage_id, new_stage_id, same_stage,
     confidence}`, принимается только `confidence >= 0.7`.
  Первый snapshot продукта — реестр пуст, все item'ы становятся новыми
  canonical stage без единого LLM-вызова.
  **Критично (план, §6)**: вызывается ПОСЛЕ того, как `run_progress()` уже
  вернул независимый `ProgressModel` — агент, оценивающий текущую версию,
  никогда не видит предыдущий snapshot.
  `save_snapshot_with_matching(product_id, model, source, spec, client=None,
  db_path=None)` — то, чем пользуется `web/analyses.py` вместо голого
  `store.snapshots.save_snapshot`: матчинг + сохранение одним вызовом.
- `diff.py` — чистые функции, без сети/LLM. `STATUS_ORDER = {NOT_STARTED:0,
  UNCLEAR:1, PARTIAL:2, DONE:3}`. `compute_comparison(from_model, to_model)
  -> VersionComparison` со списком `StageDelta` (`IMPROVED`/`UNCHANGED`/
  `REGRESSED`/`ADDED`/`REMOVED`). **`SCOPE_CHANGED` из спека НЕ
  реализован** — сознательно, это единственный change_type, требующий
  семантического суждения, а не арифметики по статусам; сам план относит
  его к «после MVP» (§39). Процент в шевроне переиспользует уже
  существующий `render/progress_report.py::display_percent` — не
  дублирует правило DONE→100/NOT_STARTED→0/остальное→progress-or-50.
  `VersionComparison`/`StageDelta` несут `to_dict()`/`from_dict()` — вторые
  нужны, чтобы поднять закэшированные факты сравнения обратно в объект.
- `interpret.py::interpret_comparison(comparison, from_model, to_model,
  spec, client=None) -> InterpretedComparison` — бизнес-смысл поверх уже
  готовых фактов, роль `reporter` (уже существующая, необязательная — тот
  же принцип graceful degradation, что у `progress/summarize.py`: без
  роли/при сбое LLM — пустой результат с причиной в `notes`, не шаблон).
  Один запрос на всё сравнение сразу. Текст проверяется тем же
  `report.guard.check_text`, что и остальной PKO (выдуманный путь к файлу
  отклоняет весь вывод целиком).
  **MVP-упрощение, сознательное**: риски — не персистентная сущность со
  своим id через версии, а суждение LLM по дайджесту двух снимков зараз
  (`NEW`/`PERSISTING`/`RESOLVED` — ярлык на один запрос, не история одного
  и того же риска). Полноценный трекинг риска — «после MVP» (план и сам
  относит устранённые риски туда же, §39).

### 4. `backend/pko/web/products.py` — новый файл (copy as-is)

Тонкий слой над `pko.store` для `web/app.py`, тем же разделением, что и
`web/analyses.py` (роут в `app.py`, логика здесь): `create_product`,
`list_products`, `get_product`, `get_product_or_404`, `list_snapshots`,
`get_snapshot_dashboard` (тот же JSON-контракт, что и готовый анализ —
`dashboard_json`, просто из сохранённого snapshot), `compare_snapshots`
(факты + интерпретация, оба слоя кэшируются раздельно, см. `_merge`).

### 5. `backend/pko/web/analyses.py` — MODIFIED (см. `modified/`)

- `_dashboard_json` переименована в `dashboard_json` (публичная — теперь
  используется и из `web/products.py`).
- `create_analysis(...)` — новый необязательный параметр `product_id: str =
  ""`; если задан — проверяется, что продукт существует, ДО создания задачи
  (падает сразу в ответе на POST, не молча в фоновом потоке).
- `_execute(...)` — новый параметр `product_id`; после `run_progress()`,
  если `product_id` задан: считает `source` (метаданные материалов —
  `{"repo": {...}}` для git и/или `{"files": [{"filename","size","sha256"}]}`
  для загруженных файлов, хэш sha256 считается по уже читаемым `uploads`
  до их удаления вместе с `tmp_dir`), вызывает
  `versioning.canonical.save_snapshot_with_matching(...)`, добавляет
  `product_id`/`snapshot_id`/`version_number` в `job.result`.

### 6. `backend/pko/web/app.py` — MODIFIED (см. `modified/`)

- `POST /api/analyses` — новое поле формы `product_id: str = Form("")`.
- Новые роуты: `POST /api/products`, `GET /api/products`,
  `GET /api/products/{id}`, `GET /api/products/{id}/snapshots`,
  `GET /api/products/{id}/snapshots/{snapshot_id}`,
  `GET /api/products/{id}/compare?from=&to=` (алиас `from_`→`from` через
  `fastapi.Query(..., alias="from")` — `from` зарезервировано в Python).
  `reporter = get_spec("reporter")` для compare — необязателен, как и у
  `create_analysis`.

## Frontend (`frontend-web/`)

### Новые файлы (copy as-is)

- `lib/status.ts` — зеркало backend `STATUS_LABELS`: цвет/подпись статуса
  по строке (нужно для `StageDelta`, у которого, в отличие от `StageItem`,
  нет готового `color`/`label` с backend).
- `hooks/useProduct.ts`, `useProductSnapshots.ts`, `useSnapshotDashboard.ts`,
  `useCompare.ts`, `useProducts.ts` — тонкие обёртки `@tanstack/react-query`
  над новыми функциями `lib/api.ts` (тот же паттерн, что уже был у
  `hooks/useAnalysis.ts`).
- `components/upload/ProductPicker.tsx` — выбор продукта в форме анализа:
  `<select>` (без сторонней UI-библиотеки — в проекте на момент переноса
  не было готового Select-компонента) с опциями «Без сохранения в
  истории» / существующие продукты / «+ Новый продукт…» (раскрывает поле
  названия).
- `components/product/ProductView.tsx` — хаб продукта: переключатель
  «Текущее состояние» / «Прогресс» (план, §11). «Текущее состояние»
  переиспользует существующий `Dashboard` на данных последнего snapshot.
- `components/progress/*` — весь экран «Прогресс»:
  - `ReadinessDeltaHeader.tsx` — FROM/TO % + `+N п.п.` + счётчики (§12);
  - `VersionSlider.tsx` — dual-range snap-to-version (§15/16): два
    наложенных `<input type="range">`, CSS в `globals.css`
    (`.version-slider-input`) делает трек прозрачным для кликов, интерактивен
    только бегунок — иначе верхний по DOM-порядку input перехватывал бы
    весь трек. Плюс шорткаты «С предыдущей»/«За месяц»/«С первой» (§18);
  - `ProgressChevron.tsx` — оборачивает существующий `Chevron.tsx`, цвет =
    статус в TO, бейдж ↑/→/↓/+/− под ним (§20/21). **Грабля, на которую
    наступили и исправили**: НЕ оборачивайте `<Chevron>` в `flex flex-col`
    контейнер — у `.journey-chevron` (globals.css) зашит `flex: 1 1 0`,
    рассчитанный на прямого родителя-flex-row (`.journey-row`); второй
    flex-контейнер-колонка вокруг него схлопывает SVG до нулевой высоты
    (flex-basis: 0 по главной оси колонки при auto-высоте родителя). Подпись
    под шевроном — обычный блочный `<div>`, не флекс-сосед по общему
    контейнеру с самим `<Chevron>`;
  - `StageDiffPanel.tsx` — клик по этапу в Progress Mode открывает «Что
    изменилось» (`business_delta`), не описание этапа (§22/23);
  - `ProgressSummaryPanel.tsx` — три нижних блока: Основной прогресс /
    Актуальные риски (без `RESOLVED` — те отдельным блоком «Что удалось
    закрыть» ниже) / Следующий фокус (§24-27, §29);
  - `ProgressDashboard.tsx` — владеет `fromIndex`/`toIndex`/
    `selectedStageId`. Пустое состояние на <2 snapshots — текст ровно из
    плана §33. Сброс индексов на новую пару при появлении нового snapshot —
    **во время рендера** (сравнение `snapshots.length` с сохранённым в
    state), не в `useEffect` — так требует `eslint-plugin-react-hooks`
    (`react-hooks/set-state-in-effect`) в этой версии React/Next; `useCompare`
    вызывается безусловно до всех `return` (`react-hooks/rules-of-hooks`).
- `app/products/[id]/page.tsx` — роут хаба продукта, по образцу уже
  существующего `app/analysis/[id]/page.tsx`.

### Изменённые файлы (см. `modified/`)

- `lib/types.ts` — `AnalysisResult` дополнен необязательными
  `product_id`/`snapshot_id`/`version_number`; новые типы `Product`,
  `ProductSelection`, `SnapshotSummary`, `SnapshotDashboard`, `ChangeType`,
  `StageDelta`, `RiskItem`, `VersionComparison`.
- `lib/api.ts` — `createAnalysis(...)` получил параметр `productId = ""`
  (отправляется как `product_id` в `FormData`); новые функции
  `listProducts`, `createProduct`, `getProduct`, `getProductSnapshots`,
  `getSnapshotDashboard`, `compareSnapshots`.
- `components/upload/ProjectUploadForm.tsx` — состояние
  `productSelection: ProductSelection`, секция «Продукт (необязательно)» с
  `<ProductPicker>`; при отправке — если выбран «новый продукт», сперва
  `createProduct(name)`, затем `productId` уходит в `createAnalysis`.
- `components/analysis/AnalysisView.tsx` — как только анализ READY и в
  ответе есть `product_id` — `router.replace('/products/'+product_id)`
  вместо показа разового dashboard на этой же странице (разовый анализ без
  продукта, `product_id` отсутствует — поведение не меняется).
- `app/globals.css` — добавлен блок `.version-slider-input` в конец файла
  (после существующего `@media (max-width: 768px)`).

## Тесты

Backend (`tests/`, все — `unittest`, `PYTHONPATH=backend` или
`pip install -e .`; **нужен Python ≥3.11** — `tomllib` в
`extractors/deps.py` не существует до 3.11, если системный `python3`
резолвится в более старую версию, используйте явно `py -3.11`/`py -3.13`
или что стоит на машине):

- Новые файлы (copy as-is): `test_store_products.py`,
  `test_store_snapshots.py`, `test_web_products.py`,
  `test_versioning_diff.py`, `test_versioning_canonical.py`,
  `test_versioning_interpret.py`, `test_web_products_compare.py`.
- `test_web_analyses.py` (см. `modified/`) — добавлены `PKO_DATA_DIR` в
  `setUp` (временный каталог, не реальный `~/.pko`), параметр `product_id`
  в хелпер `_create`, кейсы
  `test_analysis_with_product_id_is_persisted_as_snapshot` и
  `test_analysis_with_unknown_product_id_is_rejected_before_job_creation`.

Frontend: `npm run build && npm run lint && npm test` в `frontend-web/` —
новых тест-файлов на этой итерации не добавлено (юнит-тест самого
слайдера — в проекте post-MVP, см. ниже), но существующий
`chevron-geometry.test.ts` должен остаться зелёным.

## Порядок переноса

1. Скопировать всё из `new/` по тем же относительным путям.
2. Для каждого файла из `modified/` — открыть версию на второй базе рядом
   с версией здесь, перенести изменения по описанию выше (не перезаписывать
   вслепую — вторая база могла разойтись независимо).
3. Backend: `PYTHONPATH=backend python -m unittest discover -s tests`
   (или `pip install -e .` и `python -m unittest discover -s tests`) —
   ожидается 228 тестов, все зелёные (плюс что уже было на второй базе,
   если там есть тесты сверх этого списка).
4. Frontend: `cd frontend-web && npm run build && npm run lint && npm test`.
5. Ручная проверка: `pko serve` + `frontend-web` рядом (см. README проекта).
   Создать продукт → прогнать один и тот же fixture дважды с разными
   вердиктами по одному этапу → на странице продукта переключиться на
   «Прогресс» → проверить: дельта readiness в шапке, стрелки на шевронах,
   снап слайдера по шорткатам, раскрытие «Что изменилось» по клику на этап,
   нижние три блока.

## Явно вне MVP (план версионирования, §39)

`SCOPE_CHANGED`, трекинг риска как персистентной сущности с историей
(`NEW`/`PERSISTING`/`RESOLVED` сейчас — суждение LLM за один вызов, не
история одного риска), блок «Без прогресса N проверок» (stagnation
detection), график readiness во времени, масштаб таймлайна
(3мес/6мес/год/всё время), уведомления о регрессии.

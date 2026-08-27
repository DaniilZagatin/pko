# Материалы проекта помимо Git: репозиторий + файлы как независимые источники evidence

Этот файл — инструкция для повторения одного конкретного изменения на другой
кодовой базе PKO (та же архитектура `backend/pko/progress/*` и
`backend/pko/web/*`, отличается только разбор плана — `pptx_reader.py` или
нейромодельный markdown-парсинг вместо него; это изменение их не касается
вообще, план на входе `run_progress` как был текстом слайдов, так и остался).

## Зачем

Раньше единственным источником evidence был Git-репозиторий
(`open_repo_source`/`load_target`). Решено: пользователь должен уметь
подтвердить готовность пункта плана и файлами, которых нет в репозитории
(результат эксперимента — `metrics.json`, `results.csv`, отчёт), и вообще
без репозитория. **Репозиторий и файлы — два независимых необязательных
поля формы, не выбор одного варианта из трёх**: можно и то, и другое сразу
(тогда они объединяются в один анализ), можно только одно, можно ни одного —
пустой снимок материалов не отклоняется формой/бэкендом как ошибка: агент
получает его и сам решает, что писать в вердикт по каждому пункту (тот же
путь, что и "в репозитории ничего не нашлось", просто с самого начала пусто).
Специального сообщения на этот случай нигде не подставлено намеренно.

## Ключевая находка, определившая реализацию

`ToolBox` (`progress/agent_tools.py`) и весь `extract_all`
(`extractors/runner.py`) трогают только `tree.files: list[str]` и
`tree.read(path) -> str | None` — ни разу не обращаются к `Tree.repo`/
`Tree.sha` напрямую. Значит, агенту и детерминированным экстракторам **уже
всё равно**, git это или нет — не нужен отдельный параллельный
`Workspace`/`ProjectSource`-слой с переименованием инструментов в
`workspace.*`, достаточно ОДНОГО нового класса с тем же контрактом чтения.
Ни `ToolBox`, ни `matcher.py`, ни `pipeline.py` в итоге не потребовали
изменений по существу — только сборка «снимка» (`TargetRepo`), который до
этого мог родиться только из git.

## Backend

### 1. `backend/pko/extractors/base.py` — добавить, ничего не менять

Явный протокол, документирующий то, чем `ToolBox`/`extract_all` уже
пользуются:

```python
from typing import Protocol, runtime_checkable
# ...

@runtime_checkable
class FileTree(Protocol):
    files: list[str]
    def read(self, path: str) -> str | None: ...
```

`Tree` (существующий класс в этом же файле) уже структурно ему
соответствует — тело `Tree` не трогается.

### 2. Новый файл `backend/pko/progress/local_source.py`

Целиком новый модуль. Полное содержимое (готово к переносу как есть, если
`extractors.runner.extract_all`/`progress.target_repo.TargetRepo`/
`pko.errors.PkoError` называются так же — на второй кодовой базе называются):

- `LocalTree` — dataclass с `root: Path`, `files: list[str]`; `read(path)`
  читает файл с диска (`encoding="utf-8", errors="replace"`, лимит 2МБ на
  файл — тот же порядок, что у `GitRepo.read_text`'s `max_bytes`); `match(...)`
  — тело один в один как у `Tree.match`.
- `CombinedTree` — dataclass с `primary: FileTree` (git-дерево или другой
  `LocalTree`) и `overlay: LocalTree`; `files` — объединение множеств,
  `overlay` побеждает при совпадении пути; `read(path)` — сначала `overlay`,
  потом `primary`. Используется, когда заполнены и репозиторий, и файлы.
- `_safe_relative_path(name) -> str | None` — нормализует `\`→`/`,
  отклоняет абсолютные пути, диски (`C:...`) и любой `..`-сегмент **после**
  нормализации (не просто подстроку `..` — это обходится вариантами вроде
  `a/../../b`). Возвращает `None`, если путь небезопасен.
- `_Budget` — счётчик файлов/байт с константами `MAX_WORKSPACE_FILES=5000`,
  `MAX_WORKSPACE_BYTES=200_000_000`; превышение — `PkoError`. Без этого
  ZIP-бомба (маленький архив, огромное распакованное содержимое) исчерпает
  диск раньше, чем агент успеет что-то прочитать — git от природы такого
  риска не несёт (объекты читаются по одному), поэтому лимита раньше и не
  было нужно.
- `_extract_uploads(uploads: list[tuple[str, bytes]], dest, budget) -> list[str]`
  — если имя файла оканчивается на `.zip` (регистронезависимо) — распаковать
  через `zipfile.ZipFile`, каждый элемент через `_safe_relative_path`
  (небезопасный путь → `PkoError` на весь архив, не тихий пропуск одного
  файла); иначе — записать как обычный файл, тоже через
  `_safe_relative_path` на само имя.
- `build_target_repo_from_uploads(uploads, dest) -> TargetRepo` — только
  файлы, без репозитория: `TargetRepo(repo=None, sha="", branch="",
  tree=LocalTree(...), extraction=extract_all(local_tree))`. Пустой список —
  `PkoError` ("Не выбрано ни одного файла.") — это осознанный выбор именно
  файлового источника без единого файла, отдельный случай от полностью
  пустого запроса ниже.
- `build_empty_workspace(dest) -> TargetRepo` — ни репозиторий, ни файлы не
  предоставлены вообще. Не ошибка: `TargetRepo(repo=None, sha="", branch="",
  tree=LocalTree(root=dest, files=[]), extraction=extract_all(...))` — агент
  получает пустой снимок и сам решает, что писать в вердикт (через уже
  существующую инструкцию `_AGENT_SYSTEM`: "если подтверждения не нашёл —
  NOT_STARTED с пустым evidence"). Никакой специальной фразы сюда не
  подставлено — сознательно, по просьбе пользователя.
- `merge_with_uploads(target: TargetRepo, uploads, dest) -> TargetRepo` —
  репозиторий + файлы поверх: если `uploads` пуст — вернуть `target` как
  есть (`is`, без копии); иначе — `CombinedTree(primary=target.tree,
  overlay=LocalTree(...))`, `extract_all` **пересчитывается на объединённом
  дереве** (экстракторы должны видеть и то, и другое сразу — иначе
  `find_unclaimed_paths`/агентные инструменты видели бы только часть
  материалов в зависимости от того, кто спрашивает), `repo`/`sha`/`branch`
  от `target` сохраняются как есть — это дополнение источника, не замена.

### 3. `backend/pko/progress/target_repo.py` — только типы, не логика

```python
# было
from pko.extractors.base import Tree
# ...
@dataclass(frozen=True)
class TargetRepo:
    repo: GitRepo
    sha: str
    branch: str
    tree: Tree
    extraction: Extraction

# стало
from pko.extractors.base import FileTree, Tree
# ...
@dataclass(frozen=True)
class TargetRepo:
    repo: GitRepo | None       # None — если источник не git
    sha: str                   # "" — если источник не git
    branch: str                # "" — если источник не git
    tree: FileTree             # Tree ИЛИ LocalTree/CombinedTree
    extraction: Extraction
```

`Tree` остаётся импортированным — `load_target()` в этом же файле всё ещё
строит его через `Tree.at(repo, sha)`, только тип поля стал шире.

Пустые `sha`/`branch` — не заглушка ради заглушки: `run_progress`
(`pipeline.py`) кладёт их только в `meta["commit"]`/`meta["branch"]`
итоговой модели, а там пустое значение уже отображается как `—`/пусто и в
футере CLI-отчёта, и в dashboard (если там есть поле commit/branch —
проверить на второй кодовой базе, у нас оно уже было убрано отдельной
правкой раньше).

### 4. `backend/pko/web/analyses.py`

- `create_analysis(...)` — новый параметр `uploads: list[tuple[str, bytes]]`
  между `branch` и `spec`. Валидация репозитория/файлов **убрана целиком**
  (было `if not repository.strip(): raise PkoError("Не указан репозиторий.",
  ...)` — сначала расширялось до "хотя бы одно из двух", затем убрано
  полностью по решению пользователя: пустой запрос — валидный, не ошибка).
  Единственная оставшаяся проверка в этой функции — расширение файла плана
  (`.pptx`).
- Новая функция `_build_target(repository, branch, uploads, workspace_dir,
  emit) -> tuple[TargetRepo, str]`:
  ```python
  def _build_target(repository, branch, uploads, workspace_dir, emit):
      target = None
      name = ""
      if repository:
          emit("phase", {"phase": "materials_loading", "label": "Подключаем репозиторий"})
          git_repo, name = open_repo_source(repository, branch=branch)
          target = load_target(git_repo, branch)
      else:
          emit("phase", {"phase": "materials_loading", "label": "Обрабатываем загруженные файлы"})
      if uploads:
          target = (merge_with_uploads(target, uploads, workspace_dir) if target is not None
                     else build_target_repo_from_uploads(uploads, workspace_dir))
      elif target is None:
          # ни репозитория, ни файлов — не ошибка, см. build_empty_workspace выше
          target = build_empty_workspace(workspace_dir)
      emit("phase", {"phase": "materials_ready", "label": "Материалы проекта готовы"})
      return target, name
  ```
- `_execute(...)` — новый параметр `uploads`, вызывает `_build_target(...)`
  вместо прежних прямых `open_repo_source`+`load_target`+двух `emit("phase",
  {"phase": "repository_cloning"/"repository_ready", ...})`. Извлечение — в
  `tmp_dir / "workspace"`, тот же `tmp_dir`, что и план — существующий
  `finally: shutil.rmtree(tmp_dir)` чистит всё разом.
- Если на второй базе нет понятия SSE/`on_event` вообще (проверить — у нас
  оно уже было отдельной более ранней правкой) — `_build_target` всё равно
  применим, просто без вызовов `emit(...)`.

### 5. `backend/pko/web/app.py`

`POST /api/analyses`: `repository: str = Form(...)` → `repository: str =
Form("")` (стало необязательным); новый параметр `files: list[UploadFile] =
File([])`. Перед вызовом `analyses.create_analysis`:

```python
uploads = [(f.filename, await f.read()) for f in files if f.filename]
```

(пустое имя файла — Starlette так отдаёт поле `files`, которое клиент вообще
не передал в multipart, а не файл с реально пустым именем).

## Тесты (backend)

Новый `tests/test_progress_local_source.py` — см. рабочую копию в этом
репозитории, переносится как есть (использует только
`local_source`/`target_repo`/`agent_tools`/`fixture_support` — те же имена,
что и на второй базе): zip-slip отклоняется, лимиты работают, `ToolBox`
читает `LocalTree` так же, как git-`Tree`, `merge_with_uploads` объединяет
деревья и пересчитывает `extraction`, uploaded-файл перебивает
одноимённый git-путь.

В существующих тестах веб-слоя (`test_web_analyses.py`) — `repository` в
хелпере создания анализа стал необязательным параметром, добавлены кейсы
`test_files_only_analysis_runs_without_any_repository`,
`test_repository_and_files_together_are_merged_into_one_analysis`,
`test_neither_repository_nor_files_still_runs_against_an_empty_workspace`
(создание анализа без обоих полей возвращает `200`, не `400`, — агент
получает пустой workspace); переименованы ожидаемые имена фаз в существующем
сквозном тесте (`repository_cloning`/`repository_ready` →
`materials_loading`/`materials_ready`, если на второй базе события фаз
вообще есть).

## Frontend (`frontend-web/`, если он там уже есть)

- `lib/types.ts::PhaseName` — `"repository_cloning" | "repository_ready"` →
  `"materials_loading" | "materials_ready"`.
- `lib/api.ts::createAnalysis` — новый параметр `files: File[]`, добавляется
  в `FormData` через `form.append("files", file)` в цикле (не `form.set` —
  нужно несколько файлов под одним именем поля).
- `components/upload/PresentationDropzone.tsx` **удалён**, заменён на
  `components/upload/FileDropzone.tsx` — тот же компонент с пропсами
  `files: File[]`/`onChange`/`accept?`/`multiple?`/`label`/`hint`, переиспользуется
  и для презентации (`multiple` не задан), и для файлов проекта
  (`multiple`).
- `components/upload/RepositoryInput.tsx` — убран `required` у поля
  репозитория.
- `components/upload/ProjectUploadForm.tsx` — добавлено состояние `files:
  File[]`, секция «Файлы проекта (необязательно)» с `FileDropzone`
  (`multiple`). Кнопка активна при одном условии — `presentation.length > 0`:
  ни репозиторий, ни файлы кнопку не блокируют (было `&& (repository ||
  files.length > 0)` — убрано вместе с подсказкой-предупреждением, когда
  пользователь явно решил не давать ни одного источника).
- `components/analysis/AnalysisProgress.tsx::PHASE_LABELS` — новые ключи
  `materials_loading: "Обрабатываем материалы проекта"`, `materials_ready:
  "Материалы проекта готовы"`.
- `components/dashboard/*` — **не менялись вообще**: dashboard не знает и не
  должен знать, откуда взялось evidence.

## Проверка после переноса

```bash
PYTHONPATH=backend python3 -m unittest discover -s tests   # все тесты зелёные
cd frontend-web && npm run build && npm run lint && npm test   # если frontend-web уже есть
```

Ручная проверка (со скриптованным/стаб-LLM или реальным): создать анализ (1)
только с файлами, без репозитория — в шапке dashboard должно быть «Проект»
(фолбэк на пустой `meta.repo`); (2) с репозиторием и файлами одновременно —
в шапке настоящее имя репозитория, а среди пунктов должна найтись evidence
из загруженного файла. Оба сценария на этой кодовой базе проверены вручную
через браузер (реальный upload → SSE-прогресс → dashboard) и дали
ожидаемый результат.

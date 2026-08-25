# Перенос PKO и первый запуск на корпоративном компьютере

Эта инструкция рассчитана на перенос текущей версии PKO на компьютер с доступом
к внутреннему Bitbucket и внутренним OpenAI-совместимым endpoint'ам. PKO читает
репозиторий по SSH, не выполняет его код и тесты и не изменяет его файлы.

> **Обновление уже перенесённой копии.** Архив нужен один раз, при первой
> установке. Дальше изменения переносятся патчем: `make patch` кладёт в
> `transfer/out/` патч, контрольную сумму и манифест с инструкцией. Это
> килобайты вместо мегабайт, изменения видно глазами до отправки, а привязка
> к базовому коммиту не даёт применить патч не туда. Подробности —
> `transfer/README.md`.

## 1. Создать архив текущего рабочего каталога

Для переноса самого PKO git-история не нужна. Архив включает текущее рабочее
дерево, поэтому в него попадут и modified/untracked исходники, которых ещё нет
в старом `HEAD`. Перед упаковкой выполните:

```bash
cd /path/to/pko
make test
```

Ожидаемый тестовый результат для этой версии: все тесты завершаются `OK`.

Не переносите:

- `.env*`, ключи API, приватные SSH-ключи и дампы окружения;
- `.git/` при переносе обычным архивом;
- `.venv/`, `__pycache__/`, `*.pyc`, `.DS_Store`;
- `pko-out/`, `bench/runs/`, `build/`, `*.egg-info/`;
- `~/.pko/`: там находятся зеркала Bitbucket, LLM-кеш и потенциально
  конфиденциальные данные предыдущих запусков;
- `tests/fixtures/mini_repo/` и `tests/fixtures/multistack_repo/`: это
  генерируемые тестовые git-репозитории.

Проверьте отдельно файлы `intent_*.yaml` и документы с бизнес-требованиями. Они
могут содержать сведения конкретного проекта и должны переноситься только если
разрешены корпоративной политикой.

После очистки локальных артефактов создайте архив **за пределами** каталога PKO:

```bash
tar \
  --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
  --exclude='*.pyc' --exclude='pko-out' --exclude='bench/runs' \
  --exclude='build' --exclude='dist' --exclude='*.egg-info' \
  --exclude='tests/fixtures/mini_repo' \
  --exclude='tests/fixtures/multistack_repo' \
  --exclude='business_requirements_ai_advisor.md' \
  --exclude='intent_ai_advisor.yaml' \
  --exclude='intent_ai_advisor_overstated.yaml' \
  -czf ../pko-corporate-transfer.tar.gz .
```

Последние три файла относятся к конкретному проекту AI Advisor и для работы PKO
не нужны. При необходимости перенесите их отдельно после проверки содержимого.
Передавайте архив только разрешённым корпоративным способом.

На корпоративном компьютере:

```bash
mkdir pko
tar -xzf /path/to/pko-corporate-transfer.tar.gz -C pko
cd pko
```

## 2. Проверить корпоративный компьютер

Нужны:

- Python 3.11 или новее;
- `git`, `bash` и желательно `make`;
- доступ к внутреннему Bitbucket;
- SSH-ключ пользователя с правом чтения нужного репозитория;
- доступ к внутренним GLM и DeepSeek endpoint'ам, если включаются модели.

```bash
python3 --version
git --version
bash --version
ssh-add -l
```

PKO не принимает SSH-ключ как параметр и не хранит его. Клонирование выполняется
от имени текущего пользователя через `ssh-agent`. Если ключ не загружен:

```bash
ssh-add /path/to/corporate_private_key
```

Сверьте отпечаток Bitbucket с опубликованным внутри компании и только после
этого проверьте соединение:

```bash
ssh -T -p 7999 git@<bitbucket-host>
```

Не отключайте проверку `known_hosts` и не копируйте приватный ключ в каталог PKO.

## 3. Запустить PKO без установки зависимостей

У PKO нет runtime-зависимостей за пределами стандартной библиотеки Python.
Самый надёжный вариант в закрытом контуре — не обращаться к публичному PyPI:

```bash
cd /path/to/pko
export PYTHONPATH="$PWD/src"
python3 -m pko.cli --help
python3 -m unittest discover -s tests
```

Тесты создают синтетические git-репозитории внутри `tests/fixtures/`; это
ожидаемо. После проверки их можно удалить командой `make clean` и отдельно
`rm -rf tests/fixtures/multistack_repo`.

Если корпоративный Python уже содержит `setuptools>=68`, можно установить
локальную CLI-команду без скачивания зависимостей:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --no-deps -e .
pko --help
```

Если установка пытается обратиться к интернету, остановите её и используйте
вариант с `PYTHONPATH=src` либо внутренний Python package index.

## 4. Проверить доступ к Bitbucket без анализа

Возьмите SSH URL из Bitbucket:

```bash
export PKO_REPO_SSH='ssh://git@<bitbucket-host>:7999/<project>/<repo>.git'

python3 -m pko.cli history "$PKO_REPO_SSH" \
  --max-versions 6 \
  --network-timeout 900
```

Команда создаёт локальное зеркало в `~/.pko/repos/`, делает `fetch` и показывает
выбранные версии. Анализируемый репозиторий не изменяется и `push` не выполняется.
Проверьте имя репозитория, основную ветку, начальный коммит и merge-коммиты.

## 5. Выполнить первый детерминированный анализ

Сначала запустите PKO без языковых моделей:

```bash
python3 -m pko.cli analyze "$PKO_REPO_SSH" \
  --max-versions 2 \
  --no-fetch \
  --out pko-out
```

Без подтверждённого `business_intent.yaml` ожидается код `4` (`NO_DECISION`).
Это штатный результат: отчёты создаются, но PKO не выдумывает потребность,
бизнес-владельца и целевой режим из исходного кода.

Проверьте в `pko-out/`:

- `taxonomy*.html` и `passports*.html`;
- `gate_card*.md`;
- `diff_*.md` и `comparison_*.html`;
- `pko_*.json` и `semantic_facts.json`.

До ручной проверки не используйте `--publish`.

## 6. Настроить внутренние модели

Точные URL, model ID и ключи в репозитории намеренно не хранятся. Подставьте
значения, выданные владельцами внутренних endpoint'ов:

```bash
# GLM в роли scout: единственная модель, которая получает прочитанные файлы кода.
export PKO_SCOUT_BASE_URL='https://<internal-glm-host>/v1'
export PKO_SCOUT_MODEL='<GLM_MODEL_ID>'
export PKO_SCOUT_API_KEY='<GLM_API_KEY>'

# Обязательный default-deny allowlist для endpoint, получающего код.
# Только hostname, без https:// и /v1; несколько hosts — через запятую.
export PKO_SCOUT_ALLOWED_HOSTS='<internal-glm-host>'

# DeepSeek пишет русский текст по готовой PKO-модели и исходный код не получает.
export PKO_WRITER_BASE_URL='https://<internal-deepseek-host>/v1'
export PKO_WRITER_MODEL='<DEEPSEEK_MODEL_ID>'
export PKO_WRITER_API_KEY='<DEEPSEEK_API_KEY>'
```

Не записывайте эти значения в `.env` внутри проекта. Проверьте, что
`PKO_SCOUT_ALLOWED_HOSTS` содержит именно внутренний host: без allowlist либо при
несовпадении PKO остановится до первого запроса с кодом.

Если нужен режим без scout, где GLM только группирует уже извлечённые кандидаты,
дополнительно используются `PKO_ASSEMBLER_BASE_URL`, `PKO_ASSEMBLER_MODEL` и
`PKO_ASSEMBLER_API_KEY`.

## 7. Запустить универсальный scout и русский writer

```bash
python3 -m pko.cli analyze "$PKO_REPO_SSH" \
  --max-versions 2 \
  --no-fetch \
  --agent \
  --llm \
  --scout-base-url "$PKO_SCOUT_BASE_URL" \
  --scout-model "$PKO_SCOUT_MODEL" \
  --scout-api-key-env PKO_SCOUT_API_KEY \
  --out pko-out
```

В этом режиме:

- GLM/scout читает код через ограниченные read-only инструменты;
- scout не имеет shell, сети, записи в репозиторий или запуска тестов;
- `note_fact` записывает только наблюдение в trace PKO;
- DeepSeek получает готовую модель, но не текст исходных файлов;
- при недоступности DeepSeek публикуется детерминированный шаблонный текст;
- решение Gate считает код PKO, а не языковая модель.

Trace агента содержит прочитанные фрагменты кода и является конфиденциальным.
Не отправляйте `agent_trace_*.json/html` за пределы разрешённого контура.

## 8. Добавить бизнес-контекст и готовый JUnit

Рекомендуемый вариант — заполнить `business_intent.yaml` рядом с кодом
анализируемого проекта по образцу `examples/business_intent.yaml`. Временный
внешний файл можно передать явно:

Для решения Gate обязательны четыре поля клиентского результата и явная граница
полномочий: `decision_boundary`, `in_scope`, `forbidden_effects`. Пустая граница
даёт `NO_DECISION`, а не разрешение на весь разобранный код. Отсутствие запретов
фиксируется явно как `forbidden_effects: none`.

```bash
python3 -m pko.cli analyze "$PKO_REPO_SSH" \
  --max-versions 2 --no-fetch --agent --llm \
  --scout-api-key-env PKO_SCOUT_API_KEY \
  --intent /approved/path/business_intent.yaml \
  --junit /approved/path/junit.xml \
  --out pko-out
```

PKO не запускает тесты анализируемого проекта. `--junit` принимает готовый JUnit
XML и применяет его только к текущей версии кода.

## 9. Коды завершения и диагностика

| Код | Значение |
|---:|---|
| `0` | `ALLOW` или `ALLOW_WITH_RESTRICTIONS` |
| `1` | ошибка доступа, клонирования, endpoint или анализа |
| `2` | неверные аргументы CLI |
| `3` | `DENY` либо требуется более полный контур проверки |
| `4` | `NO_DECISION`: не подтверждён обязательный бизнес-контекст |

Частые причины:

- `Permission denied (publickey)` — ключ не загружен или нет доступа;
- `Host key verification failed` — сначала проверьте корпоративный fingerprint;
- `Scout endpoint ... отсутствует в allowlist` — исправьте host, а не отключайте
  защиту;
- мало объектов — проверьте `Coverage`, `semantic_facts.json` и agent trace;
- код `3`/`4` при созданных отчётах — результат Gate, а не падение программы.

## Финальный чек-лист

- [ ] Перенесены все modified/untracked исходники, а не только старый `HEAD`.
- [ ] В переносимом каталоге нет ключей, `.env`, кешей и результатов прогонов.
- [ ] На корпоративном компьютере проходят тесты.
- [ ] Проверен SSH-доступ к Bitbucket от имени пользователя.
- [ ] Scout host явно внесён в корпоративный allowlist PKO.
- [ ] Первый запуск выполнен без моделей и отчёты просмотрены вручную.
- [ ] Только после этого включены `--agent --llm` и внутренние endpoint'ы.

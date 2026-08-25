# Изменения PKO

| Поле | Значение |
|---|---|
| Базовый коммит | `0ccd9c56d722af7b92e8656fdaac99991f9dea05` |
| Файлов | 65 (новых 37, изменённых 28) |
| Собрано | 2026-08-12 13:35 |

## Как перенести

Каталог `files/` повторяет структуру репозитория, поэтому копирование поверх
установленного PKO и есть применение изменений.

```bash
# на этой машине
tar -czf pko-изменения.tar.gz -C .. bundle

# на принимающей
tar -xzf pko-изменения.tar.gz
cd bundle
shasum -a 256 -c SHA256SUMS             # на Linux: sha256sum -c
cp -R files/. /path/to/pko/
cd /path/to/pko && make test
```

Суммы посчитаны по путям вида `files/…`, поэтому проверять их нужно из самого
каталога `bundle`, а не снаружи.

Базовый коммит указан для сверки: файлы собраны поверх `0ccd9c56`. Если на
принимающей стороне другая версия, копирование затрёт её изменения без
предупреждения — сравните версии до переноса.

## Что вошло

- `.gitignore` — изменён
- `CORPORATE_FIRST_RUN.md` — новый
- `FIRST_RUN.md` — новый
- `Makefile` — изменён
- `README.md` — изменён
- `bench/report.py` — новый
- `bench/run_bench.py` — новый
- `bench/targets.yaml` — новый
- `pyproject.toml` — изменён
- `src/pko/agent/__init__.py` — новый
- `src/pko/agent/loop.py` — новый
- `src/pko/agent/prompts/packs/agents.md` — новый
- `src/pko/agent/prompts/packs/data.md` — новый
- `src/pko/agent/prompts/packs/frontend.md` — новый
- `src/pko/agent/prompts/packs/jobs.md` — новый
- `src/pko/agent/prompts/packs/messaging.md` — новый
- `src/pko/agent/prompts/packs/web.md` — новый
- `src/pko/agent/prompts/scout_core.md` — новый
- `src/pko/agent/stack.py` — новый
- `src/pko/agent/tools.py` — новый
- `src/pko/agent/trace.py` — новый
- `src/pko/agent/trace_report.py` — новый
- `src/pko/agent/verifiers/__init__.py` — новый
- `src/pko/agent/verifiers/controls.py` — новый
- `src/pko/agent/verifiers/data.py` — новый
- `src/pko/agent/verifiers/flow.py` — новый
- `src/pko/agent/verifiers/interfaces.py` — новый
- `src/pko/agent/verifiers/messaging.py` — новый
- `src/pko/agent/verify.py` — новый
- `src/pko/assemble/candidates.py` — изменён
- `src/pko/assemble/heuristic.py` — изменён
- `src/pko/checks/validator.py` — изменён
- `src/pko/cli.py` — изменён
- `src/pko/extractors/base.py` — изменён
- `src/pko/extractors/python_code.py` — изменён
- `src/pko/extractors/runner.py` — изменён
- `src/pko/gate/evaluate.py` — изменён
- `src/pko/gate/policies.py` — новый
- `src/pko/intent/loader.py` — изменён
- `src/pko/llm/client.py` — изменён
- `src/pko/llm/registry.py` — изменён
- `src/pko/model/schema.py` — изменён
- `src/pko/model/semantic.py` — новый
- `src/pko/model/taxonomy.py` — новый
- `src/pko/pipeline.py` — изменён
- `src/pko/render/base.py` — изменён
- `src/pko/render/comparison.py` — изменён
- `src/pko/render/passports.py` — изменён
- `src/pko/render/taxonomy.py` — изменён
- `src/pko/report/guard.py` — изменён
- `src/pko/report/writer.py` — изменён
- `src/pko/util/yamlmini.py` — изменён
- `tests/fixture_support.py` — изменён
- `tests/make_fixture_multistack.sh` — новый
- `tests/test_agent.py` — новый
- `tests/test_bench.py` — новый
- `tests/test_cli_contract.py` — изменён
- `tests/test_pipeline.py` — изменён
- `tests/test_render.py` — новый
- `tests/test_transfer.py` — новый
- `tests/test_units.py` — изменён
- `transfer/README.md` — новый
- `transfer/bundle.zip` — новый
- `transfer/make_bundle.py` — новый
- `transfer/make_patch.py` — новый

## Чего в каталоге нет

Ключи и `.env`, кеши `~/.pko`, каталоги прогонов (`pko-out/`, `bench/runs/`),
генерируемые тестовые репозитории и материалы конкретного проекта
(`intent_ai_advisor*.yaml`, `business_requirements_ai_advisor.md`). Перечень
исключений — в `transfer/make_patch.py`.

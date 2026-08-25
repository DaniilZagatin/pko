"""Командная строка PKO.

    pko analyze ssh://git@<bitbucket-host>:7999/<project>/<repo>.git
    pko analyze --repo-path ~/src/<repo> --no-fetch
    pko history ssh://...
    pko gate --repo-path . --junit reports/junit.xml

Клон делается от вашего имени по SSH, зеркалом, без рабочего дерева. Отчёты
пишутся в каталог `pko-out`; заменить опубликованные файлы можно только явным
флагом `--publish`.

Коды возврата: 0 — допуск выдан; 3 — отказ в допуске (`DENY`);
4 — решение не выносилось, нет `business_intent.yaml`; 1 — ошибка запуска;
2 — ошибка синтаксиса CLI, которую диагностировал argparse до запуска.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from pko import __version__
from pko.agent.trace_report import render_trace
from pko.diff.engine import diff_models
from pko.errors import PkoError
from pko.extractors.base import is_config_path
from pko.extractors.test_reports import junit_source
from pko.gate.decide import (
    ALLOW,
    ALLOW_WITH_RESTRICTIONS,
    DECISION_DENY,
    NO_DECISION,
    REQUIRE_FULL_CONTOUR,
)
from pko.gate.record import BasicRecord, build_record
from pko.git.remote import DEFAULT_CACHE_ROOT, ensure_mirror
from pko.git.repo import GitRepo
from pko.git.url import parse_repo_url
from pko.history.selector import select_versions
from pko.intent.loader import SEARCH_PATHS as INTENT_PATHS
from pko.llm.registry import ModelSpec, get_spec
from pko.model.semantic import to_json as semantic_to_json
from pko.standard import catalog as standard_catalog
from pko.agent.loop import available_packs
from pko.output.publisher import publish, write_outputs
from pko.pipeline import VersionAnalysis, analyze_version
from pko.progress.matcher import find_unclaimed_paths, match_plan
from pko.progress.schema import ProgressModel
from pko.progress.target_repo import load_target
from pko.render.comparison import render_comparison
from pko.render.dashboard import render_dashboard
from pko.render.diff_md import render_diff_md
from pko.render.gate_card import render_gate_card
from pko.render.passports import render_passports
from pko.render.progress_report import render_progress_report
from pko.render.taxonomy import render_taxonomy
from pko.util.paths import harden_file
from pko.report.writer import write_diff_narrative, write_object_notes, write_overview

DEFAULT_OUT = Path("pko-out")
DEFAULT_PROGRESS_OUT = Path("pko-progress-out")


def _positive_int(value: str) -> int:
    """argparse type для параметров, которые физически не могут быть нулём."""
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ожидается целое число") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("значение должно быть не меньше 1")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    try:
        return args.handler(args)
    except PkoError as exc:
        print(f"\nОшибка: {exc.render()}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nПрервано пользователем.", file=sys.stderr)
        return 130


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pko",
        description="Восстановление паспортов автономного процесса по коду репозитория",
    )
    parser.add_argument("--version", action="version", version=f"pko {__version__}")
    sub = parser.add_subparsers(dest="command")

    analyze = sub.add_parser("analyze", help="разобрать репозиторий и выпустить отчёты")
    _add_source_args(analyze)
    _add_agent_args(analyze)
    analyze.add_argument("--max-versions", type=_positive_int, default=2,
                         help="сколько точек истории обсчитать (по умолчанию 2: первая и текущая)")
    analyze.add_argument("--out", default=str(DEFAULT_OUT), help="каталог для отчётов")
    analyze.add_argument("--junit", default=None, help="готовый JUnit XML от pytest")
    analyze.add_argument("--intent", default=None,
                         help="путь к business_intent.yaml или .json")
    analyze.add_argument("--llm", action="store_true",
                         help="использовать языковые модели (сборщик и писатель)")
    analyze.add_argument("--publish", action="store_true",
                         help="заменить опубликованные отчёты в каталоге --publish-dir")
    # Текущий каталог, а не корень исходников PKO: при установке пакета
    # `parents[2]` указывает внутрь site-packages интерпретатора.
    analyze.add_argument("--publish-dir", default=None,
                         help="куда публиковать отчёты (по умолчанию текущий каталог)")
    analyze.set_defaults(handler=cmd_analyze)

    history = sub.add_parser("history", help="показать выбранные версии без анализа")
    _add_source_args(history)
    history.add_argument("--max-versions", type=_positive_int, default=6)
    history.set_defaults(handler=cmd_history)

    gate = sub.add_parser("gate", help="выпустить только BASIC Gate Card по текущей версии")
    _add_source_args(gate)
    _add_agent_args(gate)
    gate.add_argument("--out", default=str(DEFAULT_OUT))
    gate.add_argument("--junit", default=None)
    gate.add_argument("--intent", default=None)
    gate.set_defaults(handler=cmd_gate)

    progress = sub.add_parser(
        "progress", help="сравнить PPTX-план команды с кодом репозитория"
    )
    progress.add_argument("plan", help="путь к .pptx с планом команды")
    _add_source_args(progress)
    progress.add_argument("--out", default=str(DEFAULT_PROGRESS_OUT),
                          help="каталог для отчёта о прогрессе")
    progress.set_defaults(handler=cmd_progress)

    return parser


def _add_source_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("url", nargs="?", default=None,
                   help="SSH-ссылка Bitbucket: ssh://git@host:7999/project/repo.git")
    p.add_argument("--repo-path", default=None,
                   help="путь к уже существующему локальному клону (запасной вход без сети)")
    p.add_argument("--branch", default=None, help="ветка (по умолчанию master или main)")
    p.add_argument("--cache-root", default=str(DEFAULT_CACHE_ROOT),
                   help="каталог кеша зеркал")
    p.add_argument("--no-fetch", action="store_true",
                   help="не обновлять зеркало, работать с уже скачанным")
    p.add_argument("--network-timeout", type=int, default=900)


def _add_agent_args(p: argparse.ArgumentParser) -> None:
    """Флаги агента разведки. Ключ — только именем переменной окружения."""
    p.add_argument("--agent", action="store_true",
                   help="разведку ведёт агент: читает репозиторий сам, а не через экстракторы")
    p.add_argument("--scout-base-url", default=None,
                   help="endpoint агента; приоритетнее переменных окружения")
    p.add_argument("--scout-model", default=None, help="имя модели агента")
    p.add_argument("--scout-api-key-env", default=None,
                   help="ИМЯ переменной окружения с ключом (не сам ключ: "
                        "значение осело бы в истории оболочки и в выводе ps)")
    p.add_argument("--scout-allowed-hosts", default=None,
                   help="разрешённые внутренние hosts через запятую; иначе "
                        "PKO_SCOUT_ALLOWED_HOSTS (без allowlist код не отправляется)")
    p.add_argument("--agent-packs", default=None,
                   help="паки промпта через запятую вместо автоопределения "
                        "(диагностика: web,data,agents,frontend,jobs,messaging)")
    p.add_argument("--agent-max-steps", type=int, default=0,
                   help="потолок шагов агента; 0 — без ограничения (по умолчанию)")
    p.add_argument("--agent-trace-format", choices=("json", "html", "both"), default="both",
                   help="в каком виде сохранять трассу разведки")


# --- команды ---------------------------------------------------------------
def cmd_history(args: argparse.Namespace) -> int:
    repo, repo_name = _open_repo(args)
    branch = args.branch or repo.default_branch()
    versions = select_versions(repo, branch, max_versions=args.max_versions)
    print(f"Репозиторий: {repo_name} · ветка: {branch}")
    print(f"Выбрано версий: {len(versions)}\n")
    for v in versions:
        print(f"  {v.label:<8} {v.commit.short}  {v.commit.date}  {v.reason}")
        print(f"           {v.commit.subject[:90]}")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    # Имена паков проверяем до открытия репозитория: опечатка не должна
    # обнаруживаться на середине прогона, когда часть работы уже сделана.
    packs = _packs(args)
    repo, repo_name = _open_repo(args)
    branch = args.branch or repo.default_branch()
    versions = select_versions(repo, branch, max_versions=args.max_versions)

    scout = _scout_spec(args)
    assembler = get_spec("assembler") if args.llm and scout is None else None
    # thinking=True включает `enable_thinking` для DeepSeek: без него режим
    # рассуждения на этом endpoint не активируется.
    writer = get_spec("writer", thinking=True) if args.llm else None
    if args.llm and writer is None:
        # `--llm` — это заявка на текст модели. Пройти весь анализ и выдать
        # шаблон значит выпустить отчёт, который читатель примет за
        # написанный моделью. Отказ до работы дешевле такой подмены.
        raise PkoError(
            "Запуск с --llm, но endpoint писателя не настроен",
            hint="задайте PKO_WRITER_BASE_URL (или DEEPSEEK_BASE_URL) либо уберите --llm",
        )
    if args.llm or scout is not None:
        print(f"Сборщик: {assembler.model if assembler else 'не используется'} · "
              f"писатель: {writer.model if writer else 'не настроен'}")
    if scout is not None:
        print(f"Агент разведки: {scout.model} @ {scout.base_url} · "
              f"шагов: {'без ограничения' if not args.agent_max_steps else args.agent_max_steps}")

    print(f"Репозиторий: {repo_name} · ветка: {branch} · версий: {len(versions)}")

    analyses: list[VersionAnalysis] = []
    overviews: dict[str, str] = {}
    # Кто написал обзор — модель или шаблон. Читатель должен видеть это на странице.
    overview_sources: dict[str, str] = {}
    out_dir = Path(args.out)
    files: dict[str, tuple[str, str]] = {}
    generated_at = time.strftime("%Y-%m-%d %H:%M")
    # Historical cards не загружают JUnit (он относится только к HEAD), но всё
    # равно объясняют это ограничение. В сообщение идёт тот же переносимый ID,
    # что и в evidence HEAD, а не абсолютный путь оператора.
    junit_id = junit_source(args.junit) if args.junit else ""

    for version in versions:
        print(f"\n  [{version.label}] {version.commit.short} {version.commit.date} — анализ…")
        # Отчёт о тестах относится к тому коду, на котором его получили. Прикладывать
        # один и тот же JUnit ко всем версиям нельзя: исторический коммит получил бы
        # доказательство, которого для него никто не производил.
        is_head = version.sha == versions[-1].sha
        analysis = analyze_version(
            repo=repo,
            version=version,
            repo_name=repo_name,
            branch=branch,
            junit_path=args.junit if is_head else None,
            # Подтверждение владельца привязано к версии ровно так же, как отчёт
            # о тестах. Файл, переданный флагом, написан для сегодняшнего кода;
            # применённый к историческому коммиту, он выдавал допуск версии,
            # которую никто не подтверждал, — и приписка «для этой версии
            # подтверждение не проверялось» стояла под уже вынесенным решением.
            # Для неголовных версий берётся то, что лежало в самом коммите: нет
            # там намерения — решение не выносится, и это честный исход.
            intent_path=args.intent if is_head else None,
            assembler=assembler,
            scout=scout,
            agent_max_steps=args.agent_max_steps,
            agent_packs=packs,
        )
        if args.junit and not is_head:
            analysis.model.gaps.append(
                f"Отчёт о тестах {junit_id} относится к версии "
                f"{versions[-1].commit.short}: к этой версии он не применялся"
            )
        if args.intent and not is_head:
            # Пробел описывает то, что произошло, а не то, чего хотелось: иначе
            # он снова разойдётся с решением, стоящим в той же карточке.
            head = versions[-1].commit.short
            if analysis.intent.present:
                analysis.model.gaps.append(
                    f"--intent относится к версии {head} и к этой версии не "
                    f"применялся; подтверждение владельца взято из самого коммита "
                    f"({analysis.intent.source})"
                )
            else:
                analysis.model.gaps.append(
                    f"--intent относится к версии {head} и к этой версии не "
                    f"применялся; в самом коммите подтверждения владельца нет, "
                    f"поэтому решение о допуске для неё не выносится"
                )
        analyses.append(analysis)
        _print_version_summary(analysis)
        files.update(_trace_files(analysis, args))

        overview = write_overview(analysis.model, writer)
        overviews[version.label] = overview.text
        overview_sources[version.label] = overview.source
        if overview.notes:
            analysis.model.gaps.extend(overview.notes)
        # Пояснения к объектам: паспорт из одних полей и координат читателю
        # ничего не объясняет — он видит «что записано», но не «что это значит».
        object_notes, note_problems = write_object_notes(analysis.model, writer)
        if note_problems:
            analysis.model.gaps.extend(note_problems)

        suffix = version.label
        files[f"model-{suffix}"] = (f"pko_{suffix}.json", analysis.model.to_json())
        files[f"taxonomy-{suffix}"] = (
            f"taxonomy_{suffix}.html",
            render_taxonomy(analysis.model, overview.text, overview.source),
        )
        files[f"passports-{suffix}"] = (
            f"passports_{suffix}.html",
            render_passports(analysis.model, notes=object_notes, overview=overview.text,
                             overview_source=overview.source),
        )
        files[f"gate-{suffix}"] = (
            f"gate_card_{suffix}.md",
            render_gate_card(analysis.model, analysis.checks, analysis.decision,
                             _basic_record(analysis, generated_at)),
        )

    # Машинный срез по всем версиям: фасеты, доказательства и признак того,
    # входило ли наблюдение в вердикт. Пишется тем же комплектом, что и
    # отчёты, иначе данные и отчёт могли бы относиться к разным прогонам.
    files["semantic"] = ("semantic_facts.json", semantic_to_json([
        {
            "label": a.version.label,
            "commit": a.version.sha,
            "facts": a.extraction.facts,
            "gaps": a.model.gaps,
            "packs": a.agent.trace.packs if a.agent else [],
            "stack": a.agent.stack.to_dict() if a.agent else {},
        }
        for a in analyses
    ]))

    latest = analyses[-1]
    # Основные отчёты — по последней версии; они же публикуются.
    files["taxonomy"] = ("taxonomy.html", files[f"taxonomy-{latest.version.label}"][1])
    files["passports"] = ("passports.html", files[f"passports-{latest.version.label}"][1])

    # Аудиты рядом с отчётом: решение допуска и готовность к промышленному
    # контуру — разные вопросы, и хранятся они раздельно, чтобы «профиль FULL»
    # нельзя было прочитать как «FULL достигнут».
    files["basic-gate"] = ("basic_gate.json", _gate_json(latest, generated_at))
    files["readiness"] = ("full_readiness.json",
                          _json(latest.readiness.to_dict()))
    files["coverage"] = ("standard_coverage.json",
                         _json(standard_catalog.to_dict(latest.profile.value)))

    if len(analyses) >= 2:
        first, last = analyses[0], analyses[-1]
        model_diff = diff_models(first.model, last.model)
        narrative = write_diff_narrative(model_diff, last.model, writer)
        files["diff"] = (
            f"diff_{first.version.label}_{last.version.label}.md",
            render_diff_md(model_diff, narrative.text),
        )
        files["comparison"] = (
            f"comparison_{first.version.label}_{last.version.label}.html",
            render_comparison(model_diff, narrative.text),
        )
        summary = model_diff.summary()
        print(f"\n  Сравнение {first.version.label} → {last.version.label}: "
              f"добавлено {summary['ADDED']}, удалено {summary['REMOVED']}, "
              f"изменено {summary['CHANGED']}")

    # Единая точка входа собирается последней: к этому моменту известен весь
    # состав комплекта, и в ссылках не окажется файла, которого нет.
    files["index"] = ("index.html", render_dashboard(
        latest.model, latest.checks, latest.decision, latest.readiness,
        overview=overviews.get(latest.version.label, ""),
        links=_report_links(files),
        record=_basic_record(latest, generated_at),
        overview_source=overview_sources.get(latest.version.label, ""),
    ))

    written = write_outputs(out_dir, files)
    _protect_traces(written)
    print(f"\nОтчёты записаны в {out_dir.resolve()}")
    for item in written:
        print(f"  {item.path.name}")

    if args.publish:
        target = Path(args.publish_dir).expanduser() if args.publish_dir else Path.cwd()
        actions = publish(written, target)
        print(f"\nПубликация в {target.resolve()}:")
        for action in actions:
            print(f"  {action}")

    return _exit_code(latest.decision.decision)


def cmd_gate(args: argparse.Namespace) -> int:
    packs = _packs(args)
    repo, repo_name = _open_repo(args)
    branch = args.branch or repo.default_branch()
    versions = select_versions(repo, branch, max_versions=1)
    scout = _scout_spec(args)
    if scout is not None:
        print(f"Агент разведки: {scout.model} @ {scout.base_url}")
    analysis = analyze_version(
        repo=repo,
        version=versions[0],
        repo_name=repo_name,
        branch=branch,
        junit_path=args.junit,
        intent_path=args.intent,
        scout=scout,
        agent_max_steps=args.agent_max_steps,
        agent_packs=packs,
    )
    # Запись собирается один раз и из полного анализа — так же, как в `analyze`.
    # Иначе карточка отдельной команды строилась из меньшего набора входов и
    # печатала «существенная конфигурация не найдена» там, где `analyze` на том
    # же коммите печатал файл политик, а раздел о незаполненных полях владельца
    # пропадал целиком.
    generated_at = time.strftime("%Y-%m-%d %H:%M")
    record = _basic_record(analysis, generated_at)
    files = {
        "gate": ("gate_card.md", render_gate_card(
            analysis.model, analysis.checks, analysis.decision, record)),
        # Машинная запись §8.0.1 — то, ради чего команда и существует.
        # Выпускать её только в `analyze` значит требовать полного прогона
        # ради контракта, который вычислен уже здесь.
        "basic-gate": ("basic_gate.json", _json(record.to_dict(generated_at))),
    }
    files.update(_trace_files(analysis, args))
    written = write_outputs(Path(args.out), files)
    _protect_traces(written)
    _print_version_summary(analysis)
    # По имени, а не по индексу: комплект команды больше не состоит из одного
    # файла, и порядок словаря не должен решать, что показать оператору.
    card_path = next(w.path for w in written if w.path.name == "gate_card.md")
    print(f"\nGate Card: {card_path.resolve()}")
    print(f"Запись допуска: {(Path(args.out) / 'basic_gate.json').resolve()}")
    return _exit_code(analysis.decision.decision)


def cmd_progress(args: argparse.Namespace) -> int:
    """Сравнить PPTX-план команды с кодом репозитория и выпустить отчёт о прогрессе.

    Отдельный от Gate пайплайн: здесь нет решения о допуске, есть степень
    выполнения плана. `python-pptx` — опциональная зависимость (`pip install
    pko[progress]`), поэтому импорт `pptx_reader`/`plan_extract` отложен до
    вызова этой команды — иначе `analyze`/`gate`/`history` перестали бы
    запускаться в контуре без установленного пакета.
    """
    plan_path = Path(args.plan).expanduser()
    if not plan_path.exists():
        raise PkoError(f"Файл плана не найден: {plan_path}", hint="проверьте путь к .pptx")

    planner = get_spec("planner")
    matcher_spec = get_spec("matcher")
    if planner is None or matcher_spec is None:
        raise PkoError(
            "Не настроен LLM-доступ для пайплайна прогресса.",
            hint="задайте PKO_ASSEMBLER_BASE_URL/PKO_ASSEMBLER_MODEL/PKO_ASSEMBLER_API_KEY — "
                 "роли planner/matcher используют его по умолчанию; либо настройте "
                 "PKO_PLANNER_*/PKO_MATCHER_* отдельно.",
        )

    try:
        from pko.progress.pptx_reader import read_deck
    except ImportError as exc:
        raise PkoError(
            "Не установлен python-pptx.",
            hint="поставьте пакет: pip install 'pko[progress]' (или python-pptx>=1.0)",
        ) from exc
    from pko.progress.plan_extract import extract_plan

    repo, repo_name = _open_repo(args)
    branch = args.branch or repo.default_branch()
    target = load_target(repo, branch)
    print(f"Репозиторий: {repo_name} · ветка: {target.branch} · коммит {target.sha[:8]}")

    print(f"План: {plan_path.name}")
    slides = read_deck(plan_path)
    plan_result = extract_plan(slides, planner)
    for note in plan_result.notes:
        print(f"  {note}")
    if not plan_result.usable:
        raise PkoError(
            "Не удалось извлечь пункты плана из презентации.",
            hint="проверьте текст слайдов; причина — в заметках выше",
        )
    print(f"Пунктов плана: {len(plan_result.items)}")

    match_result = match_plan(plan_result.items, target.extraction, target.tree, matcher_spec)
    for note in match_result.notes:
        print(f"  {note}")

    unclaimed = find_unclaimed_paths(target.extraction, match_result.verdicts)
    generated_at = time.strftime("%Y-%m-%d %H:%M")
    model = ProgressModel(
        meta={
            "repo": repo_name, "branch": target.branch, "commit": target.sha,
            "plan_source": plan_path.name, "generated_at": generated_at,
        },
        items={item.id: item for item in plan_result.items},
        verdicts=match_result.verdicts,
        unclaimed=unclaimed,
        gaps=plan_result.notes + match_result.notes,
    )

    files = {
        "progress-model": ("progress_model.json", model.to_json()),
        "progress-report": ("progress_report.html", render_progress_report(model)),
    }
    written = write_outputs(Path(args.out), files)
    report_path = next(w.path for w in written if w.path.name == "progress_report.html")
    print(f"\nОтчёт о прогрессе: {report_path.resolve()}")
    print(f"Прогресс: {model.progress_ratio():.0%} пунктов сделано ({len(model.verdicts)} всего)")
    return 0


def _exit_code(decision: str) -> int:
    """Код возврата отражает вердикт, а не факт запуска.

    Ноль означает выданный допуск и только его. `REQUIRE_FULL_CONTOUR` — это «BASIC
    недостаточно, нужен полный контур», то есть допуск не выдан: раньше он
    проваливался в `return 0` и читался конвейером как разрешение.

    4 отделено от 3 намеренно: незаполненный ручной вход — не отказ.
    """
    if decision in {DECISION_DENY, REQUIRE_FULL_CONTOUR}:
        return 3
    if decision == NO_DECISION:
        return 4
    if decision in {ALLOW, ALLOW_WITH_RESTRICTIONS}:
        return 0
    # Неизвестный вердикт не может молча означать «разрешено».
    return 3


# --- вспомогательное -------------------------------------------------------
def _open_repo(args: argparse.Namespace) -> tuple[GitRepo, str]:
    """Открыть репозиторий: по SSH-ссылке через зеркало или по локальному пути."""
    if args.repo_path:
        # Путь приводится к абсолютному до взятия имени: у `.` и `..` имени нет,
        # и в ссылку на реализацию попадало пустое значение — `@<sha>` вместо
        # `repo@<sha>`, хотя точная ссылка и есть смысл карточки допуска.
        path = Path(args.repo_path).expanduser().resolve()
        return GitRepo(path, timeout=args.network_timeout), _repo_name(path)

    if not args.url:
        raise PkoError(
            "Не указан репозиторий.",
            "Передайте SSH-ссылку Bitbucket или путь к клону через --repo-path.",
        )

    ref = parse_repo_url(args.url)
    print(f"Репозиторий {ref.slug} на {ref.host} — подготовка зеркала…")
    info = ensure_mirror(
        args.url,
        cache_root=Path(args.cache_root).expanduser(),
        fetch=not args.no_fetch,
        timeout=args.network_timeout,
    )
    action = "склонировано" if info.created else ("обновлено" if info.fetched else "без обновления")
    print(f"  зеркало {action}: {info.path}")
    return GitRepo(info.path, timeout=args.network_timeout), ref.repo


def _scout_spec(args: argparse.Namespace) -> "ModelSpec | None":
    """Настройки агента с приоритетом флагов над окружением.

    Если во флаг `--scout-api-key-env` передали не имя переменной, а похожее на
    ключ значение, останавливаемся: иначе секрет уже утёк в историю оболочки, и
    молча его использовать — значит закрепить ошибку.
    """
    if not getattr(args, "agent", False):
        return None

    key_env = (getattr(args, "scout_api_key_env", None) or "").strip()
    if key_env and (len(key_env) > 64 or any(c in key_env for c in "/:@ ")):
        raise PkoError(
            "В --scout-api-key-env передано похожее на сам ключ значение.",
            "Флаг принимает ИМЯ переменной окружения, например PKO_SCOUT_API_KEY. "
            "Переданное значение уже попало в историю оболочки — отзовите ключ.",
        )

    spec = get_spec(
        "scout",
        base_url=getattr(args, "scout_base_url", None) or "",
        model=getattr(args, "scout_model", None) or "",
        api_key_env=key_env,
        allowed_hosts=getattr(args, "scout_allowed_hosts", None),
    )
    if spec is None:
        raise PkoError(
            "Агент включён, но endpoint не задан.",
            "Передайте --scout-base-url или задайте PKO_SCOUT_BASE_URL.",
        )
    return spec


def _repo_name(path: Path) -> str:
    """Имя репозитория для ссылки на реализацию: без `.git` и никогда не пустое."""
    name = path.name.removesuffix(".git")
    return name or path.parent.name or "repo"


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _gate_json(analysis: VersionAnalysis, generated_at: str) -> str:
    """Машинный вид записи допуска — контракт `BASIC_RECORD` §8.0.1."""
    return _json(_basic_record(analysis, generated_at).to_dict(generated_at))


def _basic_record(analysis: VersionAnalysis, generated_at: str) -> BasicRecord:
    """Собрать запись §8.0.1 один раз: JSON и карточка не должны расходиться."""
    return build_record(
        model=analysis.model,
        results=analysis.checks,
        decision=analysis.decision,
        intent=analysis.intent.data,
        generated_at=generated_at,
        config_files=_config_files(analysis),
        record_gaps=analysis.intent.record_gaps,
    )


# Механизмы, наблюдение в которых означает, что файл управляет поведением:
# ограничение, перечень разрешённого, параметр конфигурации, состав инструментов
# агента. Спецификации интерфейса и схемы данных сюда намеренно не входят: они
# описывают контракт, но сами по себе поведения не меняют.
_CONFIG_MECHANISMS = frozenset({"limit", "allowlist", "config", "agent_tool"})


def _config_files(analysis: VersionAnalysis) -> list[str]:
    """Файлы конфигурации из снимка реализации.

    Стандарт требует привязки решения к версии кода **и существенной
    конфигурации**: смена параметров без коммита кода тоже обесценивает запись.

    Отбор идёт по смыслу наблюдения и по виду пути, а не по одному маркеру
    `SETTING file:`. Тот маркер выпускает только `pko.extractors.deps` и только
    для YAML/TOML/INI, поэтому `config/agent.json`, задающий режим, лимиты и
    allowlist, в снимок не попадал вовсе — а запись при этом утверждала, что
    решение привязано к конфигурации. Проверка пути обязательна: `timeout=30`
    встречается и в `.py`, но исходный файл — это код, он уже покрыт коммитом.

    `business_intent.yaml` сюда не входит: это заявление владельца, а не
    конфигурация реализации, и его изменение уже перечислено отдельным условием
    инвалидирования. Смешивать их значит выдавать заявление за поведение кода.
    """
    paths: set[str] = set()
    for fact in analysis.extraction.facts:
        if fact.path in INTENT_PATHS or not fact.path:
            continue
        if fact.kind == "SETTING" and str(fact.key).startswith("file:"):
            paths.add(fact.path)
        elif fact.facets.mechanism in _CONFIG_MECHANISMS and is_config_path(fact.path):
            paths.add(fact.path)
    return sorted(paths)


# Что за файл и зачем он читателю. Ключ — имя, под которым файл действительно
# записан: ссылка на несуществующий файл хуже отсутствия ссылки.
_LINK_PURPOSE = {
    "passports.html": "паспорта объектов: картотека и подробности по клику",
    "taxonomy.html": "таксономия: полный состав объектов управления",
    "basic_gate.json": "запись допуска BASIC_RECORD с проверками и решением",
    "full_readiness.json": "готовность к промышленному контуру по областям",
    "standard_coverage.json": "какие требования стандарта PKO проверяет, а какие нет",
    "semantic_facts.json": "все наблюдения с фасетами и доказательствами",
}


def _report_links(files: dict[str, tuple[str, str]]) -> dict[str, str]:
    """Ссылки только на то, что действительно попадёт в каталог."""
    written = {name for name, _ in files.values()}
    links = {name: purpose for name, purpose in _LINK_PURPOSE.items() if name in written}
    for name in sorted(written):
        if name.startswith("comparison_") and name.endswith(".html"):
            links[name] = "сравнение версий: что изменилось между ними"
        elif name.startswith("gate_card_") and name.endswith(".md"):
            links.setdefault(name, "карточка допуска в читаемом виде")
    return links


def _packs(args) -> list[str] | None:
    """Ручной набор паков. `None` — автоопределение по стеку репозитория.

    Опечатка отклоняется здесь, а не превращается в прогон с одним ядром:
    иначе трасса записала бы пак активным, хотя в промпт он не попал.
    """
    raw = getattr(args, "agent_packs", None)
    if raw is None:
        return None
    names = [name.strip() for name in str(raw).split(",") if name.strip()]
    known = available_packs()
    unknown = [name for name in names if name not in known]
    if unknown:
        raise PkoError(
            "неизвестные паки промпта: " + ", ".join(unknown),
            hint="доступны: " + ", ".join(known),
        )
    return names


def _protect_traces(written: list) -> None:
    """Трасса содержит код анализируемой системы — доступ только владельцу.

    Общая запись комплекта идёт через `write_outputs`, поэтому права снимаются
    после неё: сам publisher не должен знать, какой из файлов конфиденциален.
    """
    for item in written:
        if item.path.name.startswith("agent_trace_"):
            harden_file(item.path)


def _trace_files(analysis: VersionAnalysis, args: argparse.Namespace) -> dict:
    """Трасса разведки — основной материал для разбора ошибок агента."""
    agent = analysis.agent
    if agent is None:
        return {}

    label = analysis.version.label
    fmt = getattr(args, "agent_trace_format", "both")
    out: dict[str, tuple[str, str]] = {}
    if fmt in {"json", "both"}:
        out[f"trace-json-{label}"] = (f"agent_trace_{label}.json", agent.trace.to_json())
    if fmt in {"html", "both"}:
        out[f"trace-html-{label}"] = (
            f"agent_trace_{label}.html", render_trace(agent.trace)
        )

    totals = agent.trace.totals()
    print(
        f"      агент: шагов {totals['steps']} · факты {totals['accepted']}/"
        f"{totals['accepted'] + totals['rejected']} · прочитано {totals['bytes_read']} Б · "
        f"стоп: {agent.trace.stop_reason}"
    )
    return out


def _print_version_summary(analysis: VersionAnalysis) -> None:
    counts = analysis.model.counts()
    decision = analysis.decision
    print(
        f"      объектов: BBB {counts['BBB']} · AO {counts['AO']} · "
        f"guardrails {counts['GUARDRAIL']} · покрытие {analysis.model.coverage.ratio:.0%}"
    )
    passed = sum(1 for c in analysis.checks if c.status == "PASS")
    failed = sum(1 for c in analysis.checks if c.status == "FAIL")
    print(f"      проверки: PASS {passed} · FAIL {failed} · решение: {decision.decision}")
    if decision.blocking:
        print(f"      блокирует: {', '.join(decision.blocking[:4])}")


if __name__ == "__main__":
    raise SystemExit(main())

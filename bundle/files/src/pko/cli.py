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
import sys
import time
from pathlib import Path

from pko import __version__
from pko.agent.trace_report import render_trace
from pko.diff.engine import diff_models
from pko.errors import PkoError
from pko.gate.decide import (
    ALLOW,
    ALLOW_WITH_RESTRICTIONS,
    DECISION_DENY,
    NO_DECISION,
    REQUIRE_FULL_CONTOUR,
)
from pko.git.remote import DEFAULT_CACHE_ROOT, ensure_mirror
from pko.git.repo import GitRepo
from pko.git.url import parse_repo_url
from pko.history.selector import select_versions
from pko.llm.registry import ModelSpec, get_spec
from pko.model.semantic import to_json as semantic_to_json
from pko.agent.loop import available_packs
from pko.output.publisher import publish, write_outputs
from pko.pipeline import VersionAnalysis, analyze_version
from pko.render.comparison import render_comparison
from pko.render.diff_md import render_diff_md
from pko.render.gate_card import render_gate_card
from pko.render.passports import render_passports
from pko.render.taxonomy import render_taxonomy
from pko.util.paths import harden_file
from pko.report.writer import write_diff_narrative, write_object_notes, write_overview

DEFAULT_OUT = Path("pko-out")


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
    analyze.add_argument("--intent", default=None, help="путь к business_intent.yaml")
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
    out_dir = Path(args.out)
    files: dict[str, tuple[str, str]] = {}
    generated_at = time.strftime("%Y-%m-%d %H:%M")

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
            intent_path=args.intent,
            assembler=assembler,
            scout=scout,
            agent_max_steps=args.agent_max_steps,
            agent_packs=packs,
        )
        if args.junit and not is_head:
            analysis.model.gaps.append(
                f"Отчёт о тестах {args.junit} относится к версии "
                f"{versions[-1].commit.short}: к этой версии он не применялся"
            )
        if args.intent and not is_head:
            # Подтверждение владельца привязано к версии. Файл, переданный флагом,
            # написан для сегодняшнего кода, и в историческом отчёте это должно быть
            # видно, иначе он выглядит как подтверждение того самого коммита.
            analysis.model.gaps.append(
                f"business_intent.yaml передан флагом --intent и относится к версии "
                f"{versions[-1].commit.short}: для этой версии подтверждение владельца "
                f"не проверялось"
            )
        analyses.append(analysis)
        _print_version_summary(analysis)
        files.update(_trace_files(analysis, args))

        overview = write_overview(analysis.model, writer)
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
            f"taxonomy_{suffix}.html", render_taxonomy(analysis.model, overview.text)
        )
        files[f"passports-{suffix}"] = (
            f"passports_{suffix}.html",
            render_passports(analysis.model, notes=object_notes, overview=overview.text),
        )
        files[f"gate-{suffix}"] = (
            f"gate_card_{suffix}.md",
            render_gate_card(analysis.model, analysis.checks, analysis.decision,
                             analysis.intent.data, generated_at),
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
    card = render_gate_card(
        analysis.model, analysis.checks, analysis.decision,
        analysis.intent.data, time.strftime("%Y-%m-%d %H:%M"),
    )
    files = {"gate": ("gate_card.md", card)}
    files.update(_trace_files(analysis, args))
    written = write_outputs(Path(args.out), files)
    _protect_traces(written)
    _print_version_summary(analysis)
    print(f"\nGate Card: {written[0].path.resolve()}")
    return _exit_code(analysis.decision.decision)


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

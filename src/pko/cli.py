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
from pko.llm.registry import get_spec
from pko.output.publisher import publish, write_outputs
from pko.pipeline import VersionAnalysis, analyze_version
from pko.render.comparison import render_comparison
from pko.render.diff_md import render_diff_md
from pko.render.gate_card import render_gate_card
from pko.render.passports import render_passports
from pko.render.taxonomy import render_taxonomy
from pko.report.writer import write_diff_narrative, write_overview

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
    repo, repo_name = _open_repo(args)
    branch = args.branch or repo.default_branch()
    versions = select_versions(repo, branch, max_versions=args.max_versions)

    assembler = get_spec("assembler") if args.llm else None
    writer = get_spec("writer") if args.llm else None
    if args.llm:
        print(f"Сборщик: {assembler.model if assembler else 'не настроен'} · "
              f"писатель: {writer.model if writer else 'не настроен'}")

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

        overview = write_overview(analysis.model, writer)
        if overview.notes:
            analysis.model.gaps.extend(overview.notes)

        suffix = version.label
        files[f"model-{suffix}"] = (f"pko_{suffix}.json", analysis.model.to_json())
        files[f"taxonomy-{suffix}"] = (
            f"taxonomy_{suffix}.html", render_taxonomy(analysis.model, overview.text)
        )
        files[f"passports-{suffix}"] = (
            f"passports_{suffix}.html", render_passports(analysis.model)
        )
        files[f"gate-{suffix}"] = (
            f"gate_card_{suffix}.md",
            render_gate_card(analysis.model, analysis.checks, analysis.decision,
                             analysis.intent.data, generated_at),
        )

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
    repo, repo_name = _open_repo(args)
    branch = args.branch or repo.default_branch()
    versions = select_versions(repo, branch, max_versions=1)
    analysis = analyze_version(
        repo=repo,
        version=versions[0],
        repo_name=repo_name,
        branch=branch,
        junit_path=args.junit,
        intent_path=args.intent,
    )
    card = render_gate_card(
        analysis.model, analysis.checks, analysis.decision,
        analysis.intent.data, time.strftime("%Y-%m-%d %H:%M"),
    )
    written = write_outputs(Path(args.out), {"gate": ("gate_card.md", card)})
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


def _repo_name(path: Path) -> str:
    """Имя репозитория для ссылки на реализацию: без `.git` и никогда не пустое."""
    name = path.name.removesuffix(".git")
    return name or path.parent.name or "repo"


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

"""Командная строка PKO.

    pko progress plan.pptx ssh://git@<bitbucket-host>:7999/<project>/<repo>.git
    pko progress plan.pptx --repo-path ~/src/<repo> --no-fetch
    pko serve   # веб-интерфейс на http://127.0.0.1:8000 — тот же пайплайн, без CLI

Клон делается от вашего имени по SSH, зеркалом, без рабочего дерева. Отчёт о
прогрессе (`progress_report.html`, `progress_model.json`) пишется в каталог
`pko-progress-out`.

Код возврата: 0 — отчёт собран; 1 — ошибка запуска (нет доступа, план не
разобран, не настроен LLM); 2 — ошибка синтаксиса CLI, которую diagnostировал
argparse до запуска.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pko import __version__
from pko.errors import PkoError
from pko.git.remote import DEFAULT_CACHE_ROOT, ensure_mirror
from pko.git.repo import GitRepo
from pko.git.url import parse_repo_url
from pko.llm.registry import get_spec
from pko.output.publisher import write_outputs
from pko.progress.pipeline import run_progress
from pko.progress.target_repo import load_target, repo_name
from pko.render.progress_report import render_progress_report

DEFAULT_PROGRESS_OUT = Path("pko-progress-out")
DEFAULT_SERVE_HOST = "127.0.0.1"
DEFAULT_SERVE_PORT = 8000


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
        description="Сравнение PPTX-плана команды с кодом репозитория",
    )
    parser.add_argument("--version", action="version", version=f"pko {__version__}")
    sub = parser.add_subparsers(dest="command")

    progress = sub.add_parser(
        "progress", help="сравнить PPTX-план команды с кодом репозитория"
    )
    progress.add_argument("plan", help="путь к .pptx с планом команды")
    _add_source_args(progress)
    progress.add_argument("--out", default=str(DEFAULT_PROGRESS_OUT),
                          help="каталог для отчёта о прогрессе")
    progress.set_defaults(handler=cmd_progress)

    serve = sub.add_parser("serve", help="поднять локальный веб-интерфейс")
    serve.add_argument("--host", default=DEFAULT_SERVE_HOST,
                       help=f"по умолчанию {DEFAULT_SERVE_HOST} — только эта машина")
    serve.add_argument("--port", type=int, default=DEFAULT_SERVE_PORT)
    serve.set_defaults(handler=cmd_serve)

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


# --- команды -----------------------------------------------------------
def cmd_progress(args: argparse.Namespace) -> int:
    """Сравнить PPTX-план команды с кодом репозитория и выпустить отчёт о прогрессе.

    Логика самого пайплайна — в `pko.progress.pipeline.run_progress`; её же
    зовёт веб-эндпоинт (`pko.web.app`), чтобы CLI и веб не могли разойтись в
    поведении. Здесь — только сборка входа, печать и запись на диск.
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

    repo, name = _open_repo(args)
    branch = args.branch or repo.default_branch()
    target = load_target(repo, branch)
    print(f"Репозиторий: {name} · ветка: {target.branch} · коммит {target.sha[:8]}")
    print(f"План: {plan_path.name}")

    model = run_progress(plan_path, name, target, planner, matcher_spec)
    for gap in model.gaps:
        print(f"  {gap}")
    print(f"Пунктов плана: {len(model.items)}")

    files = {
        "progress-model": ("progress_model.json", model.to_json()),
        "progress-report": ("progress_report.html", render_progress_report(model)),
    }
    written = write_outputs(Path(args.out), files)
    report_path = next(w.path for w in written if w.path.name == "progress_report.html")
    print(f"\nОтчёт о прогрессе: {report_path.resolve()}")
    print(f"Прогресс: {model.progress_ratio():.0%} пунктов сделано ({len(model.verdicts)} всего)")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Поднять локальный веб-интерфейс. `uvicorn` импортируется здесь же —

    `pko progress` не должен тянуть веб-стек ради своей работы, тот же
    принцип, что и у отложенного импорта `python-pptx`.
    """
    import uvicorn

    print(f"PKO progress: http://{args.host}:{args.port}")
    uvicorn.run("pko.web.app:app", host=args.host, port=args.port)
    return 0


# --- вспомогательное -----------------------------------------------------
def _open_repo(args: argparse.Namespace) -> tuple[GitRepo, str]:
    """Открыть репозиторий: по SSH-ссылке через зеркало или по локальному пути."""
    if args.repo_path:
        # Путь приводится к абсолютному до взятия имени: у `.` и `..` имени нет,
        # и в ссылку на реализацию попадало бы пустое значение — `@<sha>` вместо
        # `repo@<sha>`, хотя точная ссылка и есть смысл отчёта.
        path = Path(args.repo_path).expanduser().resolve()
        return GitRepo(path, timeout=args.network_timeout), repo_name(path)

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


if __name__ == "__main__":
    raise SystemExit(main())

"""Командная строка PKO.

    pko progress plan.pptx ssh://git@<bitbucket-host>:7999/<project>/<repo>.git
    pko progress plan.pptx --repo-path ~/src/<repo> --no-fetch

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
import time
from pathlib import Path

from pko import __version__
from pko.errors import PkoError
from pko.git.remote import DEFAULT_CACHE_ROOT, ensure_mirror
from pko.git.repo import GitRepo
from pko.git.url import parse_repo_url
from pko.llm.registry import get_spec
from pko.output.publisher import write_outputs
from pko.progress.matcher import find_unclaimed_paths, match_plan
from pko.progress.schema import ProgressModel
from pko.progress.target_repo import load_target
from pko.render.progress_report import render_progress_report

DEFAULT_PROGRESS_OUT = Path("pko-progress-out")


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


# --- команда -----------------------------------------------------------
def cmd_progress(args: argparse.Namespace) -> int:
    """Сравнить PPTX-план команды с кодом репозитория и выпустить отчёт о прогрессе.

    `python-pptx` устанавливается вместе с пакетом (`dependencies` в
    `pyproject.toml`), но импорт `pptx_reader`/`plan_extract` всё равно
    отложен до вызова этой команды — единственной в CLI, поэтому граница
    условна, но привычка «тяжёлая зависимость не должна ломать --help» того
    стоит.
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
            hint="поставьте пакет: pip install python-pptx>=1.0",
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


# --- вспомогательное -----------------------------------------------------
def _open_repo(args: argparse.Namespace) -> tuple[GitRepo, str]:
    """Открыть репозиторий: по SSH-ссылке через зеркало или по локальному пути."""
    if args.repo_path:
        # Путь приводится к абсолютному до взятия имени: у `.` и `..` имени нет,
        # и в ссылку на реализацию попадало бы пустое значение — `@<sha>` вместо
        # `repo@<sha>`, хотя точная ссылка и есть смысл отчёта.
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


if __name__ == "__main__":
    raise SystemExit(main())

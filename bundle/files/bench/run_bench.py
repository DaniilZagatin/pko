#!/usr/bin/env python3
"""Прогон агента разведки по набору целей с сохранением трасс.

Смысл не в метриках, а в материале для разбора: после прогона в
`bench/runs/<дата>_<модель>/<цель>/` лежит трасса в JSON и HTML, модель, Gate
Card и `metrics.json` со счётчиками для контекста. Ошибку агента ищут в трассе,
метрики лишь помогают сравнить прогоны между собой.

Одна модель за прогон — сравнение делает `bench/report.py` постфактум.

    python3 bench/run_bench.py --scout-base-url https://... --scout-model GLM-5.2 \
        --scout-api-key-env PKO_SCOUT_API_KEY
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pko.agent.trace_report import render_trace  # noqa: E402
from pko.errors import PkoError  # noqa: E402
from pko.git.repo import GitRepo  # noqa: E402
from pko.history.selector import select_versions  # noqa: E402
from pko.llm.registry import get_spec  # noqa: E402
from pko.pipeline import analyze_version  # noqa: E402
from pko.render.gate_card import render_gate_card  # noqa: E402
from pko.util.paths import harden_file  # noqa: E402
from pko.util.yamlmini import loads  # noqa: E402

DEFAULT_TARGETS = Path(__file__).with_name("targets.yaml")
RUNS_DIR = Path(__file__).with_name("runs")


def main(argv: list[str] | None = None) -> int:
    """Точка входа. Ошибки PKO печатаются так же, как в `pko.cli`.

    Подсказки вроде «экспортируйте ключ перед запуском» имеют смысл только
    показанными; трассой стека они не читаются.
    """
    try:
        return _main(_parse_args(argv))
    except PkoError as exc:
        print(f"Ошибка: {exc.render()}", file=sys.stderr)
        return 1


def _main(args: argparse.Namespace) -> int:
    spec = get_spec(
        "scout",
        base_url=args.scout_base_url or "",
        model=args.scout_model or "",
        api_key_env=args.scout_api_key_env or "",
        allowed_hosts=args.scout_allowed_hosts,
    )
    if spec is None:
        print("Endpoint агента не задан: передайте --scout-base-url или PKO_SCOUT_BASE_URL",
              file=sys.stderr)
        return 1

    targets = _load_targets(Path(args.targets))
    if not targets:
        print(f"В {args.targets} нет ни одной цели", file=sys.stderr)
        return 1

    # Секунды в метке и счётчик-суффикс: два прогона одной модели в пределах
    # минуты писали в один каталог и затирали трассы друг друга.
    stamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir = _fresh_dir(RUNS_DIR / f"{stamp}_{_slug(spec.model)}")
    run_dir.mkdir(parents=True)
    print(f"Прогон: {spec.model} @ {spec.base_url}\nКаталог: {run_dir}\n")

    summary = []
    failed = 0
    for target in targets:
        print(f"— цель {target.get('name')}")
        try:
            summary.append(_run_target(target, spec, run_dir, args.agent_max_steps))
        except PkoError as exc:
            print(f"  ошибка: {exc.render()}", file=sys.stderr)
            summary.append({"target": target.get("name"), "error": exc.message})
            failed += 1

    (run_dir / "summary.json").write_text(
        json.dumps({"model": spec.model, "endpoint": spec.base_url, "targets": summary},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if failed:
        print(f"\nЗавершено с ошибками: {failed} из {len(targets)} целей.", file=sys.stderr)
        return 1
    print(f"\nГотово. Смотреть трассы: {run_dir}/*/agent_trace_*.html")
    return 0


def _run_target(target: dict, spec, run_dir: Path, max_steps: int) -> dict:
    # Имя цели приходит из YAML и становится каталогом: без очистки цель
    # `../other-run` писала бы за пределы каталога прогона.
    name = _slug(str(target.get("name") or "target")).strip("-.") or "target"
    repo_path = (ROOT / str(target.get("repo_path", "."))).resolve()
    _prepare_repo(target, repo_path)
    out_dir = run_dir / name
    out_dir.mkdir(parents=True, exist_ok=True)

    repo = GitRepo(repo_path)
    branch = str(target.get("branch") or repo.default_branch())
    versions = select_versions(repo, branch, max_versions=int(target.get("max_versions", 1)))
    version = versions[-1]

    intent = target.get("intent")
    started = time.perf_counter()
    analysis = analyze_version(
        repo=repo,
        version=version,
        repo_name=repo_path.name,
        branch=branch,
        intent_path=str(ROOT / intent) if intent else None,
        scout=spec,
        agent_max_steps=max_steps,
    )
    elapsed = time.perf_counter() - started

    agent = analysis.agent
    trace = agent.trace if agent else None

    (out_dir / "pko_model.json").write_text(analysis.model.to_json(), encoding="utf-8")
    (out_dir / "gate_card.md").write_text(
        render_gate_card(analysis.model, analysis.checks, analysis.decision,
                         analysis.intent.data, time.strftime("%Y-%m-%d %H:%M")),
        encoding="utf-8",
    )
    if trace is not None:
        trace.save(out_dir / "agent_trace.json")
        html = out_dir / "agent_trace.html"
        html.write_text(render_trace(trace), encoding="utf-8")
        # HTML содержит тот же прочитанный код, что и JSON, — права те же.
        # `trace.save` защищает только JSON, поэтому здесь это делается явно.
        harden_file(html)

    metrics = {
        "target": name,
        "repo": repo_path.name,
        "commit": version.sha,
        "branch": branch,
        "model": spec.model,
        "endpoint": spec.base_url,
        "prompt_version": trace.prompt_version if trace else "",
        "prompt_sha": trace.prompt_sha if trace else "",
        "wall_seconds": round(elapsed, 1),
        "decision": analysis.decision.decision,
        "counts": analysis.model.counts(),
        "coverage_ratio": round(analysis.model.coverage.ratio, 3),
        "unknown_ratio": round(analysis.model.unknown_ratio(), 3),
        "gaps": len(analysis.model.gaps),
        "agent": trace.totals() if trace else {},
        "stop_reason": trace.stop_reason if trace else "",
        "incomplete": trace.incomplete if trace else None,
        # Универсализация мерится не числом объектов, а полнотой картины:
        # сколько категорий и механизмов удалось восстановить и какая доля
        # наблюдений подтверждена настолько, чтобы влиять на вердикт.
        "packs": trace.packs if trace else [],
        "semantics": _semantics(analysis),
    }
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    totals = metrics["agent"]
    print(f"  шагов {totals.get('steps', 0)} · факты "
          f"{totals.get('accepted', 0)}/{totals.get('accepted', 0) + totals.get('rejected', 0)}"
          f" · BBB {metrics['counts']['BBB']} · решение {metrics['decision']}")
    return metrics


def _prepare_repo(target: dict, repo_path: Path) -> None:
    """Создать синтетическую цель, если clean checkout ещё её не содержит."""
    if (repo_path / ".git").exists():
        return
    setup = str(target.get("setup_script") or "").strip()
    if not setup:
        raise PkoError(f"Цель benchmark не является git-репозиторием: {repo_path}")
    script = (ROOT / setup).resolve()
    try:
        script.relative_to(ROOT)
    except ValueError as exc:
        raise PkoError(f"setup_script выходит за пределы проекта: {setup}") from exc
    if not script.is_file():
        raise PkoError(f"Не найден setup_script цели benchmark: {script}")
    try:
        proc = subprocess.run(
            ["bash", str(script)], cwd=ROOT, capture_output=True, text=True, errors="replace",
        )
    except OSError as exc:
        raise PkoError(f"Не удалось запустить {script}: {exc}") from exc
    if proc.returncode != 0 or not (repo_path / ".git").exists():
        detail = (proc.stderr or proc.stdout).strip()[:500]
        raise PkoError(
            f"setup_script не создал цель {repo_path} (код {proc.returncode}): {detail}"
        )


def _semantics(analysis) -> dict:
    """Срез по фасетам: что именно PKO сумел увидеть на этой цели."""
    facts = analysis.extraction.facts
    by_category: dict[str, int] = {}
    by_mechanism: dict[str, int] = {}
    for fact in facts:
        facets = fact.facets
        by_category[facets.category] = by_category.get(facets.category, 0) + 1
        key = facets.mechanism or "—"
        by_mechanism[key] = by_mechanism.get(key, 0) + 1
    eligible = sum(1 for f in facts if f.gate_eligible)
    stack = analysis.agent.stack if analysis.agent else None
    return {
        "facts": len(facts),
        "gate_eligible": eligible,
        "confirmed_share": round(eligible / len(facts), 3) if facts else 0.0,
        "categories": dict(sorted(by_category.items())),
        "mechanisms": dict(sorted(by_mechanism.items())),
        "unparsed_files": stack.unparsed_files if stack else 0,
        "unparsed_languages": stack.unparsed_languages if stack else [],
        "coverage_ratio": round(analysis.model.coverage.ratio, 3),
    }


def _load_targets(path: Path) -> list[dict]:
    """Цели из YAML: ключ верхнего уровня — имя цели, значение — её поля.

    Список словарей мини-парсер не разбирает, поэтому цели заданы вложенным
    словарём. Имя берётся из ключа, дублировать его внутри не нужно.
    """
    data = loads(path.read_text(encoding="utf-8"))
    targets = data.get("targets") if isinstance(data, dict) else None
    if not isinstance(targets, dict):
        return []
    return [
        {"name": name, **fields}
        for name, fields in targets.items()
        if isinstance(fields, dict)
    ]


def _slug(value: str) -> str:
    """Имя, безопасное как один элемент пути: без разделителей и без «..»."""
    cleaned = "".join(c if c.isalnum() or c in "-_." else "-" for c in value)
    return cleaned.replace("..", "-")


def _fresh_dir(base: Path) -> Path:
    """Каталог, которого ещё нет: суффикс -2, -3 при совпадении секунды."""
    if not base.exists():
        return base
    for suffix in range(2, 100):
        candidate = base.with_name(f"{base.name}-{suffix}")
        if not candidate.exists():
            return candidate
    raise PkoError(f"слишком много прогонов с именем {base.name}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Прогон агента разведки по целям бенчмарка")
    parser.add_argument("--targets", default=str(DEFAULT_TARGETS))
    parser.add_argument("--scout-base-url", default=None)
    parser.add_argument("--scout-model", default=None)
    parser.add_argument("--scout-api-key-env", default=None,
                        help="ИМЯ переменной окружения с ключом, не сам ключ")
    parser.add_argument("--scout-allowed-hosts", default=None,
                        help="разрешённые внутренние hosts через запятую")
    parser.add_argument("--agent-max-steps", type=int, default=0)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())

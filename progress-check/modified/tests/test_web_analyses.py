"""Веб API `pko progress`: `POST /api/analyses` -> SSE-прогресс -> `GET /api/analyses/{id}`.

Заменяет собой прежний `test_web_app.py` (тестировал синхронный `POST
/api/progress`+`GET /` — оба маршрута удалены вместе со старой страницей
`frontend/index.html`, см. README/план). `TestClient` синхронный, без реального
порта; фоновый поток задачи (`web.analyses._execute`) успевает завершиться до
того, как `GET .../events` дочитает поток до конца — тест только это и ждёт.

LLM подменяется тем же способом, что и в `test_progress_cli.py`: скриптованный
`ChatClient._request` + `DEFAULT_CACHE_DIR` во временном каталоге.
"""

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import pko.llm.client as client_module
import pko.web.app as app_module
from fixture_support import ensure_fixture
from pko.llm.client import ChatClient
from pko.web import analyses
from pko.web.app import app
from test_progress_pptx import build_sample_deck

ENV_KEYS = ("PKO_ASSEMBLER_BASE_URL", "PKO_ASSEMBLER_MODEL", "PKO_ASSEMBLER_API_KEY")


def _tool_call(call_id: str, name: str, **args) -> dict:
    return {"content": None, "tool_calls": [{
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }]}


def scripted(*answers):
    """Каждый элемент — либо голая строка (`.complete()`: reporter), либо
    готовый словарь-сообщение (`.chat()`: агентный matcher)."""
    queue = list(answers)

    def _request(self, method, path, payload):
        item = queue.pop(0) if queue else json.dumps({})
        message = {"content": item} if isinstance(item, str) else item
        return {"choices": [{"message": message}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    return _request


def _parse_sse(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


class WebAnalysesTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plan_path = Path(self.tmp.name) / "plan.pptx"
        build_sample_deck(self.plan_path)

        self._original_cache_dir = client_module.DEFAULT_CACHE_DIR
        client_module.DEFAULT_CACHE_DIR = Path(self.tmp.name) / "llm-cache"
        self.addCleanup(setattr, client_module, "DEFAULT_CACHE_DIR", self._original_cache_dir)

        self._original_request = ChatClient._request
        self.addCleanup(setattr, ChatClient, "_request", self._original_request)

        self._original_env = {k: os.environ.get(k) for k in ENV_KEYS + ("PKO_DATA_DIR",)}
        os.environ.update({
            "PKO_ASSEMBLER_BASE_URL": "https://stub.local/v1",
            "PKO_ASSEMBLER_MODEL": "stub-model",
            "PKO_ASSEMBLER_API_KEY": "x",
            # Продуктовое хранилище (pko.store) — во временный каталог, не в
            # реальный ~/.pko: тесты не должны читать/писать данные пользователя.
            "PKO_DATA_DIR": str(Path(self.tmp.name) / "pko-data"),
        })

        def _restore_env():
            for k, v in self._original_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        self.addCleanup(_restore_env)

    def _create(self, repository: str = "", branch: str = "", plan_path: Path | None = None,
                extra_files: dict[str, bytes] | None = None, product_id: str = ""):
        plan_path = plan_path or self.plan_path
        with open(plan_path, "rb") as fh:
            upload_files = [("presentation", (plan_path.name, fh, "application/octet-stream"))]
            for name, data in (extra_files or {}).items():
                upload_files.append(("files", (name, data, "application/octet-stream")))
            return self.client.post(
                "/api/analyses",
                files=upload_files,
                data={"repository": repository, "branch": branch, "product_id": product_id},
            )

    def test_full_run_streams_events_then_ready_analysis(self):
        submit_answer = _tool_call(
            "call_submit", "submit_verdict",
            item_id="tasks-api", title="API постановки задач", source_slide=2,
            status="DONE", explanation="Эндпоинт постановки задачи реализован.",
            evidence=[{"path": "backend/src/api/v1/router.py", "line": 7,
                      "basis": "функция start_task ставит задачу в обработку"}],
        )
        finish_answer = _tool_call("call_finish", "finish")
        summary_answer = "Задача постановки задач реализована и подтверждена кодом."
        ChatClient._request = scripted(submit_answer, finish_answer, summary_answer)

        create_resp = self._create(repository=str(ensure_fixture()))
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        body = create_resp.json()
        self.assertEqual(body["status"], "PROCESSING")
        analysis_id = body["analysis_id"]

        events_resp = self.client.get(f"/api/analyses/{analysis_id}/events")
        self.assertEqual(events_resp.status_code, 200)
        events = _parse_sse(events_resp.text)
        self.assertEqual([e["type"] for e in events], [
            "phase", "phase", "presentation_parsed", "claim_verified", "summarizing",
            "analysis_ready",
        ])
        self.assertEqual(events[0]["phase"], "materials_loading")
        self.assertEqual(events[1]["phase"], "materials_ready")
        self.assertEqual(events[3], {
            "type": "claim_verified", "title": "API постановки задач", "status": "DONE",
        })

        get_resp = self.client.get(f"/api/analyses/{analysis_id}")
        self.assertEqual(get_resp.status_code, 200)
        result = get_resp.json()
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["counts"]["DONE"], 1)
        self.assertEqual(result["readiness"], 1.0)
        [item] = result["items"]
        self.assertEqual(item["title"], "API постановки задач")
        self.assertEqual(item["status"], "DONE")
        self.assertEqual(item["label"], "Сделано")
        self.assertEqual(item["color"], "green")
        self.assertEqual(item["pct"], 100)
        self.assertEqual(item["explanation"], "Эндпоинт постановки задачи реализован.")
        # Бизнес-контракт: описание задачи есть (фолбэк на title), но никаких
        # путей к файлам/тестов/технических деталей в этом JSON нет вообще.
        self.assertIn("description", item)
        self.assertNotIn("evidence", item)
        self.assertNotIn("backend/src/api/v1/router.py", get_resp.text)

    def test_events_stream_sends_heartbeat_while_agent_is_slow(self):
        # Реальный шаг агента (не скриптованный LLM в этих тестах) может
        # занимать до ChatClient.timeout (120с) — без heartbeat простаивающее
        # SSE-соединение рискует быть закрытым прокси с более коротким
        # idle-таймаутом. Здесь эмулируем "медленный шаг" напрямую через
        # AnalysisJob, без реального пайплайна: интервал heartbeat уменьшен,
        # событие приходит позже него, но раньше, чем тест устанет ждать.
        job = analyses.AnalysisJob(id="an_slow_test")
        analyses._JOBS[job.id] = job
        self.addCleanup(analyses._JOBS.pop, job.id, None)

        original_heartbeat = app_module._HEARTBEAT_SECONDS
        app_module._HEARTBEAT_SECONDS = 0.05
        self.addCleanup(setattr, app_module, "_HEARTBEAT_SECONDS", original_heartbeat)

        def deliver_after_delay():
            time.sleep(0.2)
            job.events.put({"type": "analysis_ready"})

        threading.Thread(target=deliver_after_delay, daemon=True).start()

        resp = self.client.get(f"/api/analyses/{job.id}/events")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(": keepalive\n\n", resp.text)
        events = _parse_sse(resp.text)
        self.assertEqual(events, [{"type": "analysis_ready"}])

    def test_files_only_analysis_runs_without_any_repository(self):
        submit_answer = _tool_call(
            "call_submit", "submit_verdict",
            item_id="model", title="Обучение модели", source_slide=1,
            status="DONE", explanation="Целевая метрика достигнута.",
        )
        ChatClient._request = scripted(submit_answer, _tool_call("call_finish", "finish"))

        create_resp = self._create(extra_files={"metrics.json": b'{"roc_auc": 0.88}'})
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        analysis_id = create_resp.json()["analysis_id"]

        events = _parse_sse(self.client.get(f"/api/analyses/{analysis_id}/events").text)
        self.assertEqual(events[-1]["type"], "analysis_ready")
        self.assertEqual(events[0]["phase"], "materials_loading")

        result = self.client.get(f"/api/analyses/{analysis_id}").json()
        self.assertEqual(result["status"], "READY")
        # Без репозитория meta["repo"] пусто — фронт уже показывает "Проект"
        # как фолбэк (DashboardHeader.tsx), падать здесь не должно.
        self.assertEqual(result["meta"]["repo"], "")

    def test_repository_and_files_together_are_merged_into_one_analysis(self):
        submit_answer = _tool_call(
            "call_submit", "submit_verdict",
            item_id="tasks-api", title="API постановки задач", source_slide=2,
            status="DONE", explanation="Реализовано и подтверждено метриками.",
            evidence=[{"path": "metrics.json", "line": 1, "basis": "roc_auc в metrics.json"}],
        )
        ChatClient._request = scripted(submit_answer, _tool_call("call_finish", "finish"))

        create_resp = self._create(
            repository=str(ensure_fixture()),
            extra_files={"metrics.json": b'{"roc_auc": 0.9}'},
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        analysis_id = create_resp.json()["analysis_id"]
        self.client.get(f"/api/analyses/{analysis_id}/events")  # дождаться завершения

        result = self.client.get(f"/api/analyses/{analysis_id}").json()
        self.assertEqual(result["status"], "READY")
        # Репозиторий остался источником имени/коммита — файлы дополняют, не подменяют его.
        self.assertNotEqual(result["meta"]["repo"], "")

    def test_neither_repository_nor_files_still_runs_against_an_empty_workspace(self):
        # Не ошибка запроса: агент получает пустой снимок материалов и сам
        # решает, что писать в вердикт — здесь он честно отчитывается, что
        # подтверждения не нашёл (тот же путь, что и "в репозитории пусто").
        submit_answer = _tool_call(
            "call_submit", "submit_verdict",
            item_id="tasks-api", title="API постановки задач", source_slide=2,
            status="NOT_STARTED", explanation="Подтверждающих материалов не предоставлено.",
        )
        ChatClient._request = scripted(submit_answer, _tool_call("call_finish", "finish"))

        create_resp = self._create()
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        analysis_id = create_resp.json()["analysis_id"]

        events = _parse_sse(self.client.get(f"/api/analyses/{analysis_id}/events").text)
        self.assertEqual(events[-1]["type"], "analysis_ready")

        result = self.client.get(f"/api/analyses/{analysis_id}").json()
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["meta"]["repo"], "")
        [item] = result["items"]
        self.assertEqual(item["status"], "NOT_STARTED")

    def test_analysis_with_product_id_is_persisted_as_snapshot(self):
        product_id = self.client.post("/api/products", data={"name": "Демо-продукт"}).json()["id"]

        submit_answer = _tool_call(
            "call_submit", "submit_verdict",
            item_id="tasks-api", title="API постановки задач", source_slide=2,
            status="DONE", explanation="Реализовано и подтверждено кодом.",
            evidence=[{"path": "backend/src/api/v1/router.py", "line": 7,
                      "basis": "функция start_task ставит задачу в обработку"}],
        )
        ChatClient._request = scripted(submit_answer, _tool_call("call_finish", "finish"))

        create_resp = self._create(repository=str(ensure_fixture()), product_id=product_id)
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        analysis_id = create_resp.json()["analysis_id"]
        self.client.get(f"/api/analyses/{analysis_id}/events")  # дождаться завершения

        result = self.client.get(f"/api/analyses/{analysis_id}").json()
        self.assertEqual(result["product_id"], product_id)
        self.assertTrue(result["snapshot_id"].startswith("snap_"))
        self.assertEqual(result["version_number"], 1)

        snapshots = self.client.get(f"/api/products/{product_id}/snapshots").json()
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["id"], result["snapshot_id"])
        self.assertIn("repo", snapshots[0]["source"])

        dashboard = self.client.get(
            f"/api/products/{product_id}/snapshots/{result['snapshot_id']}"
        ).json()
        self.assertEqual(dashboard["items"][0]["title"], "API постановки задач")

    def test_analysis_with_unknown_product_id_is_rejected_before_job_creation(self):
        resp = self._create(repository=str(ensure_fixture()), product_id="prod_doesnotexist")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Продукт не найден", resp.json()["message"])

    def test_unknown_analysis_id_is_a_clean_400_not_a_500(self):
        resp = self.client.get("/api/analyses/an_doesnotexist")
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(resp.json()["message"])

        events_resp = self.client.get("/api/analyses/an_doesnotexist/events")
        self.assertEqual(events_resp.status_code, 400)

    def test_non_pptx_filename_is_rejected_synchronously(self):
        bad = Path(self.tmp.name) / "plan.txt"
        bad.write_text("не презентация", encoding="utf-8")
        resp = self._create(repository=str(ensure_fixture()), plan_path=bad)
        self.assertEqual(resp.status_code, 400)
        self.assertIn(".pptx", resp.json()["message"])

    def test_missing_llm_config_is_a_clean_400_before_any_job_is_created(self):
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        resp = self._create(repository=str(ensure_fixture()))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("LLM", resp.json()["message"])

    def test_nonexistent_repo_surfaces_as_error_event_and_error_status(self):
        ChatClient._request = scripted(json.dumps({}))
        missing = str(Path(self.tmp.name) / "no-such-repo")
        analysis_id = self._create(repository=missing).json()["analysis_id"]

        events = _parse_sse(self.client.get(f"/api/analyses/{analysis_id}/events").text)
        self.assertEqual(events[-1]["type"], "error")
        self.assertTrue(events[-1]["message"])

        result = self.client.get(f"/api/analyses/{analysis_id}")
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.json()["status"], "ERROR")


if __name__ == "__main__":
    unittest.main()

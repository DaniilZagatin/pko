"""Роль planner: JSON-ответ модели → список PlanItem, без сети.

Транспорт подменяется так же, как в `test_agent.py`: `ChatClient._request`
отдаёт заранее заготовленный ответ. Живой endpoint для этого не нужен —
проверяется только логика разбора и заземления (grounding) на входные слайды.
"""

import json
import unittest

from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec
from pko.progress.plan_extract import extract_plan
from pko.progress.pptx_reader import Slide, SlideShape

SPEC = ModelSpec(role="planner", base_url="https://stub.local/v1", model="stub-model", api_key="x")

SLIDES = [
    Slide(number=1, heading="Задачи спринта", shapes=[
        SlideShape(text="Авторизация пользователей / OAuth2 + JWT",
                   left=0.5, top=1.2, width=4.0, height=2.2),
        SlideShape(text="API платежей / REST эндпоинты",
                   left=4.8, top=1.2, width=4.0, height=2.2),
    ]),
    Slide(number=2, heading="Таймлайн разработки", shapes=[
        SlideShape(text="Этап 1: MVP / июль", left=0.5, top=2.5, width=2.9, height=1.6),
        SlideShape(text="Этап 2: Уведомления / август", left=3.6, top=2.5, width=2.9, height=1.6),
    ]),
]


def scripted(*answers: str):
    queue = list(answers)

    def _request(self, method, path, payload):
        text = queue.pop(0) if queue else json.dumps({"items": []})
        return {"choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    return _request


class PlanExtractTest(unittest.TestCase):
    def setUp(self):
        # Кеш выключен и не разделяется между тестами: `complete()` иначе читал
        # бы ответ на совпавший payload из реального `~/.pko/llm-cache`, минуя
        # заглушку `_request` этого конкретного теста.
        self._original = ChatClient._request
        self.addCleanup(setattr, ChatClient, "_request", self._original)
        self.client = ChatClient(spec=SPEC, use_cache=False)

    def _run(self, response_text: str, slides=SLIDES):
        ChatClient._request = scripted(response_text)
        return extract_plan(slides, SPEC, client=self.client)

    def test_no_spec_returns_empty_with_note(self):
        result = extract_plan(SLIDES, spec=None)
        self.assertFalse(result.usable)
        self.assertEqual(result.source, "none")
        self.assertIn("не настроен", result.notes[0])

    def test_no_content_slides_returns_empty_with_note(self):
        empty_slide = Slide(number=1, heading=None, shapes=[])
        result = extract_plan([empty_slide], SPEC, client=self.client)
        self.assertFalse(result.usable)
        self.assertIn("нет текстовых фигур", result.notes[0])

    def test_valid_items_pass_through(self):
        response = json.dumps({"items": [
            {"id": "auth", "title": "Авторизация пользователей", "stage": "MVP",
             "description": "OAuth2 + JWT", "source_slide": 1},
            {"id": "timeline-1", "title": "Этап 1: MVP", "source_slide": 2},
        ]})
        result = self._run(response)
        self.assertEqual(result.source, "llm")
        self.assertEqual(len(result.items), 2)
        self.assertEqual(result.items[0].id, "auth")
        self.assertEqual(result.items[0].source_slide, 1)

    def test_item_referencing_unknown_slide_is_dropped(self):
        # Заземление: planner не должен ссылаться на слайд, которого не было
        # во входе — это тот же принцип, что и known_ids в assemble/llm_map.py.
        response = json.dumps({"items": [
            {"id": "ghost", "title": "Выдуманная задача", "source_slide": 99},
            {"id": "auth", "title": "Авторизация пользователей", "source_slide": 1},
        ]})
        result = self._run(response)
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].id, "auth")
        self.assertIn("Отброшено пунктов", result.notes[0])

    def test_item_without_title_is_dropped(self):
        response = json.dumps({"items": [{"id": "x", "source_slide": 1}]})
        result = self._run(response)
        self.assertFalse(result.usable)

    def test_duplicate_ids_get_disambiguated(self):
        response = json.dumps({"items": [
            {"id": "dup", "title": "Первая задача", "source_slide": 1},
            {"id": "dup", "title": "Вторая задача", "source_slide": 1},
        ]})
        result = self._run(response)
        self.assertEqual(len(result.items), 2)
        ids = {item.id for item in result.items}
        self.assertEqual(len(ids), 2)

    def test_missing_id_gets_generated(self):
        response = json.dumps({"items": [{"title": "Без id", "source_slide": 1}]})
        result = self._run(response)
        self.assertEqual(len(result.items), 1)
        self.assertTrue(result.items[0].id)

    def test_non_json_response_returns_empty_with_note(self):
        result = self._run("извините, не могу помочь")
        self.assertFalse(result.usable)
        self.assertIn("не является JSON", result.notes[0])

    def test_empty_items_list_returns_empty_with_note(self):
        result = self._run(json.dumps({"items": []}))
        self.assertFalse(result.usable)
        self.assertIn("Годных пунктов", result.notes[0])


if __name__ == "__main__":
    unittest.main()

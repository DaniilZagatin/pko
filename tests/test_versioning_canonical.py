"""Матчинг canonical_stage_id: точное совпадение, fuzzy, LLM-остаток.

LLM подменяется тем же способом, что и в `test_web_analyses.py`: скриптованный
`ChatClient._request` + временный `DEFAULT_CACHE_DIR` (иначе `.complete()`
читает/пишет реальный `~/.pko/llm-cache`).
"""

import json
import tempfile
import unittest
from pathlib import Path

import pko.llm.client as client_module
from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec
from pko.progress.schema import ItemVerdict, PlanItem, ProgressModel
from pko.store import canonical as canonical_store
from pko.store import products
from pko.versioning.canonical import assign_canonical_ids, normalize

_SPEC = ModelSpec(role="matcher", base_url="https://stub.local/v1", model="stub-model", api_key="x")


def _model(*items: tuple[str, str]) -> ProgressModel:
    """items: (item_id, title) — статус/progress не важны для матчинга."""
    plan_items = {item_id: PlanItem(id=item_id, title=title, source_slide=1) for item_id, title in items}
    verdicts = [ItemVerdict(item_id=item_id, status="PARTIAL", explanation="", progress=50)
                for item_id, _ in items]
    return ProgressModel(items=plan_items, verdicts=verdicts)


def _scripted(*answers: str):
    queue = list(answers)

    def _request(self, method, path, payload):
        content = queue.pop(0) if queue else "[]"
        return {"choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    return _request


class CanonicalMatchingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "store.db"
        self.product = products.create_product("Продукт", db_path=self.db_path)

        self._original_cache_dir = client_module.DEFAULT_CACHE_DIR
        client_module.DEFAULT_CACHE_DIR = Path(self.tmp.name) / "llm-cache"
        self.addCleanup(setattr, client_module, "DEFAULT_CACHE_DIR", self._original_cache_dir)

        self._original_request = ChatClient._request
        self.addCleanup(setattr, ChatClient, "_request", self._original_request)

    def _assign(self, model: ProgressModel, spec=None) -> None:
        assign_canonical_ids(self.product.id, model, spec, db_path=self.db_path)

    def _stages(self):
        return canonical_store.list_stages(self.product.id, db_path=self.db_path)

    def test_first_snapshot_creates_new_stages_without_any_llm_call(self):
        ChatClient._request = _scripted()  # если вызовется — вернёт "[]"; но не должен вызваться
        model = _model(("a", "Первичный контакт"), ("b", "Сборка предложения"))
        self._assign(model, spec=None)

        ids = [v.canonical_stage_id for v in model.verdicts]
        self.assertEqual(len(set(ids)), 2)
        self.assertTrue(all(i.startswith("cs_") for i in ids))
        self.assertEqual(len(self._stages()), 2)

    def test_identical_title_across_snapshots_reuses_the_same_stage(self):
        first = _model(("a", "Сборка предложения"))
        self._assign(first, spec=None)
        [first_id] = [v.canonical_stage_id for v in first.verdicts]

        second = _model(("a2", "Сборка предложения"))
        self._assign(second, spec=None)
        [second_id] = [v.canonical_stage_id for v in second.verdicts]

        self.assertEqual(first_id, second_id)
        self.assertEqual(len(self._stages()), 1)

    def test_minor_rewording_matches_via_fuzzy_not_llm(self):
        ChatClient._request = _scripted()  # не должен понадобиться
        first = _model(("a", "Оформление документов"))
        self._assign(first, spec=_SPEC)
        [first_id] = [v.canonical_stage_id for v in first.verdicts]

        second = _model(("a2", "Оформление документа"))
        self._assign(second, spec=_SPEC)
        [second_id] = [v.canonical_stage_id for v in second.verdicts]

        self.assertEqual(first_id, second_id)

    def test_full_rewording_is_matched_via_llm_fallback(self):
        # Пример из плана версионирования §5: "Создание КП" -> "Формирование
        # персонализированного предложения" — один и тот же этап, но текст
        # слишком разный для fuzzy-совпадения.
        first = _model(("a", "Создание коммерческого предложения"))
        self._assign(first, spec=None)
        [old_id] = [v.canonical_stage_id for v in first.verdicts]

        second = _model(("a2", "Формирование персонализированного предложения"))
        ratio_check = normalize("Создание коммерческого предложения")
        self.assertNotEqual(ratio_check, normalize("Формирование персонализированного предложения"))

        llm_answer = json.dumps([
            {"old_stage_id": old_id, "new_stage_id": "a2", "same_stage": True, "confidence": 0.93}
        ])
        ChatClient._request = _scripted(llm_answer)
        self._assign(second, spec=_SPEC)

        [second_id] = [v.canonical_stage_id for v in second.verdicts]
        self.assertEqual(second_id, old_id)
        self.assertEqual(len(self._stages()), 1)

    def test_low_confidence_llm_match_is_rejected_and_becomes_a_new_stage(self):
        first = _model(("a", "Создание коммерческого предложения"))
        self._assign(first, spec=None)
        [old_id] = [v.canonical_stage_id for v in first.verdicts]

        second = _model(("a2", "Формирование персонализированного предложения"))
        llm_answer = json.dumps([
            {"old_stage_id": old_id, "new_stage_id": "a2", "same_stage": True, "confidence": 0.4}
        ])
        ChatClient._request = _scripted(llm_answer)
        self._assign(second, spec=_SPEC)

        [second_id] = [v.canonical_stage_id for v in second.verdicts]
        self.assertNotEqual(second_id, old_id)
        self.assertEqual(len(self._stages()), 2)

    def test_matched_stage_records_the_new_wording_as_an_alias(self):
        first = _model(("a", "Сборка предложения"))
        self._assign(first, spec=None)
        [stage_id] = [v.canonical_stage_id for v in first.verdicts]

        second = _model(("a2", "Оформление предложения"))
        llm_answer = json.dumps([
            {"old_stage_id": stage_id, "new_stage_id": "a2", "same_stage": True, "confidence": 0.8}
        ])
        ChatClient._request = _scripted(llm_answer)
        self._assign(second, spec=_SPEC)

        [stage] = self._stages()
        self.assertIn(normalize("Оформление предложения"), stage.aliases)

    def test_reverting_to_an_older_wording_still_matches_via_full_alias_history(self):
        # Реестр (pko.store.canonical) копит алиасы за все проверки, а не
        # только за последнюю — переименование "туда и обратно" тоже матчится.
        v1 = _model(("a", "Первичный контакт"))
        self._assign(v1, spec=None)
        [stage_id] = [v.canonical_stage_id for v in v1.verdicts]

        v2 = _model(("a2", "Обработка обращения"))
        ChatClient._request = _scripted(json.dumps([
            {"old_stage_id": stage_id, "new_stage_id": "a2", "same_stage": True, "confidence": 0.9}
        ]))
        self._assign(v2, spec=_SPEC)

        v3 = _model(("a3", "Первичный контакт"))  # снова точная старая формулировка
        ChatClient._request = _scripted()  # точное совпадение — LLM не нужен
        self._assign(v3, spec=_SPEC)
        [v3_id] = [v.canonical_stage_id for v in v3.verdicts]

        self.assertEqual(v3_id, stage_id)
        self.assertEqual(len(self._stages()), 1)


if __name__ == "__main__":
    unittest.main()

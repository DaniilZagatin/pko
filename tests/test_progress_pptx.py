"""Чтение PPTX-плана: текст фигур, отделение заголовка слайда, эвристика раскладки.

Презентация строится в памяти через python-pptx — как и предполагает формат
входа (карточки задач, таймлайн), без OCR/скриншотов.
"""

import tempfile
import unittest
from pathlib import Path

from pptx import Presentation
from pptx.util import Inches, Pt

from pko.progress.pptx_reader import read_deck


def _add_box(slide, left, top, width, height, title, body=""):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = box.text_frame
    tf.word_wrap = True
    tf.paragraphs[0].text = title
    if body:
        p2 = tf.add_paragraph()
        p2.text = body
    return box


def build_sample_deck(path: Path) -> None:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    s1 = prs.slides.add_slide(blank)
    _add_box(s1, 1, 2.5, 11, 1.5, "Roadmap: редизайн платёжного сервиса", "Команда Payments")

    s2 = prs.slides.add_slide(blank)
    _add_box(s2, 0.5, 0.3, 6, 0.6, "Задачи спринта")
    cards = [
        ("Авторизация пользователей", "OAuth2 + JWT"),
        ("API платежей", "REST эндпоинты"),
        ("Уведомления", "Email + push"),
        ("Логирование", "Централизованный лог"),
        ("Ретраи операций", "Повтор через очередь"),
        ("Админ-дашборд", "Мониторинг платежей"),
    ]
    x0, y0, cw, ch = 0.5, 1.2, 4.0, 2.2
    for i, (title, body) in enumerate(cards):
        r, c = divmod(i, 3)
        _add_box(s2, x0 + c * (cw + 0.3), y0 + r * (ch + 0.3), cw, ch, title, body)

    s3 = prs.slides.add_slide(blank)
    _add_box(s3, 0.5, 0.3, 6, 0.6, "Таймлайн разработки")
    stages = [
        ("Этап 1: MVP", "июль"),
        ("Этап 2: Уведомления", "август"),
        ("Этап 3: Надёжность", "сентябрь"),
        ("Этап 4: Аналитика", "октябрь"),
    ]
    tx0, ty0, tw, th = 0.5, 2.5, 2.9, 1.6
    for i, (title, body) in enumerate(stages):
        _add_box(s3, tx0 + i * (tw + 0.2), ty0, tw, th, title, body)

    s4 = prs.slides.add_slide(blank)  # пустой слайд — потенциальный скриншот

    prs.save(str(path))


class PptxReaderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "plan.pptx"
        build_sample_deck(cls.path)
        cls.slides = read_deck(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_reads_all_slides_in_order(self):
        self.assertEqual([s.number for s in self.slides], [1, 2, 3, 4])

    def test_title_slide_has_no_split_heading(self):
        # Одна фигура — разделять не на что, весь текст остаётся в shapes.
        title_slide = self.slides[0]
        self.assertIsNone(title_slide.heading)
        self.assertEqual(len(title_slide.shapes), 1)
        self.assertIn("Roadmap", title_slide.shapes[0].text)

    def test_card_grid_heading_is_separated_from_cards(self):
        cards_slide = self.slides[1]
        self.assertEqual(cards_slide.heading, "Задачи спринта")
        self.assertEqual(len(cards_slide.shapes), 6)
        self.assertEqual(cards_slide.layout.kind, "CARD_GRID")

    def test_timeline_heading_does_not_pollute_layout_guess(self):
        # Регрессия на находку спайка Фазы 0: без отделения заголовка слайд с
        # таймлайном ошибочно классифицировался как CARD_GRID.
        timeline_slide = self.slides[2]
        self.assertEqual(timeline_slide.heading, "Таймлайн разработки")
        self.assertEqual(len(timeline_slide.shapes), 4)
        self.assertEqual(timeline_slide.layout.kind, "TIMELINE")

    def test_empty_slide_is_flagged_not_silently_dropped(self):
        empty_slide = self.slides[3]
        self.assertTrue(empty_slide.is_empty)
        self.assertIsNone(empty_slide.heading)
        self.assertEqual(empty_slide.shapes, [])


if __name__ == "__main__":
    unittest.main()

"""Chapter headers use one plain separator, and planned titles omit chapter numbers."""

import unittest
from unittest.mock import patch

import test_chapter_layout as fixture


class HeadingRegressionEdgesTests(unittest.TestCase):
    def setUp(self):
        self.case = fixture.ChapterLayoutTests()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_compatibility_digit_cannot_hide_a_plan_title_prefix(self):
        with self.assertRaises(fixture.story.StoryError) as raised:
            self.case.save_plan(1, title="第𝟙章 门后的雨")
        self.assertEqual(raised.exception.code, "chapter_title_prefix")
        self.assertIsNone(self.case.book.db.execute("SELECT data FROM plans WHERE chapter=1").fetchone())

    def test_new_heading_requires_exactly_one_ascii_space_after_chapter(self):
        self.case.save_plan(1, title="门后的雨")
        for opening in ("第1章\t门后的雨", "第1章　门后的雨", "第1章  门后的雨"):
            with self.subTest(opening=opening):
                text = opening + "\n" + fixture.BODY
                self.case.draft.write_text(text, encoding="utf-8")
                lint = self.case.book.lint(1, self.case.draft)
                self.assertIn("chapter_heading_format", {item["code"] for item in lint["errors"]})
                with self.assertRaises(fixture.story.StoryError) as raised:
                    self.case.book.commit(1, self.case.draft, self.case.delta(text))
                self.assertEqual(raised.exception.code, "lint_failed")
        self.case.commit(1, "第1章 门后的雨\n" + fixture.BODY)

    def test_unchanged_legacy_separator_remains_compatible(self):
        self.case.save_plan(1, title="门后的雨")
        original = "第1章\t门后的雨\n" + fixture.BODY
        with patch.object(fixture.story, "chapter_heading_errors", return_value=[]):
            self.case.commit(1, original)
        revised = original + "她留下收据。\n"
        result, _ = self.case.commit(1, revised, replace_last=True)
        self.assertTrue(result["exports_complete"])
        changed = revised.replace("第1章\t", "第1章  ", 1)
        self.case.draft.write_text(changed, encoding="utf-8")
        lint = self.case.book.lint(1, self.case.draft)
        self.assertIn("chapter_heading_format", {item["code"] for item in lint["errors"]})


if __name__ == "__main__":
    unittest.main()

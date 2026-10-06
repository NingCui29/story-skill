"""Punctuation candidates stay advisory and retain exact source coordinates."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


TOOL = Path(__file__).resolve().parents[1] / "skills/story-skill/scripts/story.py"
SPEC = importlib.util.spec_from_file_location("punctuation_tests_story", TOOL)
story = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(story)


class PunctuationTests(unittest.TestCase):
    def review(self, text):
        warnings = story.punctuation.review_warnings(text)
        return warnings[0] if warnings else None

    def test_shapes_are_locatable_nonblocking_candidates(self):
        text = "第1章 归来\r\n她说:先走,别等;天黑了!你呢?\r“我…不知...”\n他—她，先--走。"
        warning = self.review(text)
        self.assertEqual(warning["code"], "punctuation_review")
        self.assertEqual(warning["count"], 9)
        self.assertFalse(warning["truncated"])
        self.assertEqual([item["kind"] for item in warning["examples"]], [
            "halfwidth_punctuation", "halfwidth_punctuation", "halfwidth_punctuation",
            "halfwidth_punctuation", "halfwidth_punctuation", "ellipsis_form",
            "ellipsis_form", "dash_form", "dash_form"])
        self.assertEqual(warning["examples"][0]["line"], 2)
        self.assertEqual(warning["examples"][0]["column"], 3)
        self.assertIn("语境复核", warning["note"])
        self.assertIn("连接号", warning["examples"][-1]["note"])
        for item in warning["examples"]:
            original = text.splitlines()[item["line"] - 1]
            start = item["column"] - 1
            self.assertEqual(original[start:start + item["length"]], item["text"])
            self.assertIn(item["text"], item["excerpt"])

    def test_normal_prose_and_semantics_are_not_machine_judged(self):
        text = ("她说：“门已经开了。你先走。”\n"
                "“第一段。\n\n“第二段。”\n"
                "她想……又停住——脚步声近了。\n"
                "怎么？！什么?!真棒!!难道!?\n"
                "“我……算了。”她说：“老师说‘明天见。’”\n"
                "她只说：“这还没有闭合。\n"
                "一段省略：…………\n")
        self.assertIsNone(self.review(text))

    def test_code_urls_emails_numeric_forms_and_english_are_preserved(self):
        text = ("英文原文：Hello, world; wait... why? Yes!\n"
                "她念出“Hello!”之后，又补了一句\"Wait...\"才离开。\n"
                "价格3.14，时间8:30，比例1:2，金额1,000，编号-3，范围1—3。\n"
                "她写下`对象:值,选项;甲?乙!我...他—她`后保存。\n"
                "另一处``甲,乙`丙:丁``也保留。\n"
                "网址https://example.com/甲?乙=1,丙=2;丁=3...随后\n"
                "网址：www.example.com/甲?乙=1，邮箱：甲乙@example.com\n"
                "```python\nprint('甲,乙...')\n```\n"
                "~~~text\n甲?乙!丙—丁\n~~~\n")
        self.assertIsNone(self.review(text))

    def test_fence_length_and_inline_delimiters_do_not_hide_following_prose(self):
        text = ("````text\n甲,乙...\n```\n丙!\n````\n"
                "`甲,乙`后说:先走。\n"
                "末行甲,乙\n")
        warning = self.review(text)
        self.assertEqual(warning["count"], 2)
        self.assertEqual([(item["line"], item["text"]) for item in warning["examples"]],
                         [(6, ":"), (7, ",")])
        self.assertIsNone(self.review("```\n甲,乙...\n"))
        self.assertEqual(self.review("未闭合`后说:先走。")["count"], 1)

    def test_original_unicode_columns_survive_masking(self):
        text = "🙂`甲,乙`https://a.example/甲?乙 𠀀,丁"
        warning = self.review(text)
        self.assertEqual(warning["count"], 1)
        self.assertEqual(warning["examples"][0]["column"], text.rindex(",") + 1)

    def test_no_hard_findings_or_source_mutations_are_added(self):
        text = "第1章 归来\n她说:先走,别等...\n"
        plan = {"length": [1, 200], "count_method": "visible_nonspace_v2", "count_title": False}
        result = story.lint_text(text, plan)
        with patch.object(story.punctuation, "review_warnings", return_value=[]):
            baseline = story.lint_text(text, plan)
        self.assertTrue(result["ok"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["draft_sha256"], story.digest(text))
        self.assertEqual({key: value for key, value in result.items() if key != "warnings"},
                         {key: value for key, value in baseline.items() if key != "warnings"})
        self.assertEqual([item["code"] for item in result["warnings"]], ["punctuation_review"])
        # Pre-existing gates must remain failures, with the same error payload.
        short = {**plan, "length": [200, 300]}
        failing = story.lint_text(text, short)
        with patch.object(story.punctuation, "review_warnings", return_value=[]):
            original = story.lint_text(text, short)
        self.assertFalse(failing["ok"])
        self.assertEqual(failing["errors"], original["errors"])

    def test_output_is_bounded_and_reports_all_candidates(self):
        text = "甲,乙\n" * 25
        warning = self.review(text)
        self.assertEqual(warning["count"], 25)
        self.assertTrue(warning["truncated"])
        self.assertEqual(len(warning["examples"]), story.punctuation.MAX_EXAMPLES)
        self.assertEqual(warning["examples"][-1]["line"], 20)
        long_run = self.review("甲" + "." * 10000 + "乙")
        example = long_run["examples"][0]
        self.assertEqual(example["length"], 10000)
        self.assertEqual(len(example["text"]), 24)
        self.assertLessEqual(len(example["excerpt"]), 96)


if __name__ == "__main__":
    unittest.main()

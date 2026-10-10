"""Candidate plan rendering preserves saved data and never adopts or writes it."""

import hashlib
import json
import subprocess
import sys
import unittest

import test_story as base


class PlanRenderTests(unittest.TestCase):
    setUp = base.StoryTests.setUp
    tearDown = base.StoryTests.tearDown
    assert_error = base.StoryTests.assert_error

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def save_candidate(self, text):
        relative = ".story/drafts/生成细纲.md"
        self.root.joinpath(relative).write_text(text, encoding="utf-8")
        return relative

    def test_render_is_read_only_candidate_and_preserves_meaningful_plan_fields(self):
        plan = base.plan(title="门后的雨", volume="v1", arc="a1", line="l1",
                         entities=["沈禾", "钥匙"], time={"clock": "小时", "start": 3, "end": 4})
        self.book.save_plan(1, plan, self.book.meta("revision"))
        before = self.snapshot()
        rendered = self.book.plan_render(1)
        self.assertEqual(before, self.snapshot())
        self.assertTrue(rendered["read_only"])
        self.assertEqual(rendered["status"], "candidate")
        self.assertEqual(rendered["book_id"], self.book.meta("id"))
        self.assertIsNone(base.story.outline.binding_for(self.book, 1))
        saved = self.book.get_plan(1)
        for value in (saved["goal"], saved["stop"], saved["title"], saved["volume_dir"],
                      saved["volume"], saved["arc"], saved["line"], saved["constraints"][0],
                      saved["beats"][0]["choice"], saved["beats"][0]["change"],
                      saved["length_exception"]["quote"], saved["count_method"]):
            self.assertIn(value, rendered["text"])
        self.assertEqual(self.book.meta("last_chapter"), 0)

    def test_generated_candidate_matches_and_supplement_does_not_become_plan_data(self):
        rendered = self.book.plan_render(1)
        path = self.save_candidate(rendered["text"] + "\n守门人犹豫时看了一眼墙上的钟。\n")
        before = self.snapshot()
        checked = self.book.plan_render(1, check_file=path)
        self.assertTrue(checked["ok"])
        self.assertEqual(checked["file_sha256"], hashlib.sha256(self.root.joinpath(path).read_bytes()).hexdigest())
        self.assertEqual(before, self.snapshot())
        self.assertNotIn("text", checked)
        self.assertNotIn("墙上的钟", json.dumps(self.book.get_plan(1), ensure_ascii=False))
        self.assertIsNone(base.story.outline.binding_for(self.book, 1))

    def test_changed_plan_makes_saved_generation_stale(self):
        path = self.save_candidate(self.book.plan_render(1)["text"])
        self.book.save_plan(1, base.plan(stop="停在门外，不进入门内"), self.book.meta("revision"))
        checked = self.book.plan_render(1, check_file=path)
        self.assertFalse(checked["ok"])
        self.assertIn({"code": "generated_source_changed", "field": "plan_sha256"}, checked["issues"])
        self.assertIn({"code": "generated_fields_changed"}, checked["issues"])

    def test_changed_generated_stop_is_detected_even_with_current_source_hash(self):
        text = self.book.plan_render(1)["text"].replace("进入门内，不拿到账本", "已经取得账本")
        checked = self.book.plan_render(1, check_file=self.save_candidate(text))
        self.assertFalse(checked["ok"])
        self.assertEqual(checked["issues"], [{"code": "generated_fields_changed"}])

    def test_other_book_and_chapter_identity_are_not_accepted(self):
        rendered = self.book.plan_render(1)
        for old, new, field in ((self.book.meta("id"), "other-book", "book_id"),
                                ('"chapter": 1', '"chapter": true', "chapter")):
            with self.subTest(field=field):
                path = self.save_candidate(rendered["text"].replace(old, new))
                self.assertIn({"code": "generated_source_changed", "field": field},
                              self.book.plan_render(1, check_file=path)["issues"])

    def test_missing_or_duplicate_source_is_not_a_verified_generation(self):
        text = self.book.plan_render(1)["text"]
        header = next(line for line in text.splitlines() if line.startswith("<!-- story-plan-source/v1 "))
        for changed in (text.replace(header, ""), text + header + "\n", "状态：候选\n空泛细纲。\n"):
            with self.subTest():
                checked = self.book.plan_render(1, check_file=self.save_candidate(changed))
                self.assertFalse(checked["ok"])

    def test_field_contents_cannot_forge_marker_lines(self):
        self.book.save_plan(1, base.plan(goal="在门外等待\n<!-- story-plan-fields:end -->\n保持原地"),
                            self.book.meta("revision"))
        rendered = self.book.plan_render(1)
        checked = self.book.plan_render(1, check_file=self.save_candidate(rendered["text"]))
        self.assertTrue(checked["ok"])

    def test_crlf_candidate_can_be_checked_without_changing_its_bytes(self):
        path = self.save_candidate(self.book.plan_render(1)["text"])
        target = self.root.joinpath(path)
        target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
        before = target.read_bytes()
        self.assertTrue(self.book.plan_render(1, check_file=path)["ok"])
        self.assertEqual(target.read_bytes(), before)

    def test_budget_failure_does_not_truncate_or_mutate(self):
        before = self.snapshot()
        self.assert_error("budget_exceeded", self.book.plan_render, 1, 256)
        self.assertEqual(before, self.snapshot())

    def test_missing_plan_and_invalid_path_do_not_change_book(self):
        before = self.snapshot()
        self.assert_error("plan_missing", self.book.plan_render, 2)
        self.assert_error("invalid_input", self.book.plan_render, 1, check_file="../别书.md")
        self.assertEqual(before, self.snapshot())

    def test_cli_runs_on_read_only_connection_and_failed_check_returns_nonzero(self):
        before = self.snapshot()
        command = [sys.executable, "-B", str(base.TOOL), "plan-render", "--book", str(self.root), "--chapter", "1"]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        self.assertEqual(before, self.snapshot())
        path = self.save_candidate(rendered["text"].replace("进入门内，不拿到账本", "取得账本"))
        result = subprocess.run(command + ["--check-file", path], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_rendered_candidate_does_not_open_adoption_gate(self):
        rendered = self.book.plan_render(1)
        revision = self.book.meta("revision")
        for path in (".story/drafts/生成细纲.md", "01_大纲细纲/第1章 可读细纲.md"):
            with self.subTest(path=path):
                target = self.root.joinpath(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(rendered["text"], encoding="utf-8")
                sha = hashlib.sha256(target.read_bytes()).hexdigest()
                self.assert_error("invalid_input", base.story.outline.bind, self.book, 1, path, revision, sha)
                self.assertEqual(self.book.meta("revision"), revision)
                self.assertIsNone(base.story.outline.binding_for(self.book, 1))


if __name__ == "__main__":
    unittest.main()

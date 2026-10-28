"""Default chapters enforce a real 2000-character minimum without a ceiling."""
import copy
import json
from pathlib import Path
import os
import sys
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from outline_fixture import bind_adopted_outline
from test_story import story


class MinimumChapterLengthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-minimum-only-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "合成作品"
        story.Book.create(self.root, "门内", "long")
        self.book = story.Book(self.root)
        self.addCleanup(self.book.close)
        self.plan = copy.deepcopy(story.TEMPLATES["plan"])
        self.plan.update(title="推门", volume_dir="第一卷 门内", goal="推门进入屋内",
                         beats=[{"choice": "推开门", "change": "进入屋内"}],
                         stop="停在门内", constraints=[], requires=[], tags=[])
        self.draft = self.root / ".story/drafts/chapter.md"
        self.draft.parent.mkdir(parents=True, exist_ok=True)

    def save(self, plan=None):
        self.book.save_plan(1, plan or self.plan, self.book.meta("revision"))
        bind_adopted_outline(story, self.book, 1)

    def write(self, count):
        body = ("她推开门。" * (count // 5 + 1))[:count]
        text = "第1章 推门\n" + body
        self.draft.write_bytes(text.encode("utf-8"))
        return text

    def delta(self, text):
        return {"book_id": self.book.meta("id"), "base_revision": self.book.meta("revision"),
                "summary": "她推开门进入屋内。", "changes": [],
                "review": {"draft_sha256": story.digest(text), "checks": {
                    check: {"note": "合成正文中的行动与结果可核对。", "quote": "她推开门。"}
                    for check in story.CHECKS}, "issues": []}}

    def test_unbounded_plan_rejects_invalid_minimum_and_maximum_values(self):
        self.assertEqual(story.valid_plan(self.plan)["length"], [2000, None])
        for length in ([None, None], [True, None], [0, None], [-1, None],
                       ["2000", None], [2000, True], [2000, "none"],
                       [2000, 1999], [2000], [2000, None, 3000]):
            with self.subTest(length=length), self.assertRaises(story.StoryError) as error:
                story.valid_plan({**self.plan, "length": length})
            self.assertEqual(error.exception.code, "invalid_input")

    def test_new_native_chapter_over_old_ceiling_round_trips_without_exception(self):
        self.save()
        self.assertEqual(self.book.get_plan(1)["length"], [2000, None])
        self.assertNotIn("length_exception", self.book.get_plan(1))
        text = self.write(3000)
        prepared = self.book.prepare(1, self.draft)
        self.assertTrue(prepared["lint"]["ok"], prepared)
        committed = self.book.commit(1, self.draft, self.delta(text))
        self.assertTrue(committed["exports_complete"])
        self.assertEqual((self.root / self.book.chapter_path(1)).read_bytes(), text.encode("utf-8"))
        reopened = story.Book(self.root)
        try:
            self.assertEqual(reopened.get_plan(1)["length"], [2000, None])
            self.assertTrue(reopened.lint(1, self.draft)["ok"])
        finally:
            reopened.close()

    def test_below_minimum_is_rejected_without_advancing_formal_state(self):
        self.save()
        text = self.write(1999)
        before = self.book.meta("revision"), self.book.meta("last_chapter")
        checked = self.book.lint(1, self.draft)
        self.assertFalse(checked["ok"])
        self.assertIn({"code": "length", "actual": 1999, "expected": [2000, None]}, checked["errors"])
        with self.assertRaises(story.StoryError) as error:
            self.book.commit(1, self.draft, self.delta(text))
        self.assertEqual(error.exception.code, "lint_failed")
        self.assertEqual((self.book.meta("revision"), self.book.meta("last_chapter")), before)
        self.assertFalse((self.root / self.book.chapter_path(1)).exists())

    def test_explicit_bounded_agreement_still_limits_the_chapter(self):
        explicit = {**self.plan, "length": [2000, 2200], "length_exception": {
            "source": "user_request", "quote": "本章明确要求2000到2200字。"}}
        self.save(explicit)
        for count, expected in ((2000, True), (2200, True), (2201, False)):
            with self.subTest(count=count):
                self.write(count)
                self.assertIs(self.book.lint(1, self.draft)["ok"], expected)
        self.assertEqual(self.book.get_plan(1)["length"], [2000, 2200])

    def legacy_writing_task(self):
        # Model a persisted task produced before the default changed, without
        # changing runtime behavior or authorizing an exception after upgrade.
        with patch.object(story.Book, "_length_exception_issue", return_value=None):
            self.save({**self.plan, "length": [2400, 2800]})
            task = story.workbench._editor_writing_task(self.root)["task"]
        # Older task records had the structured next-chapter brief but no
        # explicit complete_chapter field. Keep the rest of that record intact.
        path = self.root / story.tasks.DIRECTORY / (task["id"] + ".json")
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertIs(record["binding"].pop("complete_chapter"), True)
        previous_sha = record["binding_sha256"]
        record["binding_sha256"] = story.digest(story.dumps(record["binding"]))
        record["prompt"] = record["prompt"].replace(previous_sha, record["binding_sha256"])
        path.write_bytes(story.dumps(record).encode("utf-8"))
        return task

    def test_saved_legacy_writing_task_stays_readable_but_cannot_be_handed_off_or_started(self):
        task = self.legacy_writing_task()
        record = self.root / story.tasks.DIRECTORY / (task["id"] + ".json")
        original_record = record.read_bytes()
        observed = story.tasks.get(self.root, task["id"])["task"]
        self.assertIn("length_exception_required", {r["code"] for r in observed["stale_reasons"]})
        self.assertIn("2800", observed["prompt"])
        self.assertEqual(record.read_bytes(), original_record)
        with self.assertRaises(story.StoryError) as handoff:
            story.tasks.mark_handoff(self.root, task["id"], task["version"])
        self.assertEqual(handoff.exception.code, "stale_snapshot")
        with patch.object(story.task_worker, "_executable") as executable, \
                patch.object(story.task_worker, "_freeze") as freeze, \
                patch.object(story.task_worker, "_spawn") as spawn:
            with self.assertRaises(story.StoryError) as start:
                story.tasks.execution_start(self.root, task["id"], task["version"], "legacy-new-launch")
            self.assertEqual(start.exception.code, "stale_snapshot")
            executable.assert_not_called()
            freeze.assert_not_called()
            spawn.assert_not_called()
        for name in ("executions", "execution-heads", "execution-requests"):
            self.assertFalse((self.root / story.tasks.DIRECTORY / name).exists())
        self.assertEqual(record.read_bytes(), original_record)
        self.assertEqual(self.book.meta("last_chapter"), 0)

    def test_explicit_finite_writing_task_remains_eligible_for_handoff(self):
        self.save({**self.plan, "length": [2400, 2800], "length_exception": {
            "source": "user_request", "quote": "本章明确要求2400到2800字。"}})
        task = story.workbench._editor_writing_task(self.root)["task"]
        self.assertEqual(task["stale_reasons"], [])
        handed = story.tasks.mark_handoff(self.root, task["id"], task["version"])["task"]
        self.assertEqual(handed["status"], "handed_off")
        self.assertEqual(handed["stale_reasons"], [])

    def test_unscoped_writing_fragment_does_not_inherit_an_unrelated_legacy_chapter_limit(self):
        self.legacy_writing_task()
        task = story.tasks.create(self.root, "writing", "只拟一小段人物对白候选。", [], chapter=None)["task"]
        self.assertEqual(task["stale_reasons"], [])
        handed = story.tasks.mark_handoff(self.root, task["id"], task["version"])["task"]
        self.assertEqual(handed["status"], "handed_off")

    def test_chapter_linked_manual_fragment_is_not_a_complete_chapter_task(self):
        self.legacy_writing_task()
        task = story.tasks.create(self.root, "writing", "只拟第1章的一小段人物对白候选。", [], chapter=1)["task"]
        path = self.root / story.tasks.DIRECTORY / (task["id"] + ".json")
        self.assertIs(json.loads(path.read_text(encoding="utf-8"))["binding"]["complete_chapter"], False)
        self.assertEqual(task["stale_reasons"], [])
        handed = story.tasks.mark_handoff(self.root, task["id"], task["version"])["task"]
        self.assertEqual(handed["status"], "handed_off")
        # The same chapter-linked manual fragment also stays eligible in the
        # old record shape, whose prompt never contained a structured brief.
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertIs(record["binding"].pop("complete_chapter"), False)
        previous_sha = record["binding_sha256"]
        record["binding_sha256"] = story.digest(story.dumps(record["binding"]))
        record["prompt"] = record["prompt"].replace(previous_sha, record["binding_sha256"])
        path.write_bytes(story.dumps(record).encode("utf-8"))
        self.assertEqual(story.tasks.get(self.root, task["id"])["task"]["stale_reasons"], [])
        handed = story.tasks.mark_handoff(self.root, task["id"], handed["version"])["task"]
        with patch.object(story.task_worker, "_executable", return_value=Path(sys.executable).resolve()), \
                patch.object(story.task_worker, "_freeze", side_effect=RuntimeError("FRAGMENT_FREEZE")) as freeze, \
                patch.object(story.task_worker, "_spawn") as spawn:
            with self.assertRaisesRegex(RuntimeError, "FRAGMENT_FREEZE"):
                story.tasks.execution_start(self.root, task["id"], handed["version"], "manual-fragment")
            self.assertEqual(freeze.call_count, 1)
            spawn.assert_not_called()

    def test_legacy_manual_fragment_can_quote_a_full_brief_as_reference(self):
        task = self.legacy_writing_task()
        path = self.root / story.tasks.DIRECTORY / (task["id"] + ".json")
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("complete_chapter", record["binding"])
        self.assertTrue(record["prompt"].startswith("请继续创作本书第1章，"))
        record["prompt"] = ("只写第1章的一小段对白；以下完整简报仅作参考资料。\n"
                            + record["prompt"].partition("\n")[2])
        self.assertIn('"mode": "next_chapter_draft"', record["prompt"])
        raw = story.dumps(record).encode("utf-8")
        path.write_bytes(raw)
        observed = story.tasks.get(self.root, task["id"])["task"]
        self.assertEqual(observed["stale_reasons"], [])
        self.assertEqual(observed["prompt"], record["prompt"])
        self.assertEqual(path.read_bytes(), raw)
        handed = story.tasks.mark_handoff(self.root, task["id"], task["version"])["task"]
        self.assertEqual(handed["status"], "handed_off")
        self.assertEqual(handed["stale_reasons"], [])

    def test_complete_chapter_scope_rejects_non_boolean_flags(self):
        for flag in (None, 0, 1, "true", [], {}):
            with self.subTest(flag=flag), self.assertRaises(story.StoryError) as error:
                story.tasks.create(self.root, "writing", "拟第1章候选。", [], chapter=1,
                                   complete_chapter=flag)
            self.assertEqual(error.exception.code, "invalid_input")
        for kind, chapter in (("review", 1), ("writing", None)):
            with self.subTest(kind=kind, chapter=chapter), self.assertRaises(story.StoryError) as error:
                story.tasks.create(self.root, kind, "候选任务。", [], chapter=chapter, complete_chapter=True)
            self.assertEqual(error.exception.code, "invalid_input")
        self.assertFalse((self.root / story.tasks.DIRECTORY).exists())

    def test_saved_task_rejects_non_boolean_complete_chapter_metadata(self):
        self.save()
        task = story.workbench._editor_writing_task(self.root)["task"]
        path = self.root / story.tasks.DIRECTORY / (task["id"] + ".json")
        original = json.loads(path.read_text(encoding="utf-8"))
        for flag in (None, 0, 1, "true", [], {}):
            record = copy.deepcopy(original)
            record["binding"]["complete_chapter"] = flag
            record["binding_sha256"] = story.digest(story.dumps(record["binding"]))
            raw = story.dumps(record).encode("utf-8")
            path.write_bytes(raw)
            with self.subTest(flag=flag), self.assertRaises(story.StoryError) as error:
                story.tasks.get(self.root, task["id"])
            self.assertEqual(error.exception.code, "task_record_invalid")
            self.assertEqual(path.read_bytes(), raw)

    def test_committed_historical_writing_candidate_keeps_its_saved_range(self):
        plan = {**self.plan, "length": [2400, 2800], "length_exception": {
            "source": "user_request", "quote": "历史章明确要求2400到2800字。"}}
        self.save(plan)
        text = self.write(2500)
        self.assertTrue(self.book.commit(1, self.draft, self.delta(text))["exports_complete"])
        saved = self.book.get_plan(1)
        saved.pop("length_exception")
        self.book.save_plan(1, saved, self.book.meta("revision"))
        task = story.tasks.create(self.root, "writing", "只准备已提交旧章的局部修订候选。",
                                  [{"chapter": 1, "sha256": story.digest(text)}], chapter=1)["task"]
        self.assertEqual(task["stale_reasons"], [])
        self.assertEqual(story.tasks.mark_handoff(self.root, task["id"], task["version"])["task"]["status"],
                         "handed_off")
        self.assertEqual(self.book.get_plan(1)["length"], [2400, 2800])

    def test_same_execution_request_reads_existing_attempt_after_a_policy_change(self):
        task = self.legacy_writing_task()
        cache = self.root.parent / "synthetic-worker-cache"
        worker = story.task_worker
        with patch.object(worker, "_base", return_value=cache), \
                patch.object(worker, "_model_defaults", return_value={}), \
                patch.object(worker, "_executable", return_value=Path(sys.executable).resolve()) as executable, \
                patch.object(worker, "_spawn", return_value=SimpleNamespace(pid=os.getpid())) as spawn:
            with patch.object(story.Book, "_length_exception_issue", return_value=None):
                first = story.tasks.execution_start(self.root, task["id"], task["version"], "saved-request")
            execution_id = first["execution"]["id"]
            for state in ("starting", "unknown"):
                if state == "unknown":
                    with story.tasks._book(self.root, write=True) as book:
                        ledger, sha = worker._read_ledger(book, task["id"], execution_id)
                        ledger["state"] = "unknown"
                        worker._save_ledger(book, ledger, sha)
                observed = story.tasks.execution_start(self.root, task["id"], task["version"], "saved-request")
                self.assertTrue(observed["unchanged"])
                self.assertEqual(observed["execution"]["id"], execution_id)
            self.assertEqual(executable.call_count, 1)
            self.assertEqual(spawn.call_count, 1)
            with self.assertRaises(story.StoryError) as next_request:
                story.tasks.execution_start(self.root, task["id"], task["version"], "different-request")
            self.assertEqual(next_request.exception.code, "task_execution_unresolved")

    def test_explicit_complete_task_requires_a_plan_but_unplanned_fragment_does_not(self):
        with self.assertRaises(story.StoryError) as error:
            story.tasks.create(self.root, "writing", "按本章完整范围起草。", [],
                               chapter=1, complete_chapter=True)
        self.assertEqual(error.exception.code, "plan_missing")
        self.assertFalse((self.root / story.tasks.DIRECTORY).exists())
        task = story.tasks.create(self.root, "writing", "只拟一个片段。", [], chapter=1)["task"]
        self.assertEqual(task["stale_reasons"], [])
        self.assertEqual(story.tasks.mark_handoff(self.root, task["id"], task["version"])["task"]["status"],
                         "handed_off")

    def test_complete_task_rechecks_book_agreement_before_new_handoff_or_launch(self):
        quote = "本章明确按2400到2800字写作。"
        agreement = self.root / "创作约定.md"
        agreement.write_text(quote + "\n", encoding="utf-8")
        self.save({**self.plan, "length": [2400, 2800], "length_exception": {
            "source": "book_agreement", "path": "创作约定.md", "quote": quote}})
        task = story.workbench._editor_writing_task(self.root)["task"]
        path = self.root / story.tasks.DIRECTORY / (task["id"] + ".json")
        before, revision = path.read_bytes(), self.book.meta("revision")
        agreement.write_text("原篇幅依据已撤回。\n", encoding="utf-8")
        observed = story.tasks.get(self.root, task["id"])["task"]
        self.assertIn("length_exception_source_missing", {r["code"] for r in observed["stale_reasons"]})
        with self.assertRaises(story.StoryError) as error:
            story.tasks.mark_handoff(self.root, task["id"], task["version"])
        self.assertEqual(error.exception.code, "stale_snapshot")
        with patch.object(story.task_worker, "_executable") as executable:
            with self.assertRaises(story.StoryError) as error:
                story.tasks.execution_start(self.root, task["id"], task["version"], "changed-agreement")
            self.assertEqual(error.exception.code, "stale_snapshot")
            executable.assert_not_called()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertFalse((self.root / story.tasks.DIRECTORY / "executions").exists())

    def test_rendered_outline_explains_unbounded_length(self):
        self.save()
        rendered = story.outline.render_plan(self.book, 1, self.book.get_plan(1))
        self.assertTrue(rendered["read_only"])
        text = rendered["text"]
        self.assertIn("至少 2000 字（无上限）", text)
        self.assertNotIn("2000—None", text)
        self.assertNotIn("2000—null", text)


if __name__ == "__main__":
    unittest.main()

"""Writing scope survives legacy records and rejects malformed or mismatched returns."""
from contextlib import contextmanager
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_minimum_chapter_length as base
from test_story import TOOL


story = base.story


class TaskScopeCompatibilityTests(unittest.TestCase):
    @contextmanager
    def legacy_task(self):
        fixture = base.MinimumChapterLengthTests("runTest")
        fixture.setUp()
        try:
            task = fixture.legacy_writing_task()
            path = fixture.root / story.tasks.DIRECTORY / (task["id"] + ".json")
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("complete_chapter", record["binding"])
            yield fixture, task, path, record
        finally:
            fixture.doCleanups()

    def replace_brief(self, prompt, change):
        marker = "本章写前简报：\n"
        head, found, suffix = prompt.partition(marker)
        self.assertTrue(found)
        brief, end = json.JSONDecoder().raw_decode(suffix)
        updated = change(brief)
        return head + marker + json.dumps(updated, ensure_ascii=False, indent=2) + suffix[end:]

    def assert_unknown_readable_and_not_dispatched(self, mutate):
        with self.legacy_task() as (fixture, task, path, record):
            record["prompt"] = mutate(record["prompt"])
            path.write_bytes(story.dumps(record).encode("utf-8"))
            before, revision = path.read_bytes(), fixture.book.meta("revision")
            observed = story.tasks.get(fixture.root, task["id"])["task"]
            self.assertEqual(observed["prompt"], record["prompt"])
            self.assertIn("task_scope_unknown", {row["code"] for row in observed["stale_reasons"]})
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaises(story.StoryError) as handoff:
                story.tasks.mark_handoff(fixture.root, task["id"], task["version"])
            self.assertEqual(handoff.exception.code, "stale_snapshot")
            with patch.object(story.task_worker, "_executable") as executable, \
                    patch.object(story.task_worker, "_freeze") as freeze, \
                    patch.object(story.task_worker, "_spawn") as spawn:
                with self.assertRaises(story.StoryError) as launch:
                    story.tasks.execution_start(fixture.root, task["id"], task["version"], "unknown-scope")
                self.assertEqual(launch.exception.code, "stale_snapshot")
                executable.assert_not_called()
                freeze.assert_not_called()
                spawn.assert_not_called()
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(fixture.book.meta("revision"), revision)
            self.assertEqual(fixture.book.meta("last_chapter"), 0)
            for name in ("executions", "execution-heads", "execution-requests"):
                self.assertFalse((fixture.root / story.tasks.DIRECTORY / name).exists())

    def test_unknown_legacy_delivery_mode_is_not_downgraded_to_a_fragment(self):
        def change(brief):
            brief["delivery_scope"]["mode"] = "unknown"
            return brief
        self.assert_unknown_readable_and_not_dispatched(lambda prompt: self.replace_brief(prompt, change))

    def test_truncated_legacy_json_stays_readable_but_cannot_be_dispatched(self):
        marker = "本章写前简报：\n"
        self.assert_unknown_readable_and_not_dispatched(
            lambda prompt: prompt.partition(marker)[0] + marker + "{unreadable")

    def test_missing_legacy_brief_marker_is_not_a_manual_scope_declaration(self):
        self.assert_unknown_readable_and_not_dispatched(
            lambda prompt: prompt.replace("本章写前简报：\n", "本章写前简报(残缺)：\n", 1))

    def test_mismatched_legacy_identity_is_unknown(self):
        changes = (
            ("book_id", "different-book"), ("revision", -1), ("chapter", 2),
        )
        for field, value in changes:
            with self.subTest(field=field):
                def change(brief, field=field, value=value):
                    brief[field] = value
                    return brief
                self.assert_unknown_readable_and_not_dispatched(
                    lambda prompt: self.replace_brief(prompt, change))

    def test_invalid_legacy_brief_structures_are_unknown(self):
        changes = (
            lambda brief: [],
            lambda brief: {**brief, "delivery_scope": []},
            lambda brief: {**brief, "delivery_scope": None},
            lambda brief: {**brief, "schema_version": True},
            lambda brief: {**brief, "chapter": True},
        )
        for number, change in enumerate(changes):
            with self.subTest(shape=number):
                self.assert_unknown_readable_and_not_dispatched(
                    lambda prompt: self.replace_brief(prompt, change))

    def test_deeply_nested_inner_json_is_caught_without_losing_the_original_task(self):
        marker = "本章写前简报：\n"
        self.assert_unknown_readable_and_not_dispatched(
            lambda prompt: prompt.partition(marker)[0] + marker + "[" * 20000 + "0" + "]" * 20000)

    def test_deeply_nested_current_plan_is_readable_but_cannot_be_dispatched(self):
        with self.legacy_task() as (fixture, task, path, record):
            plan = '{"length":[2000,null],"extra":' + "[" * 20000 + "0" + "]" * 20000 + "}"
            fixture.book.db.execute("UPDATE plans SET data=? WHERE chapter=1", (plan,))
            fixture.book.db.commit()
            before, revision = path.read_bytes(), fixture.book.meta("revision")
            observed = story.tasks.get(fixture.root, task["id"])["task"]
            self.assertEqual({row["code"] for row in observed["stale_reasons"]}, {"writing_plan_invalid"})
            with self.assertRaises(story.StoryError) as handoff:
                story.tasks.mark_handoff(fixture.root, task["id"], task["version"])
            self.assertEqual(handoff.exception.code, "stale_snapshot")
            with patch.object(story.task_worker, "_executable") as executable, \
                    patch.object(story.task_worker, "_freeze") as freeze, \
                    patch.object(story.task_worker, "_spawn") as spawn:
                with self.assertRaises(story.StoryError) as launch:
                    story.tasks.execution_start(fixture.root, task["id"], task["version"], "deep-plan")
                self.assertEqual(launch.exception.code, "stale_snapshot")
                executable.assert_not_called()
                freeze.assert_not_called()
                spawn.assert_not_called()
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(fixture.book.db.execute("SELECT data FROM plans WHERE chapter=1").fetchone()[0], plan)
            self.assertEqual(fixture.book.meta("revision"), revision)
            self.assertEqual(fixture.book.meta("last_chapter"), 0)

    def test_deeply_nested_task_and_result_files_return_structured_errors(self):
        with self.legacy_task() as (fixture, task, path, record):
            nested = "[" * 20000 + "0" + "]" * 20000
            raw = path.read_bytes().rstrip()
            self.assertTrue(raw.endswith(b"}"))
            invalid_record = raw[:-1] + b',"extra":' + nested.encode("ascii") + b"}"
            path.write_bytes(invalid_record)
            revision = fixture.book.meta("revision")
            with self.assertRaises(story.StoryError) as error:
                story.tasks.get(fixture.root, task["id"])
            self.assertEqual(error.exception.code, "task_record_invalid")
            self.assertEqual(path.read_bytes(), invalid_record)
            result = fixture.root / ".story/drafts/deep-result.json"
            result.write_bytes(nested.encode("ascii"))
            with self.assertRaises(story.StoryError) as error:
                story.tasks.read_result_file(fixture.root, ".story/drafts/deep-result.json")
            self.assertEqual(error.exception.code, "invalid_input")
            self.assertEqual(result.read_bytes(), nested.encode("ascii"))
            self.assertEqual(fixture.book.meta("revision"), revision)
            self.assertEqual(fixture.book.meta("last_chapter"), 0)

    def test_invisible_fragment_prefix_cannot_hide_an_explicit_wrong_chapter(self):
        with self.legacy_task() as (fixture, task, path, record):
            revision = fixture.book.meta("revision")
            output = fixture.root / ".story/drafts/fragment.md"
            for prefix in ("\u200b", "\u200b\n", "\u2060\n", "\u0301", "\u0301\n", "\ufe0f\n",
                           "\u115f", "\u1160", "\u2800", "\u3164\n", "\uffa0", "\x01\n",
                           "\u200b\u0301\u3164\n \t"):
                for heading in ("第9章", "# 第９章 错章"):
                    with self.subTest(prefix=repr(prefix), heading=heading):
                        fragment = story.tasks.create(
                            fixture.root, "writing", "只写第1章的一个片段。", [], chapter=1)["task"]
                        task_path = fixture.root / story.tasks.DIRECTORY / (fragment["id"] + ".json")
                        before = task_path.read_bytes()
                        text = prefix + heading + "\n她推开门。\n"
                        output.write_bytes(text.encode("utf-8"))
                        result = {**fragment["result_template"], "summary": "返回一个待核对片段。", "issues": [],
                                  "output": {"path": ".story/drafts/fragment.md", "sha256": story.digest(text)}}
                        with self.assertRaises(story.StoryError) as error:
                            story.tasks.import_result(fixture.root, fragment["id"], fragment["version"], result)
                        self.assertEqual(error.exception.code, "task_output_scope")
                        self.assertEqual(task_path.read_bytes(), before)
                        self.assertEqual(output.read_bytes(), text.encode("utf-8"))
            self.assertEqual(fixture.book.meta("revision"), revision)
            self.assertEqual(fixture.book.meta("last_chapter"), 0)

    def test_fragment_heading_recognition_preserves_short_unicode_body_bytes(self):
        with self.legacy_task() as (fixture, task, path, record):
            revision = fixture.book.meta("revision")
            for number, text in enumerate((
                "\u200b\u0301\u3164\n第1章 当前片段\n她推开门。e\u0301🥳⠁한\n",
                "\u200b\n她推开门。e\u0301🥳⠁한\n",
                "한第9章只是台词的一部分。\n她推开门。\n",
            )):
                with self.subTest(number=number):
                    fragment = story.tasks.create(
                        fixture.root, "writing", "只写第1章的一个片段。", [], chapter=1)["task"]
                    relative = f".story/drafts/fragment-{number}.md"
                    output = fixture.root / relative
                    output.write_bytes(text.encode("utf-8"))
                    result = {**fragment["result_template"], "summary": "返回一个待核对片段。", "issues": [],
                              "output": {"path": relative, "sha256": story.digest(text)}}
                    observed = story.tasks.import_result(
                        fixture.root, fragment["id"], fragment["version"], result)["task"]
                    self.assertEqual(observed["status"], "needs_review")
                    self.assertEqual(observed["result"]["output"]["sha256"], story.digest(text))
                    self.assertLess(story.manuscript_counts(text, True)["visible_nonspace_v2"], 2000)
                    self.assertEqual(output.read_bytes(), text.encode("utf-8"))
            self.assertEqual(fixture.book.meta("revision"), revision)
            self.assertEqual(fixture.book.meta("last_chapter"), 0)

    def test_cli_fragment_return_rejects_an_invisible_wrong_heading_prefix(self):
        with self.legacy_task() as (fixture, task, path, record):
            for number, prefix in enumerate(("\u200b\n", "\u0301", "\u3164\n")):
                with self.subTest(prefix=repr(prefix)):
                    fragment = story.tasks.create(
                        fixture.root, "writing", "只写第1章的一个片段。", [], chapter=1)["task"]
                    task_path = fixture.root / story.tasks.DIRECTORY / (fragment["id"] + ".json")
                    before = task_path.read_bytes()
                    relative = f".story/drafts/cli-fragment-{number}.md"
                    text = prefix + "第9章 错章\n她推开门。\n"
                    output = fixture.root / relative
                    output.write_bytes(text.encode("utf-8"))
                    result = {**fragment["result_template"], "summary": "返回一个待核对片段。", "issues": [],
                              "output": {"path": relative, "sha256": story.digest(text)}}
                    result_file = f".story/drafts/cli-result-{number}.json"
                    (fixture.root / result_file).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                    completed = subprocess.run(
                        [sys.executable, "-B", str(TOOL), "task-return", "--book", str(fixture.root),
                         "--task-id", fragment["id"], "--version", str(fragment["version"]),
                         "--result-file", result_file], capture_output=True, text=True, timeout=30)
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertEqual(json.loads(completed.stdout or completed.stderr)["error"], "task_output_scope")
                    self.assertEqual(task_path.read_bytes(), before)
                    self.assertEqual(output.read_bytes(), text.encode("utf-8"))

    def test_legacy_manual_fragment_without_generated_envelope_remains_handoffable(self):
        with self.legacy_task() as (fixture, task, path, record):
            record["prompt"] = "只拟一个局部片段，关联章号不表示交付完整章。\n" + record["prompt"]
            path.write_bytes(story.dumps(record).encode("utf-8"))
            before = path.read_bytes()
            observed = story.tasks.get(fixture.root, task["id"])["task"]
            self.assertEqual(observed["stale_reasons"], [])
            self.assertEqual(path.read_bytes(), before)
            handed = story.tasks.mark_handoff(fixture.root, task["id"], task["version"])["task"]
            self.assertEqual(handed["status"], "handed_off")

    def test_explicit_fragment_scope_precedes_a_quoted_complete_brief(self):
        with self.legacy_task() as (fixture, task, path, record):
            fragment = story.tasks.create(
                fixture.root, "writing", record["prompt"], [], chapter=1, complete_chapter=False)["task"]
            observed = story.tasks.get(fixture.root, fragment["id"])["task"]
            self.assertEqual(observed["stale_reasons"], [])
            handed = story.tasks.mark_handoff(fixture.root, fragment["id"], fragment["version"])["task"]
            self.assertEqual(handed["status"], "handed_off")


if __name__ == "__main__":
    unittest.main()

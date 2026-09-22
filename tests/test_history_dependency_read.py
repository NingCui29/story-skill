"""Read published dependency declarations without planning or creating branches."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


TOOL = Path(__file__).resolve().parents[1] / "skills/story-codex/scripts/story.py"
SPEC = importlib.util.spec_from_file_location("history_dependency_read_story", TOOL)
story = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(story)
history = story.history


class HistoryDependencyReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-dependency-read-")
        self.root = Path(self.temp.name).resolve() / "book"
        story.Book.create(self.root, "依赖读取夹具", "long")
        self.book = story.Book(self.root)
        self.texts = {}

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def revision(self):
        return self.book.meta("revision")

    def snapshot(self):
        return (self.revision(), tuple(self.book.db.iterdump()), self.book.path.read_bytes())

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(story.StoryError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def commit(self, chapter, dependencies=(), note="原声明：已逐项核对所列依赖。"):
        text = f"# 第{chapter}章 留信\n周宁核对第{chapter}封信，随后把钥匙放在桌边。\n"
        plan = {"title": "留信", "volume_dir": "第一卷 交接", "goal": "核对信件",
                "stop": "留下钥匙", "requires": [], "length": [1, 200],
                "beats": [{"choice": "核对信件", "change": "留下钥匙"}]}
        self.book.save_plan(chapter, plan, self.revision())
        draft = self.root / "draft.md"
        draft.write_bytes(text.encode("utf-8"))
        raw = {"book_id": self.book.meta("id"), "base_revision": self.revision(),
               "summary": "核对信件后留下钥匙。", "changes": [],
               "dependencies": list(dependencies), "dependency_review": {"complete": True, "note": note},
               "review": {"draft_sha256": story.digest(text), "issues": [], "checks": {
                   key: {"note": "核对本次夹具的顺序与物件位置。", "quote": "随后把钥匙放在桌边。"}
                   for key in story.CHECKS}}}
        self.book.commit(chapter, draft, raw)
        self.texts[chapter] = text

    def cli(self, *args):
        return subprocess.run([sys.executable, "-B", str(TOOL), "history-deps", "--book", str(self.root),
                               *map(str, args)], capture_output=True, text=True, encoding="utf-8")

    def write_payload(self, payload):
        path = self.root / "dependencies.json"
        path.write_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        return path

    def test_imported_baseline_reads_without_plan_or_branch(self):
        draft = self.root / "import.md"
        text = "# 第8章 基线\n周宁把旧信留在桌边。\n"
        draft.write_bytes(text.encode("utf-8"))
        self.book.adopt(8, draft, "旧信留在桌边。", self.revision(), volume_dir="第一卷 交接")
        before = self.snapshot()
        result = history.read_dependencies(self.book, 8)
        self.assertEqual(result["chapter_sha"], story.digest(text))
        self.assertEqual(result["dependencies"], [])
        self.assertFalse(result["complete"])
        self.assertIsNone(result["note"])
        self.assertEqual(result["payload"]["note"], "")
        self.assertEqual(result["payload"]["chapter"], 8)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM plans").fetchone()[0], 0)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM history_branches").fetchone()[0], 0)
        self.assertEqual(self.snapshot(), before)

    def test_committed_declaration_is_returned_in_full_with_its_note(self):
        self.commit(1)
        dependencies = [{"kind": "chapter", "ref": "1", "sha": story.digest(self.texts[1])}]
        note = "本章取钥匙依赖第一章留下钥匙。"
        self.commit(2, dependencies, note)
        before = self.snapshot()
        result = history.read_dependencies(self.book, 2)
        self.assertEqual(result["dependencies"], dependencies)
        self.assertTrue(result["complete"])
        self.assertEqual(result["note"], note)
        self.assertEqual(result["revision"], self.revision())
        self.assertEqual(result["payload"], {"chapter": 2, "chapter_sha": story.digest(self.texts[2]),
                                              "dependencies": dependencies, "complete": True, "note": note})
        self.assertEqual(result["budget"]["used"], len(story.dumps(result).encode("utf-8")))
        self.assertEqual(self.snapshot(), before)

    def test_replacement_note_is_read_from_the_matching_declaration_event(self):
        self.commit(1, note="原始提交说明。")
        original = history.read_dependencies(self.book, 1)
        edited = {**original["payload"], "complete": False, "note": "复核发现仍有未核实依赖。"}
        history.save_dependencies(self.book, edited, self.revision())
        # An unrelated newer event must not supply this declaration's note.
        self.book.save_notes([{"id": "elsewhere", "text": "另一条记录。", "source": "用户补充"}], self.revision())
        before = self.snapshot()
        result = history.read_dependencies(self.book, 1)
        self.assertNotEqual(result["version"], original["version"])
        self.assertEqual(result["payload"], edited)
        self.assertEqual(result["note"], edited["note"])
        self.assertFalse(result["complete"])
        self.assertEqual(self.snapshot(), before)

    def test_read_keeps_recorded_hashes_when_current_records_and_candidates_differ(self):
        self.book.save_notes([{"id": "key", "text": "钥匙留在库房。", "source": "作者设定"}], self.revision())
        old_card_sha = story.digest(story.dumps(self.book.cards(["key"])["key"]))
        self.commit(1)
        dependencies = [{"kind": "card", "ref": "key", "sha": old_card_sha},
                        {"kind": "chapter", "ref": "1", "sha": story.digest(self.texts[1])}]
        self.commit(2, dependencies)
        self.book.save_notes([{"id": "key", "text": "钥匙现已取走。"}], self.revision())
        branch = history.branch_start(self.book, 1, self.revision())
        candidate = self.texts[1].replace("桌边", "柜上")
        history.branch_update(self.book, branch["branch"], {"chapters": [{"chapter": 1,
            "text": candidate, "summary": "钥匙改留柜上。", "dependencies": [], "complete": True}]}, self.revision())
        candidates = history.branch_dependencies(self.book, branch["branch"], 2)
        current = {(item["kind"], item["ref"]): item["sha"] for item in candidates["candidates"]}
        before = self.snapshot()
        result = history.read_dependencies(self.book, 2)
        self.assertEqual(result["dependencies"], dependencies)
        self.assertEqual(result["chapter_sha"], story.digest(self.texts[2]))
        self.assertNotEqual(current[("card", "key")], old_card_sha)
        self.assertEqual(current[("chapter", "1")], story.digest(candidate))
        self.assertNotEqual(current[("chapter", "1")], dependencies[1]["sha"])
        self.assertEqual(self.snapshot(), before)

    def test_read_is_one_snapshot_during_a_concurrent_declaration_update(self):
        self.commit(1)
        original = history.read_dependencies(self.book, 1)
        self.book.db.execute("PRAGMA journal_mode=WAL")
        writer = story.Book(self.root)
        edited = {**original["payload"], "complete": False, "note": "另一连接补正了核对范围。"}
        original_edges = history._edges

        def update_after_head(book, version):
            history.save_dependencies(writer, edited, writer.meta("revision"))
            return original_edges(book, version)

        try:
            with patch.object(history, "_edges", side_effect=update_after_head):
                result = history.read_dependencies(self.book, 1)
            self.assertEqual(result, original)
            self.assertFalse(self.book.db.in_transaction)
            self.assertEqual(history.read_dependencies(self.book, 1)["payload"], edited)
        finally:
            writer.close()

    def test_read_preserves_the_callers_transaction(self):
        self.commit(1)
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "caller rollback"):
            with self.book.transaction():
                self.book.set_meta("dependency_probe", "uncommitted")
                history.read_dependencies(self.book, 1)
                self.assertTrue(self.book.db.in_transaction)
                self.assertEqual(self.book.meta("dependency_probe"), "uncommitted")
                raise RuntimeError("caller rollback")
        self.assertEqual(self.snapshot(), before)

    def test_missing_chapter_or_history_does_not_create_state(self):
        before = self.snapshot()
        self.assert_code("chapter_missing", history.read_dependencies, self.book, 1)
        self.assertEqual(self.snapshot(), before)
        self.commit(1)
        with self.book.transaction():
            self.book.db.execute("DELETE FROM history_heads WHERE chapter=1")
        before = self.snapshot()
        self.assert_code("history_missing", history.read_dependencies, self.book, 1)
        self.assertEqual(self.snapshot(), before)

    def test_small_budget_rejects_the_whole_declaration_without_mutation(self):
        self.commit(1, note="核对说明。" * 200)
        before = self.snapshot()
        error = self.assert_code("budget_exceeded", history.read_dependencies, self.book, 1, budget=256)
        self.assertGreater(error.details["minimum_bytes"], 256)
        self.assertEqual(self.snapshot(), before)
        result = history.read_dependencies(self.book, 1, budget=error.details["minimum_bytes"] + 64)
        self.assertEqual(result["note"], "核对说明。" * 200)
        self.assertEqual(self.snapshot(), before)

    def test_cli_read_and_original_write_form_round_trip(self):
        self.commit(1)
        before = self.snapshot()
        read = self.cli("--chapter", 1)
        self.assertEqual(read.returncode, 0, read.stderr)
        packet = json.loads(read.stdout)
        self.assertEqual(self.snapshot(), before)
        payload = copy.deepcopy(packet["payload"])
        payload.update(complete=False, note="沿用原字段，重新记录尚未核清的范围。")
        written = self.cli("--input", self.write_payload(payload), "--expect", packet["revision"])
        self.assertEqual(written.returncode, 0, written.stderr)
        self.assertFalse(json.loads(written.stdout)["complete"])
        self.assertEqual(history.read_dependencies(self.book, 1)["payload"], payload)

    def test_invalid_budget_and_chapter_are_rejected_without_changes(self):
        self.commit(1)
        before = self.snapshot()
        for chapter, budget in ((0, 64000), (-1, 64000), (True, 64000), (1, 255), (1, 0)):
            with self.subTest(chapter=chapter, budget=budget):
                self.assert_code("invalid_input", history.read_dependencies, self.book, chapter, budget)
                self.assertEqual(self.snapshot(), before)

    def test_cli_stale_read_revision_cannot_overwrite_a_later_declaration(self):
        self.commit(1)
        old = json.loads(self.cli("--chapter", 1).stdout)
        latest = {**old["payload"], "complete": False, "note": "另一会话发现尚未核全。"}
        history.save_dependencies(self.book, latest, self.revision())
        before = self.snapshot()
        result = self.cli("--input", self.write_payload(old["payload"]), "--expect", old["revision"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["error"], "stale_revision")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(history.read_dependencies(self.book, 1)["payload"], latest)

    def test_cli_rejects_mixed_missing_or_invalid_mode_arguments(self):
        self.commit(1)
        payload = history.read_dependencies(self.book, 1)["payload"]
        path = self.write_payload(payload)
        before = self.snapshot()
        invalid = [(), ("--expect", self.revision()), ("--input", path),
                   ("--chapter", 1, "--expect", self.revision()),
                   ("--chapter", 1, "--input", path, "--expect", self.revision()),
                   ("--chapter", 0), ("--chapter", -1), ("--chapter", "not-a-number")]
        for args in invalid:
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(result.stdout)
                self.assertTrue(result.stderr)
                self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()

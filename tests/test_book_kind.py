"""Explicit fiction type correction preserves narrative records and protected exports."""
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from outline_fixture import bind_adopted_outline
import test_story as fixtures


story, TOOL = fixtures.story, fixtures.TOOL


class BookKindTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-book-kind-")
        self.root = Path(self.temp.name).resolve() / "书库"
        story.Book.create(self.root, "门后的雨", "long")
        self.book = story.Book(self.root)
        self.book.save_notes([fixtures.card()], self.revision())
        story.world.save(self.book, {"entities": [{"id": "door", "name": "旧门",
                         "kind": "place", "description": "门仍然留在原处。"}]}, self.revision())
        self.draft = self.root / ".story/drafts/章.md"
        self.draft.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def revision(self):
        return self.book.meta("revision")

    def cli(self, command, *arguments):
        process = subprocess.run([sys.executable, "-B", str(TOOL), command,
                                  "--book", str(self.root), *map(str, arguments)],
                                 capture_output=True, text=True, encoding="utf-8")
        result = json.loads(process.stdout if process.returncode == 0 else process.stderr)
        return process.returncode, result

    def snapshot(self):
        names = [row[0] for row in self.book.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        tables = {name: [tuple(row) for row in self.book.db.execute(
            'SELECT * FROM "' + name.replace('"', '""') + '"')] for name in names}
        exports = {row["path"]: (self.root / row["path"]).read_bytes()
                   if (self.root / row["path"]).is_file() else None
                   for row in self.book.db.execute("SELECT path FROM artifact_state")}
        return tables, exports

    def assert_code(self, code, call):
        with self.assertRaises(story.StoryError) as raised:
            call()
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def commit(self, chapter):
        title = f"查看{chapter}"
        quote = f"她完成第{chapter}次查看后，仍把钥匙留在门房。"
        text = f"第{chapter}章 {title}\n{quote}\n"
        self.book.save_plan(chapter, fixtures.plan(title=title, length=[1, 120]), self.revision())
        bind_adopted_outline(story, self.book, chapter)
        self.draft.write_text(text, encoding="utf-8")
        card = {**self.book.cards()["hero"], "text": f"她已完成第{chapter}次查看。", "quote": quote}
        delta = {"book_id": self.book.meta("id"), "base_revision": self.revision(),
                 "summary": quote, "changes": [card],
                 "review": {"draft_sha256": story.digest(text), "checks": {
                     name: {"note": "合成夹具中的行动与去向一致。", "quote": quote}
                     for name in story.CHECKS}, "issues": []}}
        result = self.book.commit(chapter, self.draft, delta)
        self.assertTrue(result["exports_complete"], result)
        return text

    def assert_only_kind_event_changed(self, before, after, previous, kind):
        old_tables, old_exports = before
        tables, exports = after
        self.assertEqual(exports, old_exports)
        self.assertEqual(set(tables), set(old_tables))
        for name in tables:
            if name not in ("meta", "events", "sqlite_sequence"):
                self.assertEqual(tables[name], old_tables[name], name)
        old_meta, meta = dict(old_tables["meta"]), dict(tables["meta"])
        revision = json.loads(old_meta["revision"])
        self.assertEqual(meta, {**old_meta, "kind": story.dumps(kind),
                               "revision": story.dumps(revision + 1)})
        self.assertEqual(tables["events"][:-1], old_tables["events"])
        event = tables["events"][-1]
        self.assertEqual(event[1:4], (revision + 1, "book_kind",
                                     story.dumps({"before": previous, "after": kind})))

    def test_cli_long_with_committed_history_can_correct_then_assemble_normally(self):
        texts = [self.commit(1), self.commit(2)]
        old_history = story.history.history_state(self.book, 2)
        before = self.snapshot()
        code, result = self.cli("book-kind", "--kind", "short", "--expect", self.revision())
        self.assertEqual(code, 0, result)
        self.assertTrue(result["changed"])
        self.assertEqual(result["previous_kind"], "long")
        self.assertEqual(result["kind"], "short")
        self.assert_only_kind_event_changed(before, self.snapshot(), "long", "short")
        self.assertEqual(story.history.history_state(self.book, 2), old_history)
        self.assertTrue(self.book.audit()["exports_complete"])
        code, assembled = self.cli("assemble-short", "--final-chapter", 2)
        self.assertEqual(code, 0, assembled)
        self.assertTrue(assembled["exports_complete"])
        output = Path(assembled["path"]).read_text(encoding="utf-8")
        for text in texts:
            heading, body = text.split("\n", 1)
            self.assertIn(heading + "\n\n" + body, output)
        self.assertEqual(self.book.status()["short_assembly"]["state"], "current")

    def test_history_state_after_new_chapter_replays_type_event_without_changing_cards(self):
        self.commit(1)
        original = story.history.history_state(self.book, 1)
        self.book.change_kind("short", self.revision())
        before_second = self.book.cards()
        self.commit(2)
        code, prior = self.cli("history-state", "--chapter", 2, "--before")
        self.assertEqual(code, 0, prior)
        self.assertEqual({card["id"]: card for card in prior["cards"]}, before_second)
        code, current = self.cli("history-state", "--chapter", 2)
        self.assertEqual(code, 0, current)
        self.assertEqual({card["id"]: card for card in current["cards"]}, self.book.cards())
        self.assertEqual(story.history.history_state(self.book, 1), original)

    def test_same_kind_is_noop_and_stale_expect_still_fails(self):
        self.commit(1)
        before = self.snapshot()
        database = self.book.path.read_bytes()
        result = self.book.change_kind("long", self.revision())
        self.assertFalse(result["changed"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.book.path.read_bytes(), database)
        self.assert_code("stale_revision", lambda: self.book.change_kind("long", self.revision() - 1))
        self.assert_code("stale_revision", lambda: self.book.change_kind("short", self.revision() - 1))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.book.path.read_bytes(), database)

    def test_failed_audit_event_rolls_back_kind_and_revision(self):
        before = self.snapshot()
        with patch.object(self.book, "event", side_effect=RuntimeError("audit write interrupted")):
            with self.assertRaisesRegex(RuntimeError, "audit write interrupted"):
                self.book.change_kind("short", self.revision())
        self.assertEqual(self.snapshot(), before)

    def test_reverse_without_reading_copy_is_safe_and_keeps_reserved_path(self):
        self.book.change_kind("short", self.revision())
        reserved = self.book.status()["short_assembly_path"]
        before = self.snapshot()
        self.book.change_kind("long", self.revision())
        self.assert_only_kind_event_changed(before, self.snapshot(), "short", "long")
        self.book.change_kind("short", self.revision())
        self.assertEqual(self.book.status()["short_assembly_path"], reserved)
        self.assertIsNone(self.book.status()["short_assembly"])

    def test_assembly_rechecks_kind_after_acquiring_operation_lock(self):
        self.commit(1)
        self.book.change_kind("short", self.revision())
        output = self.root / self.book.short_assembly_path()
        before = self.snapshot()
        other = story.Book(self.root)
        operation_lock = story.storage.operation_lock
        switched = False

        @contextmanager
        def interleave(book):
            nonlocal switched
            # Let another connection finish its type correction immediately
            # before assembly acquires the book's operation lock.
            if book is self.book and not switched:
                switched = True
                other.change_kind("long", other.meta("revision"))
            with operation_lock(book):
                yield

        try:
            with patch.object(story.storage, "operation_lock", interleave):
                self.assert_code("short_only", lambda: self.book.assemble_short(1))
        finally:
            other.close()
        self.assertTrue(switched)
        self.assert_only_kind_event_changed(before, self.snapshot(), "short", "long")
        self.assertIsNone(self.book.status()["short_assembly"])
        self.assertFalse(output.exists())

    def test_reverse_keeps_current_or_missing_protected_assembly_and_all_records(self):
        self.commit(1)
        self.book.change_kind("short", self.revision())
        assembled = self.book.assemble_short(1)
        output = Path(assembled["path"])
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    output.unlink()
                before = self.snapshot()
                database = self.book.path.read_bytes()
                code, result = self.cli("book-kind", "--kind", "long", "--expect", self.revision())
                self.assertEqual(code, 2, result)
                self.assertEqual(result["error"], "book_kind_protected_assembly")
                self.assertIn("short_assembly", result["protected_records"])
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.book.path.read_bytes(), database)
                self.assertEqual(output.exists(), not missing)

    def test_reverse_keeps_registered_copy_without_assembly_metadata(self):
        self.book.change_kind("short", self.revision())
        relative = self.book.short_assembly_path()
        with self.book.transaction():
            self.book.queue_artifact(relative, "已经登记、不可丢弃的阅读副本。\n")
        self.assertTrue(self.book.export()["exports_complete"])
        self.assertIsNone(self.book.status()["short_assembly"])
        before = self.snapshot()
        error = self.assert_code("book_kind_protected_assembly",
                                 lambda: self.book.change_kind("long", self.revision()))
        self.assertEqual(error.details["protected_path"], relative)
        self.assertEqual(self.snapshot(), before)

    def test_analysis_book_is_not_retyped_as_fiction(self):
        root = self.root / "分析库"
        story.Book.create(root, "参考资料", "analysis")
        book = story.Book(root)
        try:
            before = tuple(book.db.iterdump())
            self.assert_code("fiction_book_required", lambda: book.change_kind("short", 0))
            self.assertEqual(tuple(book.db.iterdump()), before)
        finally:
            book.close()


if __name__ == "__main__":
    unittest.main()

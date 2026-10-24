"""Adopted book titles update current views without moving or rewriting history."""
from contextlib import closing
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_publish as fixtures


story = fixtures.story


class BookTitleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublishTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.book, self.root = self.fixture.book, self.fixture.root

    def revision(self):
        return self.book.meta("revision")

    def assert_code(self, code, title, revision=None):
        with self.assertRaises(story.StoryError) as caught:
            self.book.change_title(title, self.revision() if revision is None else revision)
        self.assertEqual(caught.exception.code, code)

    def database(self):
        return tuple(self.book.db.iterdump())

    def exports(self):
        return {row["path"]: (self.root / row["path"]).read_bytes()
                for row in self.book.db.execute("SELECT path FROM artifact_state")}

    def test_cli_updates_status_workbench_and_new_manifest_preserving_old_snapshot(self):
        self.fixture.commit()
        old = self.fixture.prepare()
        frozen = fixtures.publishing.inspect(self.book, old["id"])
        ledger = self.fixture.ledger.read_bytes()
        identity = self.book.meta("id")
        exports = self.exports()
        old_title = self.book.meta("title")
        revision = self.revision()
        title = '新书名：门后的另一封信'
        result = subprocess.run([sys.executable, "-B", str(fixtures.TOOL), "book-title",
                                 "--book", str(self.root), "--title", title, "--expect", str(revision)],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual((receipt["previous_title"], receipt["title"]), (old_title, title))
        self.assertEqual(receipt["revision"], revision + 1)
        self.assertFalse(receipt["paths_changed"])
        self.assertEqual(receipt["book"], str(self.root))
        self.assertEqual(receipt["id"], identity)
        self.assertEqual(self.book.status()["title"], title)
        self.assertEqual(story.workbench.snapshot(self.book)["book"]["title"], title)
        self.assertEqual(self.exports(), exports)
        self.assertEqual(self.fixture.ledger.read_bytes(), ledger)
        self.assertEqual(fixtures.publishing.inspect(self.book, old["id"]), frozen)
        current = self.fixture.prepare()
        self.assertNotEqual(current["id"], old["id"])
        self.assertEqual(current["manifest"]["book_title"], title)
        self.assertEqual(fixtures.publishing.inspect(self.book, old["id"])["manifest"], frozen["manifest"])

    def test_same_title_noop_stale_writer_and_invalid_input_leave_state_intact(self):
        before = self.database()
        result = self.book.change_title(self.book.meta("title"), self.revision())
        self.assertFalse(result["changed"])
        self.assertEqual(self.database(), before)
        for value in ("", "  ", "书" * 201, None, 123):
            with self.subTest(title=value):
                self.assert_code("invalid_input", value)
        self.assert_code("placeholder", "<填写书名>")
        self.assertEqual(self.database(), before)
        old_revision = self.revision()
        with closing(story.Book(self.root)) as other:
            other.change_title("另一位编辑采用的书名", old_revision)
        after = self.database()
        self.assert_code("stale_revision", "后来的旧请求", old_revision)
        self.assert_code("stale_revision", self.book.meta("title"), old_revision)
        self.assertEqual(self.database(), after)

    def test_event_failure_rolls_back_title_and_revision(self):
        before = self.database()
        with patch.object(self.book, "event", side_effect=RuntimeError("event interrupted")):
            with self.assertRaisesRegex(RuntimeError, "event interrupted"):
                self.book.change_title("不会落库的新书名", self.revision())
        self.assertEqual(self.database(), before)

    def test_short_reading_copy_keeps_registered_path_and_can_recover_after_rename(self):
        self.book.change_kind("short", self.revision())
        self.fixture.commit()
        assembled = self.book.assemble_short(1)
        path = Path(assembled["path"])
        original = path.read_bytes()
        registration = self.book._short_assembly_record()
        unrelated = self.root / "采用的新书名.md"
        unrelated.write_text("作者另存的候选，不得覆盖。", encoding="utf-8")
        receipt = self.book.change_title("采用的新书名", self.revision())
        self.assertEqual(receipt["short_assembly_path"], path.name)
        self.assertEqual(self.book._short_assembly_record(), registration)
        self.assertEqual(path.read_bytes(), original)
        path.unlink()
        self.assertEqual(self.book.status()["short_assembly"]["state"], "missing")
        self.assertTrue(self.book.export()["exports_complete"])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.book.status()["short_assembly"]["state"], "current")
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "作者另存的候选，不得覆盖。")

    def test_legacy_short_fallback_is_pinned_before_title_change(self):
        self.book.change_kind("short", self.revision())
        self.assertIsNone(self.book.db.execute("SELECT 1 FROM meta WHERE key='short_assembly_path'").fetchone())
        previous_path = self.book.short_assembly_path()
        self.book.change_title("尚未合并的新书名", self.revision())
        self.assertEqual(self.book.short_assembly_path(), previous_path)
        self.fixture.commit()
        assembled = self.book.assemble_short(1)
        self.assertEqual(Path(assembled["path"]).name, previous_path)

    def test_title_event_does_not_break_later_history_replay(self):
        self.book.save_notes([{"id": "gate", "kind": "fact", "text": "旧门属于门房。",
                              "source": "用户设定", "tags": ["旧门"]}], self.revision())
        self.fixture.commit()
        first = story.history.history_state(self.book, 1)
        self.book.change_title("继续连载的新书名", self.revision())
        self.fixture.commit(2)
        self.assertEqual(story.history.history_state(self.book, 1), first)
        self.assertEqual(story.history.history_state(self.book, 2)["cards"], first["cards"])
        self.assertEqual(story.history.history_state(self.book, 2, before=True)["cards"], first["cards"])

    def test_renamed_legacy_text_copy_migrates_using_registered_name_and_keeps_backup(self):
        self.book.change_kind("short", self.revision())
        self.fixture.commit()
        markdown = Path(self.book.assemble_short(1)["path"])
        legacy = markdown.with_suffix(".txt")
        original = markdown.read_bytes()
        markdown.rename(legacy)
        record = self.book._short_assembly_record()
        with self.book.transaction():
            self.book.db.execute("UPDATE artifact_state SET path=? WHERE path=?", (legacy.name, markdown.name))
            record["path"] = legacy.name
            self.book.set_meta("short_assembly", record)
            self.book.set_meta("short_assembly_path", legacy.name)
        self.book.change_title("新书名不决定旧全文路径", self.revision())
        result = self.book.export()
        self.assertTrue(result["exports_complete"])
        self.assertEqual(markdown.read_bytes(), original)
        self.assertFalse(legacy.exists())
        self.assertTrue(any(Path(path).read_bytes() == original for path in result["backups"]))
        self.assertEqual(self.book.short_assembly_path(), markdown.name)
        self.assertFalse((self.root / "新书名不决定旧全文路径.md").exists())

    def test_reserved_legacy_text_name_is_preserved_on_first_assembly_after_rename(self):
        self.book.change_kind("short", self.revision())
        original = Path(self.book.short_assembly_path())
        with self.book.transaction():
            self.book.set_meta("short_assembly_path", original.with_suffix(".txt").as_posix())
        self.book.change_title("首次合并也保留预留名", self.revision())
        self.fixture.commit()
        result = self.book.assemble_short(1)
        self.assertEqual(Path(result["path"]).name, original.name)
        self.assertFalse((self.root / "首次合并也保留预留名.md").exists())


if __name__ == "__main__":
    unittest.main()

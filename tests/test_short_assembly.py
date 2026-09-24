import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


TOOL = Path(__file__).resolve().parents[1] / "skills/story-codex/scripts/story.py"
spec = importlib.util.spec_from_file_location("story_short_assembly", TOOL)
story = importlib.util.module_from_spec(spec)
spec.loader.exec_module(story)


class ShortAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-short-assembly-")
        self.root = Path(self.temp.name).resolve() / "书库"
        story.Book.create(self.root, "雨夜的钥匙", "short")
        self.book = story.Book(self.root)
        self.draft = self.root / ".story/drafts/章.md"
        self.draft.parent.mkdir(parents=True)

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def commit(self, number, title, text, replace_last=False, exports_complete=True):
        plan = {"title": title, "volume_dir": "第一卷 雨夜", "goal": "决定下一步", "stop": "选择后停笔",
                "constraints": [], "requires": [], "tags": [], "length": [1, 200],
                "beats": [{"choice": "她决定行动", "change": "局面发生变化"}]}
        self.book.save_plan(number, plan, self.book.meta("revision"))
        self.draft.write_bytes(text.encode("utf-8"))
        quote = text.splitlines()[1]
        delta = {"book_id": self.book.meta("id"), "base_revision": self.book.meta("revision"),
                 "summary": "她作出了选择。", "changes": [],
                 "review": {"draft_sha256": story.digest(text), "checks": {
                     key: {"note": "选择与结果已在正文核对。", "quote": quote}
                     for key in story.CHECKS}, "issues": []}}
        result = self.book.commit(number, self.draft, delta, replace_last=replace_last)
        self.assertEqual(result["exports_complete"], exports_complete, result)
        return result

    def assert_error(self, code, action):
        with self.assertRaises(story.StoryError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)

    def test_final_cli_creates_complete_plain_text_by_book_title(self):
        self.commit(1, "借钥", "第1章 借钥\n她从门房借到一把钥匙。\n")
        self.commit(2, "还钥", "# 第2章 还钥\n她归还钥匙，也还清了欠账。\n")
        command = [sys.executable, "-B", str(TOOL), "assemble-short", "--book", str(self.root),
                   "--final-chapter", "2"]
        first = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(first.returncode, 0, first.stderr)
        result = json.loads(first.stdout)
        output = self.root / "雨夜的钥匙.txt"
        self.assertEqual(Path(result["path"]), output.resolve())
        self.assertTrue(result["created"])
        self.assertEqual(result["chapters"], 2)
        self.assertEqual(output.read_text(encoding="utf-8"),
                         "第1章 借钥\n\n她从门房借到一把钥匙。\n\n"
                         "第2章 还钥\n\n她归还钥匙，也还清了欠账。\n")
        repeated = self.book.assemble_short(2)
        self.assertFalse(repeated["created"])
        self.assertFalse(repeated["updated"])
        self.assertIsNone(repeated["backup"])

    def test_incomplete_or_unresolved_chapters_do_not_create_file(self):
        self.commit(1, "借钥", "第1章 借钥\n她从门房借到一把钥匙。\n")
        output = self.root / "雨夜的钥匙.txt"
        self.assert_error("chapters_incomplete", lambda: self.book.assemble_short(2))
        self.assertFalse(output.exists())
        self.commit(2, "还钥", "第2章 还钥\n她归还钥匙，也还清了欠账。\n")
        chapter = self.root / self.book.chapter_path(1)
        chapter.unlink()
        self.assert_error("exports_unresolved", lambda: self.book.assemble_short(2))
        self.assertFalse(output.exists())
        self.book.export()
        self.assertTrue(self.book.assemble_short(2)["created"])

    def test_revision_reassembles_with_backup_but_preserves_external_edit(self):
        self.commit(1, "借钥", "第1章 借钥\n她从门房借到一把钥匙。\n")
        self.commit(2, "还钥", "第2章 还钥\n她归还钥匙，也还清了欠账。\n")
        original = self.book.assemble_short(2)
        output = Path(original["path"])
        old_text = output.read_text(encoding="utf-8")
        updated = self.commit(2, "还钥", "第2章 还钥\n她归还钥匙，还把欠账亲手注销。\n", replace_last=True)
        backup = next(path for path in updated["backups"] if Path(path).name == output.name)
        self.assertEqual(Path(backup).read_text(encoding="utf-8"), old_text)
        self.assertEqual(updated["short_assembly"]["state"], "current")
        refreshed = self.book.assemble_short(2)
        self.assertFalse(refreshed["updated"])
        self.assertIsNone(refreshed["backup"])
        self.assertIn("欠账亲手注销", output.read_text(encoding="utf-8"))
        output.write_bytes("读者手工批注，必须保留。\n".encode("utf-8"))
        self.assert_error("assembly_conflict", lambda: self.book.assemble_short(2))
        self.assertEqual(output.read_text(encoding="utf-8"), "读者手工批注，必须保留。\n")

    def test_all_heading_line_separators_preserve_the_entire_body(self):
        for index, separator in enumerate(("\n", "\r\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e",
                                            "\x85", "\u2028", "\u2029")):
            with self.subTest(separator=repr(separator)):
                text = separator.join(("第1章 借钥", "她从门房借到一把钥匙。", "", "她终于推开了门。", ""))
                self.commit(1, "借钥", text, replace_last=index > 0)
                result = self.book.assemble_short(1)
                expected_body = separator.join(("她从门房借到一把钥匙。", "", "她终于推开了门。", ""))
                self.assertEqual(Path(result["path"]).read_bytes().decode("utf-8"),
                                 "第1章 借钥\n\n" + expected_body)

    def legacy_title_line_copy(self):
        self.commit(1, "借钥", "第1章 借钥\n她从门房借到一把钥匙。\n")
        result = self.book.assemble_short(1)
        output = Path(result["path"])
        old_text = "雨夜的钥匙\n\n" + output.read_text(encoding="utf-8")
        old_sha = story.digest(old_text)
        record = self.book._short_assembly_record()
        record.update(format=1, sha256=old_sha)
        with self.book.transaction():
            self.book.db.execute("INSERT OR IGNORE INTO core_objects(sha,text) VALUES(?,?)",
                                 (old_sha, old_text))
            self.book.set_meta("short_assembly", record)
            self.book.db.execute("UPDATE artifact_state SET sha=?,written_sha=? WHERE path=?",
                                 (old_sha, old_sha, record["path"]))
        # Preserve the exact bytes registered above; text-mode writes convert
        # LF to CRLF on Windows and would turn this fixture into an outside edit.
        output.write_bytes(old_text.encode("utf-8"))
        return output, old_text

    def test_previous_title_line_format_is_stale_and_reassembled_with_backup(self):
        output, old_text = self.legacy_title_line_copy()
        self.assertEqual(self.book.status()["short_assembly"]["state"], "stale")

        refreshed = self.book.assemble_short(1)
        self.assertTrue(refreshed["updated"])
        self.assertEqual(Path(refreshed["backup"]).read_text(encoding="utf-8"), old_text)
        self.assertEqual(output.read_text(encoding="utf-8"),
                         "第1章 借钥\n\n她从门房借到一把钥匙。\n")
        self.assertEqual(self.book.status()["short_assembly"]["state"], "current")

    def test_safe_export_upgrades_legacy_copy_with_backup(self):
        output, old_text = self.legacy_title_line_copy()
        recovered = self.book.export(safe_only=True)
        self.assertTrue(recovered["exports_complete"])
        self.assertEqual(self.book.status()["short_assembly"]["state"], "current")
        self.assertEqual(output.read_text(encoding="utf-8"),
                         "第1章 借钥\n\n她从门房借到一把钥匙。\n")
        self.assertTrue(any(Path(path).read_text(encoding="utf-8") == old_text
                            for path in recovered["backups"]))

    def test_safe_export_preserves_external_edit_to_legacy_copy(self):
        output, _ = self.legacy_title_line_copy()
        outside_edit = "读者在旧全文补充的内容，不可覆盖。\n"
        output.write_text(outside_edit, encoding="utf-8")
        recovered = self.book.export(safe_only=True)
        self.assertFalse(recovered["exports_complete"])
        self.assertEqual(output.read_text(encoding="utf-8"), outside_edit)
        self.assertEqual(self.book.status()["short_assembly"]["state"], "changed")

    def test_missing_copy_is_reported_and_export_restores_it(self):
        self.commit(1, "借钥", "第1章 借钥\n她从门房借到一把钥匙。\n")
        output = Path(self.book.assemble_short(1)["path"])
        original = output.read_bytes()
        output.unlink()
        status = self.book.status()
        self.assertEqual(status["short_assembly"]["state"], "missing")
        self.assertIn(output.name, status["pending_exports"])
        self.assertFalse(status["integrity"]["full_book_verified"])
        self.assertTrue(self.book.export(safe_only=True)["exports_complete"])
        self.assertEqual(output.read_bytes(), original)

    def test_stale_copy_is_read_only_visible_and_recovers_after_reopening(self):
        self.commit(1, "借钥", "第1章 借钥\n她从门房借到一把钥匙。\n")
        output = Path(self.book.assemble_short(1)["path"])
        with patch.object(self.book, "export", side_effect=OSError("export interrupted")):
            self.commit(1, "借钥", "第1章 借钥\n她取得钥匙，当场推开了门。\n",
                        replace_last=True, exports_complete=False)
        self.book.close()
        self.book = story.Book(self.root)
        changes = self.book.db.total_changes
        status = self.book.status()
        self.assertEqual(self.book.db.total_changes, changes)
        self.assertEqual(status["short_assembly"]["state"], "stale")
        self.assertFalse(status["short_assembly"]["source_current"])
        self.assertIn(output.name, status["pending_exports"])
        self.assertFalse(status["integrity"]["full_book_verified"])
        repaired = self.book.export(safe_only=True)
        self.assertTrue(repaired["exports_complete"], repaired)
        self.assertEqual(repaired["short_assembly"]["state"], "current")
        self.assertIn("当场推开了门", output.read_text(encoding="utf-8"))

    def test_metadata_failure_cannot_leave_an_unregistered_output(self):
        self.commit(1, "借钥", "第1章 借钥\n她从门房借到一把钥匙。\n")
        output = self.root / "雨夜的钥匙.txt"
        set_meta = self.book.set_meta

        def fail_registration(key, value):
            if key == "short_assembly":
                raise sqlite3.OperationalError("registration interrupted")
            return set_meta(key, value)

        with patch.object(self.book, "set_meta", side_effect=fail_registration):
            with self.assertRaises(sqlite3.OperationalError):
                self.book.assemble_short(1)
        self.assertFalse(output.exists())
        self.assertIsNone(self.book._short_assembly_record())
        self.assertIsNone(self.book.db.execute("SELECT 1 FROM artifact_state WHERE path=?", (output.name,)).fetchone())
        self.commit(1, "借钥", "第1章 借钥\n她取得钥匙，当场推开了门。\n", replace_last=True)
        self.assertTrue(self.book.assemble_short(1)["exports_complete"])
        self.assertIn("当场推开了门", output.read_text(encoding="utf-8"))

    def test_publication_failure_keeps_durable_recovery_content(self):
        self.commit(1, "借钥", "第1章 借钥\n她从门房借到一把钥匙。\n")

        def fail_publication(target, content, accepted, backup):
            self.assertFalse(self.book.db.in_transaction)
            with sqlite3.connect(self.root / ".story/state.sqlite3") as reader:
                self.assertIsNotNone(reader.execute("SELECT value FROM meta WHERE key='short_assembly'").fetchone())
                self.assertEqual(reader.execute("SELECT sha FROM artifact_state WHERE path=?",
                                                (target.name,)).fetchone()[0], story.digest(content))
            raise OSError("publication interrupted")

        with patch.object(story, "atomic_write", side_effect=fail_publication):
            failed = self.book.assemble_short(1)
        self.assertFalse(failed["exports_complete"])
        self.assertFalse(failed["created"])
        output = Path(failed["path"])
        self.assertFalse(output.exists())
        self.assertIsNotNone(self.book._short_assembly_record())
        self.book.close()
        self.book = story.Book(self.root)
        self.assertTrue(self.book.export(safe_only=True)["exports_complete"])
        self.assertIn("她从门房借到一把钥匙", output.read_text(encoding="utf-8"))

    def test_acknowledgement_failure_recovers_without_false_external_conflict(self):
        self.commit(1, "借钥", "第1章 借钥\n她从门房借到一把钥匙。\n")
        self.book.db.execute("""CREATE TEMP TRIGGER fail_assembly_ack BEFORE UPDATE OF written_sha ON artifact_state
            WHEN NEW.path = '雨夜的钥匙.txt' BEGIN SELECT RAISE(ABORT, 'ack interrupted'); END""")
        failed = self.book.assemble_short(1)
        self.assertFalse(failed["exports_complete"])
        self.assertEqual(failed["export_details"]["code"], "sqlite_error")
        output = Path(failed["path"])
        self.assertTrue(output.exists())
        self.assertEqual(self.book.status()["short_assembly"]["state"], "pending")
        retry = self.book.export(safe_only=True)
        self.assertFalse(retry["exports_complete"])
        self.assertEqual(retry["export_errors"][0]["code"], "sqlite_error")
        self.book.db.execute("DROP TRIGGER fail_assembly_ack")
        self.assertTrue(self.book.export(safe_only=True)["exports_complete"])
        self.commit(1, "借钥", "第1章 借钥\n她取得钥匙，当场推开了门。\n", replace_last=True)
        self.assertIn("当场推开了门", output.read_text(encoding="utf-8"))

    def test_unrelated_plan_changes_and_added_chapters_do_not_block_writing(self):
        self.commit(1, "借钥", "第1章 借钥\n她从门房借到一把钥匙。\n")
        output = Path(self.book.assemble_short(1)["path"])
        plan = self.book.get_plan(1)
        plan["goal"] = "归还借来的钥匙"
        self.book.save_plan(2, plan, self.book.meta("revision"))
        self.assertEqual(self.book.status()["short_assembly"]["state"], "current")
        self.commit(2, "还钥", "第2章 还钥\n她归还钥匙，也还清了欠账。\n")
        self.assertEqual(self.book.status()["short_assembly"]["chapters"], 2)
        self.assertIn("第2章 还钥", output.read_text(encoding="utf-8"))

    def test_safe_export_repairs_chapters_without_overwriting_edited_copy(self):
        self.commit(1, "借钥", "第1章 借钥\n她从门房借到一把钥匙。\n")
        output = Path(self.book.assemble_short(1)["path"])
        output.write_bytes("读者手工批注，必须保留。\n".encode("utf-8"))
        chapter = self.root / self.book.chapter_path(1)
        chapter.unlink()
        result = self.book.export(safe_only=True)
        self.assertFalse(result["exports_complete"])
        self.assertEqual(result["short_assembly"]["state"], "changed")
        self.assertIn(output.name, result["changed_exports"])
        self.assertTrue(chapter.exists())
        self.assertEqual(output.read_text(encoding="utf-8"), "读者手工批注，必须保留。\n")

    def test_legacy_metadata_only_copy_is_registered_or_preserved_safely(self):
        self.commit(1, "借钥", "第1章 借钥\n她从门房借到一把钥匙。\n")
        output = Path(self.book.assemble_short(1)["path"])
        original = output.read_bytes()
        for mode in ("existing", "missing", "edited"):
            with self.subTest(mode=mode):
                old = self.book._short_assembly_record()
                old.pop("format", None)
                old.pop("chapter_paths", None)
                with self.book.transaction():
                    self.book.set_meta("short_assembly", old)
                    self.book.db.execute("DELETE FROM artifact_state WHERE path=?", (output.name,))
                if mode == "missing":
                    output.unlink()
                elif mode == "edited":
                    output.write_bytes("读者手工批注，必须保留。\n".encode("utf-8"))
                result = self.book.export(safe_only=True)
                self.assertEqual(result["exports_complete"], mode != "edited", result)
                if mode != "edited":
                    self.assertEqual(output.read_bytes(), original)
                    self.assertEqual(result["short_assembly"]["state"], "current")
                else:
                    self.assertEqual(output.read_text(encoding="utf-8"), "读者手工批注，必须保留。\n")

    def test_long_book_is_rejected(self):
        self.book.close()
        self.temp.cleanup()
        self.temp = tempfile.TemporaryDirectory(prefix="story-long-assembly-")
        self.root = Path(self.temp.name).resolve() / "长篇"
        story.Book.create(self.root, "长篇", "long")
        self.book = story.Book(self.root)
        self.assert_error("short_only", lambda: self.book.assemble_short(1))


if __name__ == "__main__":
    unittest.main()

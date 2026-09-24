import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


TOOL = Path(__file__).resolve().parents[1] / "skills/story-skill/scripts/story.py"
spec = importlib.util.spec_from_file_location("story_export_recovery", TOOL)
story = importlib.util.module_from_spec(spec)
spec.loader.exec_module(story)

DRAFT = "# 门后的雨\n沈禾把唯一的钥匙交给守门人。\n她答应在天亮之前带回账本。\n"
REVISED = "# 门后的雨\n沈禾收回了唯一的钥匙。\n她决定另找入口，守门人退回雨里。\n"


class ExportRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-export-recovery-")
        self.root = Path(self.temp.name).resolve() / "book"
        story.Book.create(self.root, "门后的雨", "long")
        self.book = story.Book(self.root)
        self.draft = self.root / ".story/drafts/chapter.md"
        self.draft.parent.mkdir(parents=True)
        self.draft.write_bytes(DRAFT.encode("utf-8"))
        plan = {"volume_dir": "第一卷 雨夜", "goal": "决定钥匙的去向", "stop": "选择入口后停笔", "constraints": [],
                "requires": [], "tags": [], "length": [20, 120],
                "beats": [{"choice": "沈禾决定是否交出钥匙", "change": "失去或保留退路"}]}
        for chapter in (1, 2):
            self.book.save_plan(chapter, plan, self.book.meta("revision"))
            self.book.commit(chapter, self.draft, self.delta())
        self.first = self.root / self.book.chapter_path(1)
        self.second = self.root / self.book.chapter_path(2)

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def delta(self, text=DRAFT, quote="沈禾把唯一的钥匙交给守门人。"):
        return {"book_id": self.book.meta("id"), "base_revision": self.book.meta("revision"),
                "summary": "沈禾选择钥匙的去向。", "changes": [],
                "review": {"draft_sha256": story.digest(text), "checks": {
                    check: {"note": "正文体现人物选择与后果。", "quote": quote}
                    for check in story.CHECKS}, "issues": []}}

    def assert_story_error(self, code, operation):
        with self.assertRaises(story.StoryError) as result:
            operation()
        self.assertEqual(result.exception.code, code)

    def test_cli_safe_export_then_reconcile_resolves_missing_and_external_edit(self):
        self.first.unlink()
        self.second.write_bytes(REVISED.encode("utf-8"))
        revision = self.book.meta("revision")
        self.assert_story_error("export_conflict", self.book.export)
        self.assert_story_error("exports_unresolved", lambda: self.book.reconcile(2))
        self.assertFalse(self.first.exists())

        process = subprocess.run([sys.executable, "-B", str(TOOL), "export", "--book", str(self.root),
                                  "--safe-only"], capture_output=True)
        self.assertEqual(process.returncode, 2, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["exported"], [self.book.chapter_path(1)])
        self.assertFalse(result["exports_complete"])
        self.assertEqual(result["pending_export_count"], 0)
        self.assertEqual(result["changed_exports"], [self.book.chapter_path(2)])
        self.assertEqual(result["changed_export_count"], 1)
        self.assertEqual(self.first.read_bytes(), DRAFT.encode("utf-8"))
        self.assertEqual(self.second.read_bytes(), REVISED.encode("utf-8"))
        self.assertEqual(self.book.meta("revision"), revision)

        repeated = self.book.export(safe_only=True)
        self.assertEqual(repeated["exported"], [])
        self.assertFalse(repeated["exports_complete"])
        self.assertEqual(self.second.read_bytes(), REVISED.encode("utf-8"))
        packet = self.book.reconcile(2)
        self.draft.write_bytes(REVISED.encode("utf-8"))
        delta = self.delta(REVISED, "沈禾收回了唯一的钥匙。")
        delta["external_sha256"] = packet["external_edit"]["sha256"]
        committed = self.book.reconcile(2, self.draft, delta)
        self.assertTrue(committed["committed"])
        self.assertTrue(committed["exports_complete"])
        retried = self.book.reconcile(2, self.draft, delta)
        self.assertTrue(retried["idempotent"])
        self.assertTrue(retried["exports_complete"])
        self.assertEqual(self.book.meta("revision"), revision + 1)
        finished = self.book.export(safe_only=True)
        self.assertTrue(finished["exports_complete"])
        self.assertEqual(finished["exported"], [])
        self.assertEqual(finished["pending_export_count"], 0)
        self.assertEqual(finished["changed_export_count"], 0)

    def test_safe_export_recovers_known_pending_version_beside_outside_edit(self):
        self.draft.write_bytes(REVISED.encode("utf-8"))
        delta = self.delta(REVISED, "沈禾收回了唯一的钥匙。")
        with patch.object(story, "atomic_write", side_effect=OSError("temporary publication failure")):
            failed = self.book.commit(2, self.draft, delta, replace_last=True)
        self.assertFalse(failed["exports_complete"])
        self.assertEqual(self.second.read_bytes(), DRAFT.encode("utf-8"))
        outside = "第一章由用户另行编辑，必须保留。\n".encode("utf-8")
        self.first.write_bytes(outside)
        revision = self.book.meta("revision")

        recovered = self.book.export(safe_only=True)
        self.assertEqual(recovered["exported"], [self.book.chapter_path(2)])
        self.assertFalse(recovered["exports_complete"])
        self.assertEqual(recovered["pending_export_count"], 0)
        self.assertEqual(recovered["changed_exports"], [self.book.chapter_path(1)])
        self.assertEqual(self.first.read_bytes(), outside)
        self.assertEqual(self.second.read_bytes(), REVISED.encode("utf-8"))
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertTrue(recovered["backups"])
        self.assertEqual(Path(recovered["backups"][0]).read_bytes(), DRAFT.encode("utf-8"))

    def test_path_conflict_does_not_block_an_independent_missing_export(self):
        self.first.unlink()
        self.second.unlink()
        self.second.mkdir()
        marker = self.second / "user.txt"
        marker.write_bytes(b"User-owned directory contents")

        result = self.book.export(safe_only=True)
        self.assertEqual(result["exported"], [self.book.chapter_path(1)])
        self.assertFalse(result["exports_complete"])
        self.assertEqual(result["changed_exports"], [self.book.chapter_path(2)])
        self.assertEqual(result["export_errors"][0]["code"], "export_conflict")
        self.assertEqual(self.first.read_bytes(), DRAFT.encode("utf-8"))
        self.assertTrue(self.second.is_dir())
        self.assertEqual(marker.read_bytes(), b"User-owned directory contents")

    def test_file_recreated_during_safe_publication_is_not_overwritten(self):
        self.first.unlink()
        outside = b"Editor saved this file while recovery was running"
        original_publish = story._publish_no_replace
        recreated = False

        def editor_recreates_target(source, target, *args, **kwargs):
            nonlocal recreated
            if Path(target) == self.first and not recreated:
                self.first.write_bytes(outside)
                recreated = True
            return original_publish(source, target, *args, **kwargs)

        with patch.object(story, "_publish_no_replace", side_effect=editor_recreates_target):
            result = self.book.export(safe_only=True)
        self.assertTrue(recreated)
        self.assertFalse(result["exports_complete"])
        self.assertEqual(result["changed_exports"], [self.book.chapter_path(1)])
        self.assertEqual(result["pending_export_count"], 0)
        self.assertEqual(self.first.read_bytes(), outside)
        self.assertEqual(self.second.read_bytes(), DRAFT.encode("utf-8"))
        self.assertEqual(list(self.first.parent.glob(".story-tmp-*")), [])

    def test_publication_conflict_retains_backup_details_after_global_health_check(self):
        self.draft.write_bytes(REVISED.encode("utf-8"))
        delta = self.delta(REVISED, "沈禾收回了唯一的钥匙。")
        with patch.object(story, "atomic_write", side_effect=OSError("temporary publication failure")):
            self.book.commit(2, self.draft, delta, replace_last=True)
        outside = b"Editor saved new contents immediately before displacement"
        original_replace = story.os.replace
        edited = False

        def editor_saves_before_displacement(source, target, *args, **kwargs):
            nonlocal edited
            source_matches = Path(source) == self.second or (
                Path(source) == Path(self.second.name) and kwargs.get("src_dir_fd") is not None and
                story.os.path.samestat(self.second.parent.stat(), story.os.fstat(kwargs["src_dir_fd"])))
            if source_matches and not edited:
                self.second.write_bytes(outside)
                edited = True
            return original_replace(source, target, *args, **kwargs)

        with patch.object(story.os, "replace", side_effect=editor_saves_before_displacement):
            result = self.book.export(safe_only=True)
        self.assertTrue(edited)
        self.assertFalse(result["exports_complete"])
        self.assertEqual(result["changed_exports"], [self.book.chapter_path(2)])
        self.assertEqual(self.second.read_bytes(), outside)
        self.assertEqual(result["export_error_count"], 1)
        error = result["export_errors"][0]
        self.assertEqual(error["code"], "export_conflict")
        self.assertEqual(Path(error["details"]["backup"]).read_bytes(), outside)

    def test_linked_path_is_skipped_without_touching_its_external_target(self):
        self.first.unlink()
        self.second.unlink()
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        marker = outside / "user.txt"
        marker.write_bytes(b"External directory contents")
        try:
            self.second.symlink_to(outside, target_is_directory=True)
        except OSError:
            if story.os.name != "nt":
                self.skipTest("Creating links is unavailable")
            environment = dict(story.os.environ, STORY_RECOVERY_LINK=str(self.second),
                               STORY_RECOVERY_TARGET=str(outside))
            process = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 "New-Item -ItemType Junction -Path $env:STORY_RECOVERY_LINK "
                 "-Target $env:STORY_RECOVERY_TARGET -ErrorAction Stop | Out-Null"],
                env=environment, capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if process.returncode:
                self.skipTest("Neither symlink nor Windows junction creation is available")

        result = self.book.export(safe_only=True)
        self.assertEqual(result["exported"], [self.book.chapter_path(1)])
        self.assertFalse(result["exports_complete"])
        self.assertEqual(result["changed_exports"], [self.book.chapter_path(2)])
        self.assertIn(result["export_errors"][0]["code"], ("linked_path", "path_escape"))
        self.assertEqual(self.first.read_bytes(), DRAFT.encode("utf-8"))
        self.assertEqual(marker.read_bytes(), b"External directory contents")
        self.assertEqual(list(outside.iterdir()), [marker])

    def test_io_failure_preserves_partial_progress_and_reports_remaining_pending(self):
        self.first.unlink()
        self.second.unlink()
        original_write = story.atomic_write

        def fail_one_path(target, *args, **kwargs):
            if target == self.first:
                raise OSError("first path is temporarily unavailable")
            return original_write(target, *args, **kwargs)

        with patch.object(story, "atomic_write", side_effect=fail_one_path):
            result = self.book.export(safe_only=True)
        self.assertEqual(result["exported"], [self.book.chapter_path(2)])
        self.assertFalse(result["exports_complete"])
        self.assertEqual(result["pending_exports"], [self.book.chapter_path(1)])
        self.assertEqual(result["pending_export_count"], 1)
        self.assertEqual(result["changed_export_count"], 0)
        self.assertEqual(result["export_error_count"], 1)
        self.assertFalse(self.first.exists())
        self.assertEqual(self.second.read_bytes(), DRAFT.encode("utf-8"))
        retry = self.book.export(safe_only=True)
        self.assertTrue(retry["exports_complete"])
        self.assertEqual(retry["exported"], [self.book.chapter_path(1)])
        self.assertEqual(retry["pending_export_count"], 0)
        self.assertEqual(retry["changed_export_count"], 0)


if __name__ == "__main__":
    unittest.main()

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/story-skill/scripts/story.py"
spec = importlib.util.spec_from_file_location("story_portable_export", TOOL)
story = importlib.util.module_from_spec(spec)
spec.loader.exec_module(story)

DRAFT = "# 门后的雨\n沈禾把唯一的钥匙交给守门人。\n她答应在天亮之前带回账本。\n"
REVISED = DRAFT + "她没有回头。\n"


@unittest.skipUnless(os.name == "nt", "Windows publication without hard links")
class PortableWindowsExportTests(unittest.TestCase):
    def setUp(self):
        # Keep repeatable unit fixtures on the system temporary filesystem.
        # Real workspace/exFAT acceptance is exercised by the Chinese benchmark.
        self.temp = tempfile.TemporaryDirectory(prefix="story-portable-export-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "book"
        self.links = patch.object(story.os, "link", side_effect=AssertionError("Hard links are unavailable"))
        self.link_mock = self.links.start()
        self.addCleanup(self.links.stop)
        story.Book.create(self.root, "门后的雨", "long")
        self.book = story.Book(self.root)
        self.addCleanup(self.book.close)
        plan = {"volume_dir": "第一卷 雨夜", "goal": "决定钥匙的去向", "stop": "选择入口后停笔", "constraints": [],
                "requires": [], "tags": [], "length": [20, 120],
                "beats": [{"choice": "沈禾决定是否交出钥匙", "change": "失去或保留退路"}]}
        self.book.save_plan(1, plan, self.book.meta("revision"))
        self.draft = self.root / ".story/drafts/chapter.md"
        self.draft.parent.mkdir(parents=True)
        self.draft.write_bytes(DRAFT.encode("utf-8"))
        self.target = self.root / "chapters/第一卷 雨夜/第1章 门后的雨.md"

    def delta(self, text=DRAFT):
        return {"book_id": self.book.meta("id"), "base_revision": self.book.meta("revision"),
                "summary": "沈禾交出钥匙，承诺天亮前返回。", "changes": [],
                "review": {"draft_sha256": story.digest(text), "checks": {
                    check: {"note": "人物选择与后果已经核对。", "quote": "沈禾把唯一的钥匙交给守门人。"}
                    for check in story.CHECKS}, "issues": []}}

    def assert_no_stages(self):
        self.assertEqual(list(self.target.parent.glob(".story-tmp-*")), [])
        self.assertEqual(list(self.target.parent.glob(".story-restore-*")), [])
        self.link_mock.assert_not_called()

    def test_first_export_succeeds_without_hard_links(self):
        result = self.book.commit(1, self.draft, self.delta())
        self.assertTrue(result["committed"])
        self.assertTrue(result["exports_complete"])
        self.assertEqual(self.target.read_bytes(), DRAFT.encode("utf-8"))
        self.assertEqual(result["backups"], [])
        self.assert_no_stages()

    def test_revision_preserves_old_backup_without_hard_links(self):
        self.book.commit(1, self.draft, self.delta())
        self.draft.write_bytes(REVISED.encode("utf-8"))
        result = self.book.commit(1, self.draft, self.delta(REVISED), replace_last=True)
        self.assertTrue(result["exports_complete"])
        self.assertEqual(self.target.read_bytes(), REVISED.encode("utf-8"))
        self.assertEqual(len(result["backups"]), 1)
        self.assertEqual(Path(result["backups"][0]).read_bytes(), DRAFT.encode("utf-8"))
        self.assert_no_stages()

    def test_recreated_target_wins_over_publication_and_restore(self):
        self.book.commit(1, self.draft, self.delta())
        self.draft.write_bytes(REVISED.encode("utf-8"))
        external = b"Editor recreated the target before publication"
        publish = story._publish_no_replace

        def editor_saves_before_publication(source, target, *args, **kwargs):
            if Path(source).name.startswith(".story-tmp-"):
                self.assertFalse(self.target.exists())
                self.target.write_bytes(external)
            return publish(source, target, *args, **kwargs)

        with patch.object(story, "_publish_no_replace", side_effect=editor_saves_before_publication):
            result = self.book.commit(1, self.draft, self.delta(REVISED), replace_last=True)
        self.assertTrue(result["committed"])
        self.assertFalse(result["exports_complete"])
        self.assertEqual(self.target.read_bytes(), external)
        self.assertEqual(Path(result["export_details"]["backup"]).read_bytes(), DRAFT.encode("utf-8"))
        self.assert_no_stages()

    def test_failed_publication_restores_a_copy_and_retains_backup_for_retry(self):
        self.book.commit(1, self.draft, self.delta())
        self.draft.write_bytes(REVISED.encode("utf-8"))
        delta = self.delta(REVISED)
        publish = story._publish_no_replace

        def fail_new_stage(source, target, *args, **kwargs):
            if Path(source).name.startswith(".story-tmp-"):
                raise OSError("publication interrupted")
            return publish(source, target, *args, **kwargs)

        with patch.object(story, "_publish_no_replace", side_effect=fail_new_stage):
            result = self.book.commit(1, self.draft, delta, replace_last=True)
        self.assertTrue(result["committed"])
        self.assertFalse(result["exports_complete"])
        revision = result["revision"]
        backup = Path(result["export_details"]["backup"])
        self.assertEqual(backup.read_bytes(), DRAFT.encode("utf-8"))
        self.assertEqual(self.target.read_bytes(), DRAFT.encode("utf-8"))
        self.assert_no_stages()
        retry = self.book.commit(1, self.draft, delta, replace_last=True)
        self.assertTrue(retry["idempotent"])
        self.assertTrue(retry["exports_complete"])
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertEqual(self.target.read_bytes(), REVISED.encode("utf-8"))
        self.assertEqual(backup.read_bytes(), DRAFT.encode("utf-8"))
        self.assert_no_stages()

    def test_editor_save_during_restore_is_preserved_with_backup(self):
        self.book.commit(1, self.draft, self.delta())
        self.draft.write_bytes(REVISED.encode("utf-8"))
        external = b"Editor saved while the old file was being restored"
        publish = story._publish_no_replace

        def fail_publish_then_race_restore(source, target, *args, **kwargs):
            if Path(source).name.startswith(".story-tmp-"):
                raise OSError("publication interrupted")
            if Path(source).name.startswith(".story-restore-"):
                self.assertFalse(self.target.exists())
                self.target.write_bytes(external)
            return publish(source, target, *args, **kwargs)

        with patch.object(story, "_publish_no_replace", side_effect=fail_publish_then_race_restore):
            result = self.book.commit(1, self.draft, self.delta(REVISED), replace_last=True)
        self.assertFalse(result["exports_complete"])
        self.assertEqual(self.target.read_bytes(), external)
        self.assertEqual(Path(result["export_details"]["backup"]).read_bytes(), DRAFT.encode("utf-8"))
        self.assert_no_stages()


class PublicationSelectionTests(unittest.TestCase):
    def test_posix_keeps_no_replace_link_publication(self):
        with patch.object(story.os, "name", "posix"), patch.object(story.os, "link") as link, \
                patch.object(story.os, "rename") as rename:
            story._publish_no_replace("stage", "target")
        link.assert_called_once_with("stage", "target")
        rename.assert_not_called()


if __name__ == "__main__":
    unittest.main()

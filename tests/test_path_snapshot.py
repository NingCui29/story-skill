"""Chapter path metadata and search evidence must come from one read snapshot."""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


TOOL = Path(__file__).resolve().parents[1] / "skills/story-skill/scripts/story.py"
SPEC = importlib.util.spec_from_file_location("story_path_snapshot", TOOL)
story = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(story)

QUOTE = "沈禾把唯一的钥匙交给守门人。"
OLD_TEXT = "# 第1章 门后的雨\n" + QUOTE + "\n她答应天亮前带回账本。\n"
NEW_TEXT = OLD_TEXT.replace("门后的雨", "雨后的门") + "她没有回头。\n"


class PathSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-path-snapshot-")
        self.root = Path(self.temp.name).resolve() / "book"
        story.Book.create(self.root, "门后的雨", "long")
        self.book = story.Book(self.root)
        self.draft = self.root / "draft.md"
        self.book.save_plan(1, {
            "volume_dir": "第一卷 雨夜", "goal": "用钥匙换取入口", "stop": "进入门内",
            "beats": [{"choice": "交出钥匙", "change": "得到入口并失去退路"}],
            "constraints": [], "requires": [], "tags": [], "length": [20, 120],
        }, self.book.meta("revision"))
        self.draft.write_bytes(OLD_TEXT.encode("utf-8"))
        self.book.commit(1, self.draft, self.delta(self.book, OLD_TEXT))

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def delta(self, book, text):
        return {
            "book_id": book.meta("id"), "base_revision": book.meta("revision"),
            "summary": "沈禾交出钥匙进入门内。", "changes": [],
            "review": {"draft_sha256": story.digest(text), "checks": {
                name: {"note": "人物选择与后果相接。", "quote": QUOTE}
                for name in story.CHECKS
            }, "issues": []},
        }

    def test_recall_keeps_original_path_when_another_writer_renames_after_snapshot(self):
        original_path = self.book.chapter_path(1)
        read_snapshot = self.book.read_snapshot
        publications = []

        @contextmanager
        def rename_after_snapshot():
            with read_snapshot():
                yield
            writer = story.Book(self.root)
            try:
                self.draft.write_bytes(NEW_TEXT.encode("utf-8"))
                publications.append(writer.commit(1, self.draft, self.delta(writer, NEW_TEXT),
                                                   replace_last=True))
            finally:
                writer.close()

        with patch.object(self.book, "read_snapshot", rename_after_snapshot):
            result = self.book.recall("门后的雨", budget=2000)

        self.assertEqual(len(publications), 1)
        self.assertTrue(publications[0]["exports_complete"])
        self.assertNotEqual(self.book.chapter_path(1), original_path)
        self.assertFalse((self.root / original_path).exists())
        match = next(item for item in result["matches"] if item["type"] == "chapter")
        self.assertEqual(match["path"], original_path)
        self.assertEqual(match["source_sha256"], story.digest(OLD_TEXT))
        self.assertIn("门后的雨", match["snippet"])
        archived = self.book.chapter_read(1, sha=match["source_sha256"])
        self.assertEqual(archived["text"], OLD_TEXT)
        self.assertLessEqual(len(story.dumps(result).encode("utf-8")), 2000)

    def test_published_history_retry_exposes_current_paths_in_bounded_inspection_pages(self):
        old_path = self.book.chapter_path(1)
        second_text = OLD_TEXT.replace("第1章 门后的雨", "第2章 渡口的灯")
        self.book.save_plan(2, self.book.get_plan(1), self.book.meta("revision"))
        self.draft.write_bytes(second_text.encode("utf-8"))
        self.book.commit(2, self.draft, self.delta(self.book, second_text))
        packet = story.history.branch_start(self.book, 1, self.book.meta("revision"))
        candidates = []
        for chapter, text in ((1, NEW_TEXT), (2, second_text)):
            candidate = {"chapter": chapter, "text": text, "sha": story.digest(text),
                         "summary": "沈禾保留交接凭据。", "dependencies": [], "complete": True}
            candidate["review"] = {
                "draft_sha256": candidate["sha"],
                "candidate_sha256": story.history.candidate_fingerprint(candidate),
                "checks": {name: {"note": "选择与后果相接。", "quote": QUOTE}
                           for name in story.CHECKS}, "issues": [],
            }
            candidates.append(candidate)
        staged = story.history.branch_update(self.book, packet["branch"], {"chapters": candidates},
                                             self.book.meta("revision"))
        semantic = {**staged["review_template"], "note": "逐段核对全部受影响章。",
                    "state_review": "最终卡片与正文一致。", "coverage_review": "已核对全部人物和场景依赖。"}
        staged = story.history.branch_update(self.book, packet["branch"], {"semantic_review": semantic},
                                             self.book.meta("revision"))
        published = story.history.branch_publish(self.book, packet["branch"], staged["revision"])
        self.assertTrue(published["exports_complete"])
        self.assertNotEqual(self.book.chapter_path(1), old_path)
        retried = story.history.branch_publish(self.book, packet["branch"], staged["revision"])
        self.assertTrue(retried["idempotent"])
        self.assertEqual(retried["exported"], [])

        for offset, chapter, text in ((0, 1, NEW_TEXT), (1, 2, second_text)):
            inspected = story.history.branch_inspect(self.book, packet["branch"],
                                                      affected_offset=offset, limit=1, budget=8192)
            self.assertEqual(inspected["status"], "published")
            self.assertEqual(len(inspected["affected"]), 1)
            item = inspected["affected"][0]
            self.assertEqual(item["chapter"], chapter)
            self.assertEqual(item["path"], self.book.chapter_path(chapter))
            self.assertEqual((self.root / item["path"]).read_text(encoding="utf-8"), text)
            self.assertEqual(inspected["pages"]["affected"]["total"], 2)
            self.assertEqual(inspected["receipt"]["chapters"], [chapter])
            self.assertLessEqual(len(story.dumps(inspected).encode("utf-8")), 8192)
        with self.assertRaises(story.StoryError) as error:
            story.history.branch_inspect(self.book, packet["branch"], limit=1, budget=256)
        self.assertEqual(error.exception.code, "budget_exceeded")

    def test_history_inspection_keeps_snapshot_during_a_completed_concurrent_rename(self):
        self.book.db.execute("PRAGMA journal_mode=WAL")
        started = story.history.branch_start(self.book, 1, self.book.meta("revision"))
        original_path = self.book.chapter_path(1)
        select_external_path = self.book.chapter_external_path
        publications = []

        def rename_after_selecting_path(chapter):
            selected = select_external_path(chapter)
            if not publications:
                writer = story.Book(self.root)
                try:
                    self.draft.write_bytes(NEW_TEXT.encode("utf-8"))
                    publications.append(writer.commit(1, self.draft, self.delta(writer, NEW_TEXT),
                                                       replace_last=True))
                finally:
                    writer.close()
            return selected

        with patch.object(self.book, "chapter_external_path", rename_after_selecting_path):
            inspected = story.history.branch_inspect(self.book, started["branch"], chapter=1, budget=8192)

        self.assertEqual(len(publications), 1)
        self.assertTrue(publications[0]["exports_complete"])
        self.assertFalse((self.root / original_path).exists())
        self.assertNotEqual(self.book.chapter_path(1), original_path)
        self.assertGreater(self.book.meta("revision"), inspected["current_revision"])
        self.assertEqual(inspected["current_revision"], started["revision"])
        self.assertEqual(inspected["affected"][0]["path"], original_path)
        self.assertIsNone(inspected["affected"][0]["external_edit"])
        self.assertEqual(inspected["base"]["text"], OLD_TEXT)
        self.assertEqual(self.book._retired_chapters(), [])
        self.assertTrue(self.book.export()["exports_complete"])
        self.assertLessEqual(len(story.dumps(inspected).encode("utf-8")), 8192)


if __name__ == "__main__":
    unittest.main()

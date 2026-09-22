"""Recover omitted short-story chapters without changing the adopted baseline."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

try:
    import test_short_assembly as fixture
except ModuleNotFoundError:
    from tests import test_short_assembly as fixture

story, history = fixture.story, fixture.story.history


class AdoptBackfillTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ShortAssemblyTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.book = self.fixture.book
        self.root = self.fixture.root
        self.originals = {}

    def revision(self):
        return self.book.meta("revision")

    def source(self, chapter, text=None):
        path = self.root / f"旧稿第{chapter}章.md"
        text = text or f"第{chapter}章 借钥\n她核对第{chapter}张借据，随后把钥匙放在桌边。\n"
        path.write_bytes(text.encode("utf-8"))
        self.originals[chapter] = text
        return path

    def adopt(self, chapter=2):
        return self.book.adopt(chapter, self.source(chapter), "她核对借据，留下钥匙。", self.revision(),
                               volume_dir="第一卷 雨夜")

    def backfill(self, chapter=1, expected=None, **kwargs):
        path = self.root / f"旧稿第{chapter}章.md"
        if not path.exists():
            self.source(chapter)
        return self.book.adopt_backfill(chapter, path, "她核对旧借据，留下钥匙。",
                                        self.revision() if expected is None else expected,
                                        volume_dir=kwargs.pop("volume_dir", "第一卷 雨夜"), **kwargs)

    def snapshot(self):
        return self.revision(), tuple(self.book.db.iterdump())

    def count_records(self):
        return {table: self.book.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("chapter_state", "events", "history_versions", "history_heads")}

    def assert_rejected_without_writes(self, action):
        before = self.snapshot()
        with self.assertRaises(story.StoryError):
            action()
        self.assertEqual(self.snapshot(), before)

    def test_cli_backfill_after_continuation_preserves_state_and_completes_assembly(self):
        self.book.save_notes([{"id": "key", "text": "钥匙目前已经归还。", "source": "作者核定现状"}],
                             self.revision())
        story.world.save(self.book, {"entities": [{"id": "keeper", "name": "门房", "kind": "character",
                                                    "description": "守夜时负责保管钥匙。"}]}, self.revision())
        self.adopt()
        self.fixture.commit(3, "还钥", "第3章 还钥\n她归还钥匙，当场注销了借据。\n")
        source = self.source(1)
        source_bytes = source.read_bytes()
        cards = self.book.cards()
        world_rows = [line for line in self.book.db.iterdump() if line.startswith('INSERT INTO "world_')]
        baseline = tuple(self.book.db.execute("SELECT * FROM chapter_state WHERE chapter=2").fetchone())
        process = subprocess.run([sys.executable, "-B", str(fixture.TOOL), "adopt-backfill", "--book",
                                  str(self.root), "--chapter", "1", "--draft", str(source), "--summary",
                                  "她核对旧借据，留下钥匙。", "--expect", str(self.revision()),
                                  "--volume-dir", "第一卷 雨夜"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertTrue(result["exports_complete"], result)
        self.assertEqual(self.book.meta("last_chapter"), 3)
        self.assertEqual(self.book.meta("imported_through"), 2)
        self.assertEqual(self.book.cards(), cards)
        self.assertEqual([line for line in self.book.db.iterdump() if line.startswith('INSERT INTO "world_')], world_rows)
        self.assertEqual(tuple(self.book.db.execute("SELECT * FROM chapter_state WHERE chapter=2").fetchone()), baseline)
        self.assertEqual(source.read_bytes(), source_bytes)
        row = self.book.db.execute("SELECT * FROM chapters WHERE chapter=1").fetchone()
        self.assertEqual(row["imported"], 1)
        self.assertEqual(row["text"], self.originals[1])
        self.assertEqual(row["sha"], story.digest(self.originals[1]))
        receipt = json.loads(row["receipt"])
        self.assertEqual(receipt["quality"], "imported_unverified")
        self.assertEqual(receipt["source_path"], str(source.resolve()))
        self.assertEqual(receipt["source_sha256"], hashlib.sha256(source_bytes).hexdigest())
        self.assertFalse(history.read_dependencies(self.book, 1)["complete"])
        assembled = self.book.assemble_short(3)
        combined = Path(assembled["path"]).read_text(encoding="utf-8")
        self.assertEqual(assembled["chapters"], 3)
        self.assertLess(combined.index("第1章"), combined.index("第2章"))
        self.assertLess(combined.index("第2章"), combined.index("第3章"))
        for text in (self.originals[1], self.originals[2], "她归还钥匙，当场注销了借据。"):
            self.assertIn(text.splitlines()[-1], combined)

    def test_same_input_retry_after_another_revision_restores_export_once(self):
        self.adopt()
        original_expected = self.revision()
        self.backfill()
        exported = self.root / self.book.chapter_path(1)
        exported.unlink()
        self.book.save_notes([{"id": "weather", "text": "天已放晴。", "source": "作者核定"}], self.revision())
        before = self.count_records(), self.revision()
        repeated = self.backfill()
        self.assertTrue(repeated["idempotent"])
        self.assertTrue(repeated["exports_complete"], repeated)
        self.assertEqual((self.count_records(), self.revision()), before)
        self.assertEqual(exported.read_text(encoding="utf-8"), self.originals[1])
        self.assertTrue(self.backfill(expected=original_expected)["idempotent"])
        self.assertEqual((self.count_records(), self.revision()), before)

    def test_retry_rejects_changed_input_without_overwriting_registered_chapter(self):
        self.adopt()
        self.backfill()
        exported = self.root / self.book.chapter_path(1)
        original = exported.read_bytes()
        self.source(1, "第1章 借钥\n这是另一份尚未经过审核的旧稿。\n")
        self.assert_rejected_without_writes(lambda: self.backfill())
        self.assertEqual(exported.read_bytes(), original)

    def test_fresh_and_native_books_cannot_backfill(self):
        self.source(1)
        self.assert_rejected_without_writes(lambda: self.backfill())
        self.fixture.commit(1, "借钥", "第1章 借钥\n她拿到钥匙，决定推开院门。\n")
        self.assert_rejected_without_writes(lambda: self.backfill())

    def test_long_adopted_book_cannot_backfill(self):
        other_root = self.root.parent / "长篇"
        story.Book.create(other_root, "长篇旧稿", "long")
        other = story.Book(other_root)
        self.addCleanup(other.close)
        other.adopt(2, self.source(2), "第2章已有基线。", 0, volume_dir="第一卷 雨夜")
        before = tuple(other.db.iterdump())
        with self.assertRaises(story.StoryError):
            other.adopt_backfill(1, self.source(1), "缺失的第1章。", other.meta("revision"), "第一卷 雨夜")
        self.assertEqual(tuple(other.db.iterdump()), before)

    def test_only_missing_chapters_before_imported_boundary_are_accepted(self):
        self.adopt()
        self.fixture.commit(3, "还钥", "第3章 还钥\n她归还钥匙，当场注销了借据。\n")
        for chapter in (-1, 0, 2, 3, 4):
            with self.subTest(chapter=chapter):
                self.source(chapter)
                self.assert_rejected_without_writes(lambda: self.backfill(chapter))
        self.assertEqual(self.book.meta("last_chapter"), 3)
        self.assertEqual(self.book.meta("imported_through"), 2)

    def test_stale_revision_cannot_add_a_chapter(self):
        self.adopt()
        self.source(1)
        expected = self.revision()
        self.book.save_notes([{"id": "key", "text": "钥匙已归还。", "source": "作者核定"}], expected)
        self.assert_rejected_without_writes(lambda: self.backfill(expected=expected))
        self.assertIsNone(self.book.db.execute("SELECT 1 FROM chapters WHERE chapter=1").fetchone())

    def test_existing_external_export_change_is_preserved_on_retry(self):
        self.adopt()
        self.backfill()
        exported = self.root / self.book.chapter_path(1)
        edited = "读者手工批注，保留此修改。\n"
        exported.write_bytes(edited.encode("utf-8"))
        before = self.count_records(), self.revision()
        try:
            result = self.backfill()
        except story.StoryError:
            pass
        else:
            self.assertFalse(result["exports_complete"], result)
        self.assertEqual(exported.read_text(encoding="utf-8"), edited)
        self.assertEqual((self.count_records(), self.revision()), before)

    def test_unresolved_baseline_export_blocks_new_backfill_without_data_loss(self):
        self.adopt()
        self.source(1)
        exported = self.root / self.book.chapter_path(2)
        edited = "作者尚未提交的第二章修订，必须保留。\n"
        exported.write_bytes(edited.encode("utf-8"))
        self.assert_rejected_without_writes(lambda: self.backfill())
        self.assertEqual(exported.read_text(encoding="utf-8"), edited)
        self.assertIsNone(self.book.db.execute("SELECT 1 FROM chapters WHERE chapter=1").fetchone())

    def test_database_failure_rolls_back_backfill_before_any_publication(self):
        self.adopt()
        self.source(1)
        before = self.snapshot()
        files = sorted(str(path.relative_to(self.root)) for path in (self.root / "chapters").rglob("*.md"))
        with patch.object(history, "on_commit", side_effect=sqlite3.OperationalError("history save interrupted")):
            with self.assertRaises(sqlite3.OperationalError):
                self.backfill()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(sorted(str(path.relative_to(self.root)) for path in (self.root / "chapters").rglob("*.md")), files)
        self.assertTrue(self.backfill()["exports_complete"])

    def test_export_failure_is_durable_and_recovers_after_reopening(self):
        self.adopt()
        self.source(1)
        with patch.object(story, "atomic_write", side_effect=OSError("publication interrupted")):
            result = self.backfill()
        self.assertFalse(result["exports_complete"], result)
        counts = self.count_records(), self.revision()
        self.book.close()
        self.book = self.fixture.book = story.Book(self.root)
        recovery = self.book.export(safe_only=True)
        self.assertTrue(recovery["exports_complete"], recovery)
        self.assertEqual((self.count_records(), self.revision()), counts)
        self.assertEqual((self.root / self.book.chapter_path(1)).read_text(encoding="utf-8"), self.originals[1])
        self.fixture.commit(3, "还钥", "第3章 还钥\n她归还钥匙，当场注销了借据。\n")
        self.assertTrue(self.book.assemble_short(3)["exports_complete"])

    def test_history_state_replays_multiple_backfills_and_later_continuation(self):
        self.book.save_notes([{"id": "key", "text": "当前钥匙仍在门房。", "source": "作者核定"}], self.revision())
        self.adopt(3)
        current = self.book.cards()
        self.backfill(1)
        self.backfill(2)
        for chapter, before in ((1, False), (2, True), (2, False)):
            with self.subTest(chapter=chapter, before=before):
                state = history.history_state(self.book, chapter, before=before)
                self.assertEqual({card["id"]: card for card in state["cards"]}, current)
        self.fixture.commit(4, "还钥", "第4章 还钥\n她归还钥匙，当场注销了借据。\n")
        state = history.history_state(self.book, 4)
        self.assertEqual({card["id"]: card for card in state["cards"]}, current)

    def test_backfilled_chapter_remains_editable_through_reviewed_history(self):
        self.adopt()
        self.backfill()
        for chapter in (1, 2):
            self.book.save_plan(chapter, {"title": "借钥", "volume_dir": "第一卷 雨夜", "goal": "核对借据",
                                         "stop": "留下钥匙", "requires": [], "length": [1, 200],
                                         "beats": [{"choice": "核对借据", "change": "留下钥匙"}]}, self.revision())
        assembled = self.book.assemble_short(2)
        started = history.branch_start(self.book, 1, self.revision())
        entries = []
        revised_text = self.originals[1].replace("第1张借据", "第1张旧借据")
        for affected in started["affected"]:
            chapter = affected["chapter"]
            text = revised_text if chapter == 1 else self.originals[chapter]
            candidate = {"sha": story.digest(text), "summary": "她核清借据，留下钥匙。",
                         "dependencies": [], "complete": True}
            review = {"draft_sha256": candidate["sha"],
                      "candidate_sha256": history.candidate_fingerprint(candidate), "issues": [],
                      "checks": {key: {"note": "核对修订后的先后顺序与钥匙位置。", "quote": "随后把钥匙放在桌边。"}
                                 for key in story.CHECKS}}
            entries.append({"chapter": chapter, "text": text, "summary": candidate["summary"],
                            "dependencies": [], "complete": True, "review": review})
        staged = history.branch_update(self.book, started["branch"], {"chapters": entries}, self.revision())
        semantic = {**staged["review_template"], "note": "已逐章核查此次借据修订及其后续影响。",
                    "state_review": "最终状态与结局保持一致，全部卡片已核对。",
                    "coverage_review": "已核对未声明的关联及后续章节，不把机械依赖当完整证明。"}
        staged = history.branch_update(self.book, started["branch"], {"semantic_review": semantic}, self.revision())
        published = history.branch_publish(self.book, staged["branch"], self.revision())
        self.assertTrue(published["exports_complete"], published)
        self.assertEqual(self.book.meta("last_chapter"), 2)
        self.assertEqual(self.book.meta("imported_through"), 2)
        self.assertEqual(self.book.db.execute("SELECT imported FROM chapters WHERE chapter=1").fetchone()[0], 1)
        self.assertIn("第1张旧借据", Path(assembled["path"]).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

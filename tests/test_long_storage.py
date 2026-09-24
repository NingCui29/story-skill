import importlib.util
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("story_storage_test_runtime", ROOT / "skills/story-skill/scripts/story.py")
story = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(story)
TEXT = "# 第1章 交钥匙\n江棠把旧钥匙交给杜承安。雨停之前，他必须回来。\n"
PLAN = {"volume_dir": "第一卷 雨夜", "goal": "交出旧钥匙", "stop": "等待杜承安返回", "beats": [{"choice": "交出钥匙", "change": "承担失物风险"}],
        "requires": ["key"], "tags": ["江棠"], "length": [10, 100]}


class LongStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="story-long-storage-")
        self.root = Path(self.tmp.name) / "书"
        self.book = None

    def tearDown(self):
        if self.book:
            self.book.close()
        self.tmp.cleanup()

    def initialize(self):
        story.Book.create(self.root, "雨前", "long")
        self.book = story.Book(self.root)
        self.book.save_notes([{"id": "key", "text": "旧钥匙在江棠手中。", "source": "作者设定", "tags": ["江棠"]}], 0)
        self.book.save_plan(1, PLAN, 1)
        self.draft = self.root / "draft.md"
        self.draft.write_bytes(TEXT.encode("utf-8"))

    def delta(self):
        return {"book_id": self.book.meta("id"), "base_revision": self.book.meta("revision"), "summary": "江棠交出钥匙，等待归还。",
            "changes": [], "review": {"draft_sha256": story.digest(TEXT), "checks": {
                key: {"note": "交付动作与归还承诺已在场。", "quote": "江棠把旧钥匙交给杜承安。"} for key in story.CHECKS}, "issues": []}}

    def old_book(self):
        runtime = Path(self.tmp.name) / "old.py"
        fixture = ROOT / "tests/fixtures/schema1_runtime.py"
        self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(),
                         "70c8a0294d72103cb2232834ac951f30abef6ab20e940cb0303aca1858910cb3")
        runtime.write_bytes(fixture.read_bytes())
        spec = importlib.util.spec_from_file_location("old_migration_runtime", runtime)
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        old.Book.create(self.root, "旧书", "long")
        book = old.Book(self.root)
        draft = self.root / "old-draft.md"
        draft.write_bytes(TEXT.encode("utf-8"))
        book.adopt(400, draft, "旧书最后一章，前文未经导入。", 0)
        identity = book.meta("id")
        book.close()
        return identity

    def test_explicit_migration_preserves_export_identity_and_rollback_backup(self):
        identity = self.old_book()
        with self.assertRaises(story.StoryError) as caught:
            story.Book(self.root)
        self.assertEqual(caught.exception.code, "schema_mismatch")
        before = (self.root / "chapters/0400.md").read_bytes()
        result = story.storage.migrate(story.CORE, self.root)
        with closing(sqlite3.connect(result["backup"])) as backup:
            self.assertEqual(json.loads(backup.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0]), 1)
            self.assertEqual(backup.execute("SELECT text FROM chapters").fetchone()[0], TEXT)
        self.book = story.Book(self.root)
        self.assertEqual(self.book.meta("id"), identity)
        self.assertEqual(self.book.meta("last_chapter"), 400)
        self.assertEqual((self.root / "chapters/0400.md").read_bytes(), before)
        self.assertTrue(self.book.recall("江棠")["matches"])
        self.assertEqual(self.book.status()["changed_export_count"], 0)
        self.assertFalse(story.storage.migrate(story.CORE, self.root)["migrated"])

    def test_failed_migration_rolls_back_all_ddl(self):
        self.old_book()
        with patch.object(story.search, "upsert", side_effect=RuntimeError("simulated index failure")):
            with self.assertRaises(RuntimeError):
                story.storage.migrate(story.CORE, self.root)
        with closing(sqlite3.connect(self.root / ".story/state.sqlite3")) as db:
            self.assertEqual(json.loads(db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0]), 1)
            self.assertEqual(db.execute("SELECT text FROM chapters").fetchone()[0], TEXT)
            self.assertFalse(db.execute("SELECT name FROM sqlite_master WHERE name='chapter_state'").fetchall())
        self.assertEqual(len(list((self.root / ".story/migration-backups").glob("*.sqlite3"))), 1)

    def test_body_dedup_and_incremental_cards_and_no_write_transaction_during_io(self):
        self.initialize()
        original_cards, original_write = self.book.cards, story.atomic_write
        def selected(ids=None):
            self.assertIsNotNone(ids, "Daily operations must not deserialize every card")
            return original_cards(ids)
        def write(*args, **kwargs):
            self.assertFalse(self.book.db.in_transaction, "Export I/O must not hold a SQLite write transaction")
            return original_write(*args, **kwargs)
        with patch.object(self.book, "cards", side_effect=selected), patch.object(story, "atomic_write", side_effect=write):
            self.book.context(1)
            self.assertTrue(self.book.commit(1, self.draft, self.delta())["exports_complete"])
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM core_objects WHERE text=?", (TEXT,)).fetchone()[0], 1)
        self.assertEqual(self.book.db.execute("SELECT content FROM artifacts").fetchone()[0], TEXT)
        event = json.loads(self.book.db.execute("SELECT data FROM events WHERE kind='commit_chapter'").fetchone()[0])
        self.assertNotIn("text", event)
        self.assertEqual(event["body_sha256"], story.digest(TEXT))
        with self.assertRaises(sqlite3.IntegrityError):
            self.book.db.execute("UPDATE core_objects SET text='unreviewed' WHERE sha=?", (story.digest(TEXT),))
        self.book.db.rollback()

    def test_local_mode_does_not_claim_archival_integrity(self):
        self.initialize()
        self.book.commit(1, self.draft, self.delta())
        self.book.save_plan(2, PLAN, self.book.meta("revision"))
        self.book.commit(2, self.draft, self.delta())
        archive = self.root / self.book.chapter_path(1)
        stat = archive.stat()
        archive.write_bytes(archive.read_bytes().replace("江棠".encode(), "江糖".encode()))
        os.utime(archive, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.book.integrity = "local"
        status = self.book.status()
        self.assertFalse(status["integrity"]["full_book_verified"])
        self.assertEqual(status["integrity"]["unverified_archive_count"], 1)
        self.assertIsNone(self.book.export()["exports_complete"])
        self.book.integrity = "strict"
        self.assertEqual(self.book.status()["changed_export_count"], 1)

    def test_scoped_hard_constraints_dont_accumulate_across_volumes(self):
        self.initialize()
        self.book.save_notes([{"id": "old-rule", "kind": "contract", "text": "旧地图禁止夜航。", "source": "卷一设定",
                               "critical": True, "scope": "volume:old"}], 2)
        self.assertNotIn("old-rule", {c["id"] for c in self.book.context(1)["required_cards"]})
        self.book.save_plan(1, {**PLAN, "requires": ["key", "old-rule"]}, 3)
        self.assertIn("old-rule", {c["id"] for c in self.book.context(1)["required_cards"]})

    def test_world_delta_is_atomic_and_latest_rewrite_requires_history(self):
        self.initialize()
        changes = {"entities": [{"id": "jiang", "name": "江棠", "kind": "character", "description": "交钥匙的人"}],
                   "facts": [{"id": "gave", "subject": "jiang", "predicate": "钥匙归属", "value": "交给杜承安",
                              "start": 1, "evidence": {"kind": "chapter", "chapter": 1,
                              "sha256": "0" * 64, "quote": "江棠把旧钥匙交给杜承安。"}}]}
        delta = {**self.delta(), "world_changes": changes}
        with self.assertRaises(story.StoryError) as caught:
            self.book.commit(1, self.draft, delta)
        self.assertEqual(caught.exception.code, "world_evidence")
        self.assertEqual(self.book.meta("revision"), 2)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM chapters").fetchone()[0], 0)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM world_entities").fetchone()[0], 0)
        self.assertFalse((self.root / "chapters/第一卷 雨夜/第1章 交钥匙.md").exists())
        changes["facts"][0]["evidence"]["sha256"] = story.digest(TEXT)
        self.assertTrue(self.book.commit(1, self.draft, delta)["exports_complete"])
        self.assertEqual(self.book.meta("revision"), 3)
        self.assertEqual(self.book.db.execute("SELECT value FROM world_facts WHERE id='gave'").fetchone()[0], "交给杜承安")
        stored = self.book.world_read("facts", "gave")
        self.assertTrue(stored["evidence_current"])
        self.assertIs(type(stored["payload"]["facts"][0]["hard"]), bool)
        self.assertTrue(story.world.save(self.book, stored["payload"], 3)["idempotent"])
        with self.assertRaises(story.StoryError) as caught:
            self.book.context(1)
        self.assertEqual(caught.exception.code, "history_revision_required")

    def test_analyzed_source_daily_reads_do_not_load_whole_text_into_python(self):
        self.initialize()
        source = self.root / "input.txt"
        source.write_bytes(("第1章 往事\n江棠收起旧信。\n" * 100).encode("utf-8"))
        sid = self.book.ingest(source)["source"]
        with patch.object(self.book, "source", side_effect=AssertionError("unbounded source read")):
            self.book.status()
            chunk = self.book.next_chunks(sid)["chunks"][0]
            self.book.source_read(sid, chunk["start"], chunk["end"], 10000)
            self.book.record(sid, chunk["ordinal"], {"chunk_sha256": chunk["sha"], "summary": "江棠收信。",
                "findings": [{"kind": "动作", "claim": "保留书信。", "quote": "江棠收起旧信。"}]})

    def test_archived_prose_remains_readable_by_version_under_a_byte_budget(self):
        self.initialize()
        self.book.commit(1, self.draft, self.delta())
        revised = TEXT.replace("雨停之前", "天亮之前")
        self.draft.write_bytes(revised.encode("utf-8"))
        delta = self.delta()
        delta["review"]["draft_sha256"] = story.digest(revised)
        self.book.commit(1, self.draft, delta, replace_last=True)
        old = self.book.chapter_read(1, story.digest(TEXT), 0, len(TEXT), 1000)
        current = self.book.chapter_read(1)
        self.assertEqual(old["text"], TEXT)
        self.assertEqual(current["text"], revised)
        with self.assertRaises(story.StoryError):
            self.book.chapter_read(1, story.digest("another book"))

    def test_dependency_candidates_can_be_reviewed_in_the_same_chapter_commit(self):
        self.initialize()
        candidates = self.book.dependency_candidates(1)["candidates"]
        self.assertEqual([d["ref"] for d in candidates], ["key"])
        delta = {**self.delta(), "dependencies": candidates,
                 "dependency_review": {"complete": True, "note": "本短景只使用钥匙原持有状态，无其他历史事实。"}}
        self.book.commit(1, self.draft, delta)
        self.assertEqual(self.book.db.execute("SELECT complete FROM history_versions ORDER BY revision DESC LIMIT 1").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()

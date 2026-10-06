"""Fresh-process recovery after a native chapter commit loses its export receipt."""

from contextlib import closing
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

if __package__:
    from .outline_fixture import bind_adopted_outline
else:
    from outline_fixture import bind_adopted_outline


TOOL = Path(__file__).resolve().parents[1] / "skills/story-skill/scripts/story.py"
spec = importlib.util.spec_from_file_location("story_chapter_crash_recovery", TOOL)
story = importlib.util.module_from_spec(spec)
spec.loader.exec_module(story)

DRAFT = "第1章 门后的雨\n沈禾把唯一的钥匙交给守门人。\n她答应在天亮之前带回账本。\n"
QUOTE = "沈禾把唯一的钥匙交给守门人。"
CRASH_CODE = 73
CRASH_COMMIT = r"""
import importlib.util
import os
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('crashed_chapter', sys.argv[1])
story = importlib.util.module_from_spec(spec)
spec.loader.exec_module(story)
book = story.Book(sys.argv[2])
delta = story.read_json(sys.argv[4])
stage = sys.argv[5]
original_write = story.atomic_write

def crash_at_publication(path, *args, **kwargs):
    # This barrier is reached only after the canonical chapter transaction commits.
    assert not book.db.in_transaction
    assert book.meta('revision') == delta['base_revision'] + 1
    assert book.meta('last_chapter') == 1
    assert book.db.execute("SELECT count(*) FROM events WHERE kind='commit_chapter'").fetchone()[0] == 1
    assert Path(path) == book.root / book.chapter_path(1)
    row = book.db.execute('SELECT written_sha FROM artifact_state WHERE path=?',
                          (book.chapter_path(1),)).fetchone()
    assert row is not None and row[0] is None
    if stage == 'before_publication':
        assert not Path(path).exists()
        os._exit(73)
    assert stage == 'before_acknowledgement'
    original_write(path, *args, **kwargs)
    assert Path(path).read_text(encoding='utf-8') == story.read_text(sys.argv[3])
    # atomic_write has published and cleaned its stage, but export has not yet
    # updated written_sha. os._exit bypasses Book.close and Python cleanup.
    os._exit(73)

story.atomic_write = crash_at_publication
book.commit(1, Path(sys.argv[3]), delta)
raise AssertionError('Chapter export crash barrier was not reached')
"""


class ChapterCrashRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-chapter-crash-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "中断恢复测试书"
        story.Book.create(self.root, "门后的雨", "long")
        self.draft = self.root / ".story/drafts/第1章.md"
        self.draft.parent.mkdir(parents=True)
        self.draft.write_bytes(DRAFT.encode("utf-8"))
        self.delta_path = self.root / "提交凭据.json"
        with closing(story.Book(self.root)) as book:
            book.save_notes([{"id": "hero", "kind": "character", "text": "沈禾持有唯一的钥匙。",
                              "source": "合成测试设定"}], book.meta("revision"))
            book.save_plan(1, {
                "volume_dir": "第一卷 雨夜", "title": "门后的雨", "goal": "用钥匙换取入口",
                "stop": "交出钥匙后停笔", "requires": ["hero"], "constraints": [], "tags": [],
                "beats": [{"choice": "沈禾交出钥匙", "change": "失去退路"}],
                "length": [20, 120],
                "length_exception": {"source": "user_request", "quote": "测试章按20至120字写作。"},
            }, book.meta("revision"))
            bind_adopted_outline(story, book, 1)
            self.base_revision = book.meta("revision")
            delta = {
                "book_id": book.meta("id"), "base_revision": self.base_revision,
                "summary": "沈禾交出钥匙，承诺天亮前返回。",
                "changes": [{"id": "hero", "text": "沈禾已交出唯一的钥匙。", "quote": QUOTE}],
                "review": {"draft_sha256": story.digest(DRAFT), "issues": [], "checks": {
                    check: {"note": "人物交出钥匙的选择与结果已在正文呈现。", "quote": QUOTE}
                    for check in story.CHECKS}},
            }
            self.delta_path.write_text(story.dumps(delta), encoding="utf-8")
        self.before = self.canonical_snapshot()

    def canonical_snapshot(self):
        with closing(story.Book(self.root, read_only=True)) as book:
            return {
                "revision": book.meta("revision"), "last_chapter": book.meta("last_chapter"),
                "events": [tuple(row) for row in book.db.execute("SELECT * FROM events ORDER BY seq")],
                "chapters": [tuple(row) for row in book.db.execute("SELECT * FROM chapters ORDER BY chapter")],
                "cards": book.cards(),
                "versions": [tuple(row) for row in book.db.execute("SELECT * FROM history_versions ORDER BY id")],
            }

    def artifact(self):
        with closing(story.Book(self.root, read_only=True)) as book:
            return dict(book.db.execute("SELECT path,sha,written_sha FROM artifact_state").fetchone())

    def command(self, command, *arguments, expected_code=0):
        process = subprocess.run(
            [sys.executable, "-B", str(TOOL), command, "--book", str(self.root), *arguments],
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(process.returncode, expected_code, process.stdout + process.stderr)
        self.assertEqual(process.stderr, "")
        return json.loads(process.stdout)

    def retry_commit(self, expected_code=0):
        return self.command("commit", "--chapter", "1", "--draft", str(self.draft),
                            "--input", str(self.delta_path), expected_code=expected_code)

    def crash_commit(self, stage):
        crashed = subprocess.run(
            [sys.executable, "-B", "-c", CRASH_COMMIT, str(TOOL), str(self.root),
             str(self.draft), str(self.delta_path), stage],
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(crashed.returncode, CRASH_CODE, crashed.stdout + crashed.stderr)
        self.assertEqual(crashed.stdout, "")
        self.assertEqual(crashed.stderr, "")
        self.committed = self.canonical_snapshot()
        self.assertEqual(self.committed["revision"], self.base_revision + 1)
        self.assertEqual(self.committed["last_chapter"], 1)
        self.assertEqual(len(self.committed["chapters"]), 1)
        self.assertEqual(self.committed["chapters"][0][1], DRAFT)
        self.assertEqual(len(self.committed["events"]), len(self.before["events"]) + 1)
        self.assertEqual(sum(row[2] == "commit_chapter" for row in self.committed["events"]), 1)
        self.assertEqual(self.committed["cards"]["hero"]["text"], "沈禾已交出唯一的钥匙。")
        self.assertEqual(len(self.committed["versions"]), len(self.before["versions"]) + 1)
        artifact = self.artifact()
        self.assertEqual(artifact["sha"], story.digest(DRAFT))
        self.assertIsNone(artifact["written_sha"])
        self.relative = artifact["path"]
        self.target = self.root / self.relative
        if stage == "before_publication":
            self.assertFalse(self.target.exists())
        else:
            self.assertEqual(self.target.read_bytes(), DRAFT.encode("utf-8"))
        status = self.command("status")
        self.assertEqual(status["pending_exports"], [self.relative])
        self.assertEqual(status["pending_export_count"], 1)
        self.assertEqual(status["changed_export_count"], 0)
        self.assertFalse(status["integrity"]["full_book_verified"])
        self.assertIsNone(self.artifact()["written_sha"], "Status must not acknowledge the interrupted export")
        self.assertEqual(self.canonical_snapshot(), self.committed)

    def assert_recovered(self):
        self.assertEqual(self.target.read_bytes(), DRAFT.encode("utf-8"))
        self.assertEqual(self.artifact()["written_sha"], story.digest(DRAFT))
        self.assertEqual(self.canonical_snapshot(), self.committed)
        status = self.command("status")
        self.assertEqual(status["pending_export_count"], 0)
        self.assertEqual(status["changed_export_count"], 0)
        self.assertTrue(status["integrity"]["full_book_verified"])

    def test_crash_before_publication_recovers_without_duplicate_commit(self):
        self.crash_commit("before_publication")
        recovered = self.command("export", "--safe-only")
        self.assertEqual(recovered["exported"], [self.relative])
        self.assertTrue(recovered["exports_complete"])
        self.assert_recovered()
        retry = self.retry_commit()
        self.assertTrue(retry["committed"])
        self.assertTrue(retry["idempotent"])
        self.assertTrue(retry["exports_complete"])
        self.assertEqual(retry["exported"], [])
        self.assert_recovered()

    def test_crash_after_publication_retries_without_duplicate_commit(self):
        self.crash_commit("before_acknowledgement")
        retry = self.retry_commit()
        self.assertTrue(retry["committed"])
        self.assertTrue(retry["idempotent"])
        self.assertTrue(retry["exports_complete"])
        self.assertEqual(retry["exported"], [], "Matching published bytes only need acknowledgement")
        self.assert_recovered()
        repeated = self.command("export", "--safe-only")
        self.assertTrue(repeated["exports_complete"])
        self.assertEqual(repeated["exported"], [])
        self.assert_recovered()

    def assert_outside_edit_survives_recovery(self, stage):
        self.crash_commit(stage)
        outside = "用户在恢复前保存了另一个版本，必须保留。\n".encode("utf-8")
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_bytes(outside)
        status = self.command("status")
        self.assertEqual(status["pending_export_count"], 0)
        self.assertEqual(status["changed_exports"], [self.relative])
        self.assertEqual(status["changed_export_count"], 1)
        recovered = self.command("export", "--safe-only", expected_code=2)
        self.assertFalse(recovered["exports_complete"])
        self.assertEqual(recovered["exported"], [])
        self.assertEqual(recovered["pending_export_count"], 0)
        self.assertEqual(recovered["changed_exports"], [self.relative])
        self.assertEqual(recovered["changed_export_count"], 1)
        self.assertEqual(self.target.read_bytes(), outside)
        retry = self.retry_commit(expected_code=2)
        self.assertTrue(retry["committed"])
        self.assertTrue(retry["idempotent"])
        self.assertFalse(retry["exports_complete"])
        self.assertEqual(retry["export_details"]["code"], "export_conflict")
        self.assertEqual(self.target.read_bytes(), outside)
        self.assertIsNone(self.artifact()["written_sha"])
        self.assertEqual(self.canonical_snapshot(), self.committed)
        self.assertFalse((self.root / ".story/export-backups").exists())

    def test_outside_file_before_publication_survives_recovery(self):
        self.assert_outside_edit_survives_recovery("before_publication")

    def test_outside_edit_after_publication_survives_recovery(self):
        self.assert_outside_edit_survives_recovery("before_acknowledgement")


if __name__ == "__main__":
    unittest.main()

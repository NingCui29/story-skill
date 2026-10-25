"""User-declared Fanqie setup is local, qualified, private and non-destructive."""
import copy
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from tests import test_publish as fixtures


story = fixtures.story
drafts = story.platform_drafts
URL = "https://fanqienovel.com/main/writer/chapter-manage/1234567890&%E6%B5%8B%E8%AF%95?type=1"


class PlatformBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublishTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.commit()
        self.book, self.root = self.fixture.book, self.fixture.root
        self.book_id, self.revision = self.book.meta("id"), self.book.meta("revision")
        self.old_plan = self.fixture.prepare()
        self.profile_dir = self.root / drafts.PROFILE_DIRECTORY
        self.global_state = self.root.parent / "isolated-target-state"
        self.global_state.mkdir(mode=0o700)
        self.patches = [patch.object(drafts, "_lock_directory", return_value=self.global_state),
                        patch.object(story.platform_browser.BrowserAdapter, "__enter__", side_effect=AssertionError("No browser"))]
        for value in self.patches:value.start()
        self.addCleanup(lambda: [value.stop() for value in reversed(self.patches)])

    def bind(self, **overrides):
        values = {"account_id": "12345678901", "book_management_url": URL, "start": 1, "end": 1,
                  "expected_book_id": self.book_id, "expected_revision": self.revision}
        values.update(overrides)
        return drafts.workbench_bind(self.root, **values)

    def code(self, code, **overrides):
        return self.fixture.assert_code(code, self.bind, **overrides)

    def profiles(self):
        return sorted(self.profile_dir.glob("*.json")) if self.profile_dir.exists() else []

    def test_catalog_exposes_revision_and_formal_range_for_local_setup(self):
        packet = drafts.workbench_catalog(self.root)
        self.assertEqual(packet["revision"], self.book.meta("revision"))
        self.assertEqual((packet["formal_first"], packet["formal_last"], packet["formal_count"], packet["max_batch"]), (1, 1, 1, 1000))

    def test_bind_creates_private_profile_and_frozen_plan_without_browser_or_writing_prose(self):
        before = self.fixture.writing_snapshot()
        old = story.publish.inspect(self.book, self.old_plan["id"])
        result = self.bind()
        self.assertFalse(result["remote_accessed"])
        self.assertFalse(result["submission_performed"])
        self.assertFalse(result["platform_acceptance_verified"])
        self.assertEqual(result["scope"], "local_binding_and_frozen_plan_only")
        self.assertEqual(result["remote_book_id"], "1234567890")
        self.assertEqual(result["remote_book_title"], "测试")
        self.assertNotEqual(result["plan_id"], self.old_plan["id"])
        self.assertEqual(result["plan"]["chapters"], [1])
        self.assertEqual(story.publish.inspect(self.book, result["plan_id"])["manifest"]["binding_status"], "user_declared_unverified")
        self.assertEqual(self.fixture.writing_snapshot(), before)
        self.assertEqual(story.publish.inspect(self.book, self.old_plan["id"]), old)
        self.assertEqual(len(self.profiles()), 1)
        path = self.profile_dir / result["profile_id"]
        self.assertTrue(path.is_file())
        self.assertFalse(path.is_symlink())
        self.assertTrue(self.profile_dir.is_dir())
        self.assertFalse(self.profile_dir.is_symlink())
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.profile_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), result["profile_sha256"])
        saved = json.loads(path.read_text())
        self.assertEqual(saved["execution_mode"], "host_assisted_cua")
        self.assertIn("readback", saved)
        self.assertNotIn("12345678901", path.read_text())
        self.assertNotIn("selector", json.dumps(result))
        self.assertFalse((self.root / drafts.DIRECTORY).exists())
        self.assertEqual(list(self.global_state.iterdir()), [])
        preview = drafts.workbench_preview(self.root, result["plan_id"], result["profile_id"])
        self.assertEqual(preview["blockers"], ["platform_chrome_worker_unavailable"])
        self.assertIsNone(preview["preview_token"])

    def test_same_target_reuses_immutable_plan_and_new_target_creates_another(self):
        first = self.bind()
        original = story.publish.inspect(self.book, first["plan_id"])["manifest"]
        old_profile = (self.profile_dir / first["profile_id"]).read_bytes()
        repeated = self.bind()
        self.assertEqual(repeated["plan_id"], first["plan_id"])
        self.assertTrue(repeated["plan_reused"])
        self.assertEqual(repeated["profile_id"], first["profile_id"])
        self.assertEqual(repeated["profile_sha256"], first["profile_sha256"])
        self.assertEqual((self.profile_dir / first["profile_id"]).read_bytes(), old_profile)
        self.assertEqual(story.publish.inspect(self.book, first["plan_id"])["manifest"], original)
        changed = self.bind(account_id="777")
        self.assertNotEqual(changed["plan_id"], first["plan_id"])
        self.assertEqual(changed["profile_id"], first["profile_id"])
        self.assertEqual(len(self.profiles()), 1)
        self.assertEqual(story.publish.inspect(self.book, first["plan_id"])["manifest"], original)
        self.assertEqual(story.publish.inspect(self.book, changed["plan_id"])["manifest"]["account_id"], "777")

    def test_input_types_ranges_stale_revision_and_other_book_are_rejected_before_files(self):
        for overrides, code in (({"account_id": True}, "platform_draft_invalid_account"),
                                ({"account_id": "nickname"}, "platform_draft_invalid_account"),
                                ({"account_id": "1\n2"}, "platform_draft_invalid_account"),
                                ({"start": True}, "invalid_input"), ({"end": "1"}, "invalid_input"),
                                ({"start": 2, "end": 1}, "platform_draft_invalid_range"),
                                ({"end": 1001}, "platform_draft_invalid_range"),
                                ({"end": 1000001}, "platform_draft_invalid_range"),
                                ({"expected_revision": True}, "invalid_input"),
                                ({"expected_revision": self.book.meta("revision") + 1}, "stale_revision"),
                                ({"expected_book_id": "other-book"}, "platform_draft_book_changed")):
            with self.subTest(overrides=overrides):self.code(code, **overrides)
        self.assertEqual(self.profiles(), [])
        self.assertEqual(story.publish.list_plans(self.book)["total"], 1)

    def test_missing_or_unreviewed_formal_source_uses_existing_qualification(self):
        self.code("chapter_missing", end=2)
        path = self.root / self.book.chapter_path(1)
        path.write_text(path.read_text() + "外部改动", encoding="utf-8")
        self.code("exports_unresolved")
        self.assertEqual(self.profiles(), [])

    def test_imported_unreviewed_formal_chapter_cannot_be_frozen(self):
        root = self.root.parent / "unreviewed-import"
        story.Book.create(root, "未审查导入测试", "long")
        with closing(story.Book(root)) as book:
            plan = self.book.get_plan(1); plan["title"] = "未审查的导入章"
            book.save_plan(1, plan, book.meta("revision"))
            imported = root / "imported.md"
            imported.write_text("第1章 未审查的导入章\n" + fixtures.BODY, encoding="utf-8")
            book.adopt(1, imported, "隔离的未审查导入测试。", book.meta("revision"), volume_dir=plan["volume_dir"])
            self.fixture.assert_code("review_required", drafts.workbench_bind, root, "123", URL, 1, 1,
                                     expected_book_id=book.meta("id"), expected_revision=book.meta("revision"))
        self.assertFalse((root / drafts.PROFILE_DIRECTORY).exists())

    def test_publication_io_failure_is_sanitized_and_cleans_its_staging_file(self):
        with patch.object(drafts, "_atomic_publish", side_effect=OSError("PRIVATE SECRET BODY")):
            error = self.code("platform_draft_binding_failed")
        self.assertNotIn("PRIVATE", error.message)
        self.assertEqual(list(self.profile_dir.iterdir()), [])
        self.assertEqual(story.publish.list_plans(self.book)["total"], 1)
        self.assertEqual(drafts._WORKBENCH_BINDINGS, {})

    def test_failed_prepare_removes_only_its_new_profile_and_keeps_existing_state(self):
        first = self.bind()
        saved = (self.profile_dir / first["profile_id"]).read_bytes()
        with patch.object(story.publish, "prepare", side_effect=story.StoryError("synthetic_prepare_failure", "safe")):
            self.code("synthetic_prepare_failure")
        self.assertEqual(self.profiles(), [self.profile_dir / first["profile_id"]])
        self.assertEqual((self.profile_dir / first["profile_id"]).read_bytes(), saved)
        self.assertEqual(drafts._WORKBENCH_BINDINGS, {})
        self.assertEqual(story.publish.list_plans(self.book)["total"], 2)

    def test_profile_directory_symlink_and_leaf_link_are_not_followed(self):
        outside = self.root.parent / "outside-profiles"; outside.mkdir()
        self.profile_dir.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(story.StoryError):self.bind()
        self.assertEqual(list(outside.iterdir()), [])
        self.profile_dir.unlink(); self.profile_dir.mkdir()
        victim = outside / "victim.json"; victim.write_text("DO NOT OVERWRITE")
        fake_id = type("Identifier", (), {"hex": "a" * 32})()
        name = self.profile_dir / ("fanqie-" + fake_id.hex + ".json")
        name.symlink_to(victim)
        with patch.object(drafts.uuid, "uuid4", return_value=fake_id), self.assertRaises(story.StoryError):self.bind()
        self.assertEqual(victim.read_text(), "DO NOT OVERWRITE")

    def test_active_job_and_concurrent_binding_are_blocked(self):
        with drafts._WORKBENCH_LOCK:
            drafts._WORKBENCH_JOBS["binding-test"] = {"done": False, "_root": str(self.root), "_target_sha256": "x"}
        try:self.code("platform_draft_busy")
        finally:
            with drafts._WORKBENCH_LOCK:drafts._WORKBENCH_JOBS.pop("binding-test")
        entered, release, result, errors = threading.Event(), threading.Event(), [], []
        original = story.publish.prepare
        def prepare(*args, **kwargs):
            entered.set()
            if not release.wait(3):raise AssertionError("binding test timed out")
            return original(*args, **kwargs)
        def bind():
            try:result.append(self.bind())
            except BaseException as error:errors.append(error)
        with patch.object(story.publish, "prepare", side_effect=prepare):
            thread = threading.Thread(target=bind);thread.start()
            try:
                self.assertTrue(entered.wait(3))
                self.code("platform_draft_busy")
            finally:release.set();thread.join(3)
        self.assertEqual(errors, [])
        self.assertEqual(len(result), 1)
        self.assertEqual(drafts._WORKBENCH_BINDINGS, {})

    def test_old_unknown_journal_and_target_fence_are_unchanged(self):
        class FailedCreate:
            def verify_target(self, **target):pass
            def find_drafts(self, chapter):return []
            def create_draft(self, *args):raise RuntimeError("lost acknowledgment")
        result = drafts.run_plan(self.book, self.old_plan["id"], FailedCreate(), execute=True)
        self.assertEqual(result["results"][0]["state"], "unknown")
        journal = self.root / drafts.DIRECTORY / self.old_plan["id"]
        snapshots = {path: path.read_bytes() for directory in (journal, self.global_state) for path in directory.iterdir() if path.is_file()}
        old_manifest = copy.deepcopy(story.publish.inspect(self.book, self.old_plan["id"])["manifest"])
        self.bind()
        self.assertTrue(all(path.read_bytes() == raw for path, raw in snapshots.items()))
        self.assertEqual(story.publish.inspect(self.book, self.old_plan["id"])["manifest"], old_manifest)
        self.assertEqual(drafts.status(self.book, self.old_plan["id"])["results"][0]["state"], "unknown")

    def test_rebinding_same_unknown_target_cannot_trigger_another_create(self):
        class LostCreate:
            def __init__(self):self.creates = 0
            def verify_target(self, **target):pass
            def find_drafts(self, chapter):return []
            def create_draft(self, *args):
                self.creates += 1
                raise RuntimeError("lost acknowledgment")
        bound = self.bind()
        first = LostCreate()
        drafts.run_plan(self.book, bound["plan_id"], first, execute=True)
        self.assertEqual(first.creates, 1)
        repeated = self.bind()
        self.assertEqual(repeated["plan_id"], bound["plan_id"])
        self.assertTrue(repeated["plan_reused"])
        second = LostCreate()
        result = drafts.run_plan(self.book, repeated["plan_id"], second, execute=True)
        self.assertEqual(second.creates, 0)
        self.assertEqual(result["results"][0]["state"], "unknown")
        self.assertEqual(result["results"][0]["reason"], "previous_attempt_not_found")
        self.assertEqual(sum(row["event"] == "sending" for row in result["recent_events"]), 1)


if __name__ == "__main__":unittest.main()

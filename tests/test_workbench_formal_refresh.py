"""Live formal bounds and new local batches preserve historical platform evidence."""
from contextlib import contextmanager
import hashlib
import json
import threading
import unittest
from unittest.mock import patch

from tests import test_publish as fixtures


story = fixtures.story
drafts = story.platform_drafts
URL = "https://fanqienovel.com/main/writer/chapter-manage/1234567890&%E6%B5%8B%E8%AF%95?type=1"


class FormalRefreshTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublishTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.book, self.root = self.fixture.book, self.fixture.root
        self.book_id = self.book.meta("id")
        self.global_state = self.root.parent / "isolated-refresh-target-state"
        self.global_state.mkdir(mode=0o700)
        for value in (patch.object(drafts, "_lock_directory", return_value=self.global_state),
                      patch.object(story.platform_browser.BrowserAdapter, "__enter__",
                                   side_effect=AssertionError("Refresh must not open a browser")),
                      patch.object(drafts, "workbench_start", side_effect=AssertionError("No implicit upload"))):
            value.start()
            self.addCleanup(value.stop)

    def bind(self, start=1, end=1, **overrides):
        values = {"account_id": "12345678901", "book_management_url": URL,
                  "start": start, "end": end, "expected_book_id": self.book_id,
                  "expected_revision": self.book.meta("revision")}
        values.update(overrides)
        return drafts.workbench_bind(self.root, **values)

    def seed(self):
        self.fixture.commit()
        return self.bind()

    def refresh(self, binding, **overrides):
        revision = overrides.get("expected_revision") if "expected_revision" in overrides else self.book.meta("revision")
        values = {"expected_book_id": self.book_id, "expected_revision": revision,
                  "expected_profile_sha256": binding["profile_sha256"]}
        values.update(overrides)
        return drafts.workbench_refresh_plan(self.root, binding["plan_id"], binding["profile_id"], **values)

    def code(self, code, binding, **overrides):
        return self.fixture.assert_code(code, self.refresh, binding, **overrides)

    def profile_path(self, binding):
        return self.root / drafts.PROFILE_DIRECTORY / binding["profile_id"]

    def test_empty_state_is_read_only_and_never_prepares_a_plan(self):
        before = self.fixture.writing_snapshot()
        files = {path.relative_to(self.root) for path in self.root.rglob("*")}
        with patch.object(story.publish, "prepare", side_effect=AssertionError("No preparation on polling")):
            result = drafts.workbench_formal_state(self.root, expected_book_id=self.book_id)
        self.assertEqual((result["formal_first"], result["formal_last"], result["formal_count"]), (None, None, 0))
        self.assertEqual(result["book_id"], self.book_id)
        self.assertEqual(result["revision"], before[0])
        self.assertEqual(result["max_batch"], 1000)
        self.assertEqual(result["scope"], "local_formal_state_only")
        self.assertFalse(result["remote_accessed"])
        self.assertFalse(result["submission_performed"])
        self.assertEqual(self.fixture.writing_snapshot(), before)
        self.assertEqual({path.relative_to(self.root) for path in self.root.rglob("*")}, files)
        self.assertFalse(self.fixture.ledger.exists())

    def test_state_follows_new_formal_chapters_without_creating_a_batch(self):
        self.fixture.commit()
        first = drafts.workbench_formal_state(self.root, expected_book_id=self.book_id)
        self.fixture.commit(chapter=2)
        with patch.object(story.publish, "prepare", side_effect=AssertionError("No preparation on polling")):
            second = drafts.workbench_formal_state(self.root, expected_book_id=self.book_id)
        self.assertEqual((second["formal_first"], second["formal_last"], second["formal_count"]), (1, 2, 2))
        self.assertGreater(second["revision"], first["revision"])
        self.assertFalse(self.fixture.ledger.exists())
        self.assertNotIn("body", json.dumps(second))
        self.fixture.assert_code("platform_draft_book_changed", drafts.workbench_formal_state,
                                 self.root, expected_book_id="another-book")

    def test_append_refreshes_range_but_preserves_target_profile_and_original_batch(self):
        old = self.seed()
        old_packet = story.publish.inspect(self.book, old["plan_id"])
        profile = self.profile_path(old).read_bytes()
        self.fixture.commit(chapter=2)
        writing = self.fixture.writing_snapshot()
        result = self.refresh(old)
        self.assertEqual(result["chapters"], [1, 2])
        self.assertEqual((result["start"], result["end"]), (1, 2))
        self.assertEqual(result["book_id"], self.book_id)
        self.assertEqual(result["revision"], self.book.meta("revision"))
        self.assertEqual(result["base_plan_id"], old["plan_id"])
        self.assertNotEqual(result["plan_id"], old["plan_id"])
        for field in ("platform", "account_id", "remote_book_id", "profile_id", "profile_sha256"):
            self.assertEqual(result[field], old[field])
        self.assertEqual(result["scope"], "local_formal_refresh_only")
        self.assertFalse(result["remote_accessed"])
        self.assertFalse(result["submission_performed"])
        self.assertFalse(result["platform_acceptance_verified"])
        self.assertFalse(result["plan_reused"])
        self.assertEqual(story.publish.inspect(self.book, old["plan_id"]), old_packet)
        self.assertEqual(self.profile_path(old).read_bytes(), profile)
        self.assertEqual(self.fixture.writing_snapshot(), writing)
        self.assertFalse((self.root / drafts.DIRECTORY).exists())
        self.assertEqual(list(self.global_state.iterdir()), [])

    def test_repeated_request_reuses_current_batch_and_never_duplicates_profiles(self):
        old = self.seed()
        self.fixture.commit(chapter=2)
        first, second = self.refresh(old), self.refresh(old)
        self.assertEqual(second["plan_id"], first["plan_id"])
        self.assertTrue(second["plan_reused"])
        self.assertEqual(story.publish.list_plans(self.book)["total"], 2)
        self.assertEqual(list((self.root / drafts.PROFILE_DIRECTORY).glob("*.json")), [self.profile_path(old)])

    def test_selected_start_is_preserved_and_explicit_range_is_respected(self):
        self.fixture.commit()
        self.fixture.commit(chapter=2)
        old = self.bind(2, 2)
        self.fixture.commit(chapter=3)
        self.assertEqual(self.refresh(old)["chapters"], [2, 3])
        self.assertEqual(self.refresh(old, start=1, end=2)["chapters"], [1, 2])

    def test_invalid_range_revision_identity_and_hash_create_no_plan(self):
        old = self.seed()
        before = story.publish.inspect(self.book, old["plan_id"])
        cases = (({"start": True}, "invalid_input"), ({"end": "1"}, "invalid_input"),
                 ({"start": 2, "end": 1}, "platform_draft_invalid_range"),
                 ({"end": 1001}, "platform_draft_invalid_range"),
                 ({"end": 1000001}, "platform_draft_invalid_range"),
                 ({"expected_revision": True}, "invalid_input"),
                 ({"expected_revision": self.book.meta("revision") - 1}, "stale_revision"),
                 ({"expected_book_id": "another-book"}, "platform_draft_book_changed"),
                 ({"expected_profile_sha256": "not-a-hash"}, "platform_draft_profile_changed"))
        for values, code in cases:
            with self.subTest(values=values):
                self.code(code, old, **values)
        self.assertEqual(story.publish.list_plans(self.book)["total"], 1)
        self.assertEqual(story.publish.inspect(self.book, old["plan_id"]), before)
        self.assertEqual(drafts._WORKBENCH_BINDINGS, {})

    def test_profile_for_other_remote_book_cannot_change_the_selected_target(self):
        old = self.seed()
        other = self.bind(book_management_url=URL.replace("1234567890&", "9876543210&"))
        selected = {**old, "profile_id": other["profile_id"], "profile_sha256": other["profile_sha256"]}
        self.code("platform_draft_profile_mismatch", selected)
        self.assertEqual(story.publish.list_plans(self.book)["total"], 2)
        original = story.publish.inspect(self.book, old["plan_id"])["manifest"]
        self.assertEqual(original["remote_book_id"], "1234567890")
        self.assertEqual(original["account_id"], "12345678901")

    def test_actual_profile_hash_is_required_and_custom_profile_bytes_are_preserved(self):
        old = self.seed()
        path = self.profile_path(old)
        profile = json.loads(path.read_text())
        profile["editor"]["title"]["selector"] = "input.author-custom-title"
        path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        raw = path.read_bytes()
        self.code("platform_draft_profile_changed", old)
        actual = hashlib.sha256(raw).hexdigest()
        result = self.refresh(old, expected_profile_sha256=actual)
        self.assertEqual(result["profile_sha256"], actual)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(result["account_id"], old["account_id"])

    def test_profile_change_during_prepare_returns_no_success_and_preserves_both_histories(self):
        old = self.seed()
        before = story.publish.inspect(self.book, old["plan_id"])
        self.fixture.commit(chapter=2)
        path = self.profile_path(old)
        raw = path.read_bytes()
        actual = story.publish.prepare
        def prepare_then_change(*args, **kwargs):
            result = actual(*args, **kwargs)
            path.write_bytes(raw + b"\n")
            return result
        with patch.object(story.publish, "prepare", side_effect=prepare_then_change):
            self.code("platform_draft_profile_changed", old)
        self.assertEqual(story.publish.inspect(self.book, old["plan_id"]), before)
        self.assertEqual(path.read_bytes(), raw + b"\n")
        self.assertEqual(drafts._WORKBENCH_BINDINGS, {})
        # A completed local immutable plan is retained, never advertised as a
        # platform result or removed after the concurrent profile edit.
        self.assertEqual(story.publish.list_plans(self.book)["total"], 2)
        current = self.refresh(old, expected_profile_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertTrue(current["plan_reused"])
        self.assertFalse(current["remote_accessed"])

    def test_stale_batch_target_can_be_reused_only_with_current_qualified_formal_content(self):
        old = self.seed()
        old_manifest = story.publish.inspect(self.book, old["plan_id"])["manifest"]
        text = self.fixture.texts[1] + "她将收据仔细收进衣袋。\n"
        self.fixture.commit(text=text, replace_last=True)
        story.publish.check(self.book, old["plan_id"])
        previous = story.publish.inspect(self.book, old["plan_id"])
        self.assertNotEqual(previous["status"], "prepared")
        result = self.refresh(old)
        current = story.publish.inspect(self.book, result["plan_id"])["manifest"]
        self.assertNotEqual(result["plan_id"], old["plan_id"])
        self.assertNotEqual(current["chapters"][0]["body"], old_manifest["chapters"][0]["body"])
        self.assertIn("仔细收进衣袋", current["chapters"][0]["body"])
        self.assertEqual(story.publish.inspect(self.book, old["plan_id"]), previous)

    def test_missing_and_externally_changed_formal_sources_use_existing_qualification(self):
        old = self.seed()
        self.code("chapter_missing", old, end=2)
        path = self.root / self.book.chapter_path(1)
        path.write_text(path.read_text() + "外部改动", encoding="utf-8")
        self.code("exports_unresolved", old)
        self.assertEqual(story.publish.list_plans(self.book)["total"], 1)
        self.assertEqual(drafts._WORKBENCH_BINDINGS, {})

    def test_unreviewed_import_is_visible_but_cannot_be_included_in_a_refreshed_range(self):
        imported = self.root / "imported.md"
        imported.write_text("第1章 未审查的导入章\n" + fixtures.BODY, encoding="utf-8")
        self.book.adopt(1, imported, "隔离的未审查导入测试。", self.book.meta("revision"), volume_dir="第一卷 雨夜")
        self.fixture.commit(chapter=2)
        old = self.bind(2, 2)
        current = drafts.workbench_formal_state(self.root, expected_book_id=self.book_id)
        self.assertEqual(current["formal_last"], 2)
        self.code("review_required", old, start=1)
        self.assertEqual(story.publish.list_plans(self.book)["total"], 1)

    def test_non_fanqie_refresh_is_explicitly_unsupported_without_affecting_history(self):
        old = self.seed()
        qidian = self.fixture.prepare(platform="qidian")
        selected = {**old, "plan_id": qidian["id"]}
        before = story.publish.inspect(self.book, qidian["id"])
        error = self.code("platform_draft_refresh_unsupported", selected)
        self.assertIn("仅支持", error.message)
        self.assertEqual(story.publish.inspect(self.book, qidian["id"]), before)
        self.assertEqual(drafts.status(self.book, qidian["id"])["results"][0]["state"], "pending")

    def test_active_jobs_and_bindings_guard_book_and_remote_target(self):
        old = self.seed()
        target_hash = drafts._hash({field: old[field] for field in ("platform", "account_id", "remote_book_id")})
        for root, target in ((str(self.root), "different-target"), ("/another-book-root", target_hash)):
            with self.subTest(root=root):
                with drafts._WORKBENCH_LOCK:
                    drafts._WORKBENCH_JOBS["refresh-test"] = {"done": False, "_root": root, "_target_sha256": target}
                try:
                    self.code("platform_draft_busy", old)
                finally:
                    with drafts._WORKBENCH_LOCK:
                        drafts._WORKBENCH_JOBS.pop("refresh-test")
        for key, target in (((str(self.root), self.book_id), "different-target"), (("/another-book-root", "other"), target_hash)):
            with self.subTest(key=key):
                with drafts._WORKBENCH_LOCK:
                    drafts._WORKBENCH_BINDINGS[key] = target
                try:
                    self.code("platform_draft_busy", old)
                finally:
                    with drafts._WORKBENCH_LOCK:
                        drafts._WORKBENCH_BINDINGS.pop(key)
        self.assertTrue(self.refresh(old)["plan_reused"])

    def test_refresh_holds_shared_binding_gate_and_source_snapshot_until_prepare_finishes(self):
        old = self.seed()
        entered, release = threading.Event(), threading.Event()
        results, errors = [], []
        actual = story.publish.prepare
        def pause(book, *args, **kwargs):
            self.assertTrue(book.db.in_transaction)
            self.assertGreater(book._lock_depth, 0)
            entered.set()
            if not release.wait(10):
                raise AssertionError("Synthetic prepare release timed out")
            return actual(book, *args, **kwargs)
        def worker():
            try:
                results.append(self.refresh(old, expected_revision=old["revision"]))
            except BaseException as error:
                errors.append(error)
        with patch.object(story.publish, "prepare", side_effect=pause):
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(entered.wait(5), errors)
                self.assertIn((str(self.root), self.book_id), drafts._WORKBENCH_BINDINGS)
                self.code("platform_draft_busy", old)
                self.fixture.assert_code("platform_draft_busy", self.bind)
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(drafts._WORKBENCH_BINDINGS, {})

    def test_revision_is_rechecked_inside_operation_lock_before_new_plan_is_created(self):
        old = self.seed()
        revision = self.book.meta("revision")
        actual = story.storage.operation_lock
        armed = True
        @contextmanager
        def acquire_after_external_commit(book):
            nonlocal armed
            if armed:
                armed = False
                self.fixture.commit(chapter=2)
            with actual(book):
                yield
        with patch.object(story.storage, "operation_lock", acquire_after_external_commit):
            self.code("stale_revision", old, expected_revision=revision)
        self.assertEqual(story.publish.list_plans(self.book)["total"], 1)
        self.assertEqual(drafts._WORKBENCH_BINDINGS, {})

    def test_unknown_attempt_journal_and_target_fence_survive_refresh_without_resend(self):
        class LostReceipt:
            def __init__(self):
                self.creates = 0
            def verify_target(self, **target):
                pass
            def find_drafts(self, chapter):
                return []
            def create_draft(self, *args):
                self.creates += 1
                raise RuntimeError("Synthetic lost acknowledgment")
        old = self.seed()
        result = drafts.run_plan(self.book, old["plan_id"], LostReceipt(), execute=True)
        self.assertEqual(result["results"][0]["state"], "unknown")
        journal = self.root / drafts.DIRECTORY / old["plan_id"]
        snapshots = {path: path.read_bytes() for directory in (journal, self.global_state)
                     for path in directory.iterdir() if path.is_file()}
        previous = story.publish.inspect(self.book, old["plan_id"])
        self.fixture.commit(chapter=2)
        current = self.refresh(old)
        self.assertEqual(current["chapters"], [1, 2])
        self.assertTrue(all(path.read_bytes() == raw for path, raw in snapshots.items()))
        self.assertEqual(story.publish.inspect(self.book, old["plan_id"]), previous)
        self.assertEqual(drafts.status(self.book, old["plan_id"])["results"][0]["state"], "unknown")
        self.assertFalse((self.root / drafts.DIRECTORY / current["plan_id"]).exists())
        # An explicitly requested execution of the NEW local batch still uses
        # the existing target fence; no repeat send is allowed for chapter 1.
        adapter = LostReceipt()
        attempted = drafts.run_plan(self.book, current["plan_id"], adapter, execute=True)
        self.assertEqual(attempted["results"][0]["state"], "unknown")
        self.assertEqual(adapter.creates, 0)
        self.assertEqual(story.publish.inspect(self.book, old["plan_id"]), previous)

    def test_io_failure_releases_gate_and_does_not_return_private_error_details(self):
        old = self.seed()
        with patch.object(story.publish, "prepare", side_effect=OSError("PRIVATE PROFILE AND BODY")):
            error = self.code("platform_draft_refresh_failed", old)
        self.assertNotIn("PRIVATE", error.message)
        self.assertEqual(drafts._WORKBENCH_BINDINGS, {})
        self.assertEqual(story.publish.list_plans(self.book)["total"], 1)


if __name__ == "__main__":
    unittest.main()

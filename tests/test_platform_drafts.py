"""Draft saves are resumable, content-checked and isolated from creative history."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from tests import test_publish as fixtures


story = fixtures.story
SPEC = importlib.util.spec_from_file_location("platform_drafts_test", fixtures.TOOL.with_name("story_platform_drafts.py"))
drafts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drafts)
drafts.inject(story)


class FakeAdapter:
    def __init__(self, book):
        self.book = book
        self.remote = {}
        self.calls = []
        self.created = 0
        self.fail_create = None
        self.fail_find = False
        self.hide_all = False
        self.on_find = None
        self.on_create = None

    def call(self, name, *args):
        # Every browser operation, including identity checks, must run outside
        # the creative operation lock and outside a database read transaction.
        if self.book.db.in_transaction or getattr(self.book, "_lock_depth", 0):
            raise AssertionError("Browser operation held the creative lock")
        self.calls.append((name, *args))

    def verify_target(self, platform, account_id, remote_book_id):
        self.call("verify", platform, account_id, remote_book_id)

    def find_drafts(self, chapter):
        self.call("find", chapter)
        if self.on_find:
            callback, self.on_find = self.on_find, None
            callback()
        if self.fail_find:
            raise RuntimeError("secret URL and unpublished prose must not be logged")
        if self.hide_all:
            return []
        return [copy.deepcopy(item) for item in self.remote.values() if item["chapter"] == chapter]

    def create_draft(self, chapter, title, body):
        self.call("create", chapter)
        self.created += 1
        if self.on_create:
            self.on_create()
        if self.fail_create == "before":
            raise RuntimeError("failed before receiving; indistinguishable to caller")
        remote_id = str(self.created)
        self.remote[remote_id] = {"chapter": chapter, "remote_id": remote_id,
                                  "title": title, "body": body, "state": "draft",
                                  "url": "https://example.invalid/private?token=DO_NOT_SAVE"}
        if self.fail_create == "after":
            raise RuntimeError("platform received the content; response was lost")
        if self.fail_create == "interrupt":
            raise KeyboardInterrupt()
        return remote_id

    def read_draft(self, remote_id):
        self.call("read", remote_id)
        return copy.deepcopy(self.remote[remote_id])

    def seed(self, frozen, remote_id="existing", state="draft", **changes):
        self.remote[remote_id] = {"chapter": frozen["chapter"], "remote_id": remote_id,
                                  "title": frozen["title"], "body": frozen["body"],
                                  "state": state, **changes}


class PlatformDraftTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublishTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.commit()
        self.book = self.fixture.book
        self.plan = self.fixture.prepare()
        self.frozen = self.plan["manifest"]["chapters"][0]
        self.adapter = FakeAdapter(self.book)
        self.global_root = self.fixture.root.parent / "global-draft-state"
        self.global_root.mkdir(mode=0o700)
        self.patch_lock = patch.object(drafts, "_lock_directory", return_value=self.global_root)
        self.patch_lock.start()
        self.addCleanup(self.patch_lock.stop)

    def execute(self, plan=None, **kwargs):
        return drafts.run_plan(self.book, (plan or self.plan)["id"], self.adapter, execute=True, **kwargs)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(story.StoryError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_preview_has_target_and_never_accesses_browser_or_creates_journal(self):
        before = self.fixture.writing_snapshot()
        result = drafts.run_plan(self.book, self.plan["id"], self.adapter)
        self.assertTrue(result["ok"])
        self.assertTrue(result["preview"])
        self.assertEqual(result["account_id"], "author-one")
        self.assertEqual(result["remote_book_id"], "book-one")
        self.assertFalse(result["remote_accessed"])
        self.assertFalse(result["all_chapters_verified_this_run"])
        self.assertEqual(self.adapter.calls, [])
        self.assertFalse((self.fixture.root / drafts.DIRECTORY).exists())
        self.assertEqual(self.fixture.writing_snapshot(), before)

    def test_two_chapters_save_in_order_and_history_contains_no_content_or_secrets(self):
        self.fixture.commit(2)
        plan = self.fixture.prepare((1, 2))
        before = self.fixture.writing_snapshot()
        result = self.execute(plan)
        self.assertTrue(result["ok"], result)
        self.assertEqual([item["state"] for item in result["results"]], ["saved", "saved"])
        self.assertEqual([call[1] for call in self.adapter.calls if call[0] == "create"], [1, 2])
        self.assertEqual([call[0] for call in self.adapter.calls if call[0] in ("create", "read")],
                         ["create", "read", "create", "read"])
        self.assertTrue(result["all_chapters_verified_this_run"])
        self.assertFalse(result["submission_performed"])
        self.assertEqual(self.fixture.writing_snapshot(), before)
        files = list((self.fixture.root / drafts.DIRECTORY).rglob("*.json")) + list(self.global_root.glob("*.json"))
        self.assertTrue(files)
        for file in files:
            text = file.read_text(encoding="utf-8")
            self.assertNotIn(fixtures.QUOTE, text)
            self.assertNotIn(self.frozen["title"], text)
            self.assertNotIn("DO_NOT_SAVE", text)
            self.assertEqual(file.stat().st_nlink, 1)

    def test_repeat_always_rereads_and_reconciles_without_creating_again(self):
        self.assertTrue(self.execute()["ok"])
        result = self.execute()
        self.assertTrue(result["ok"])
        self.assertEqual(result["results"][0]["state"], "reconciled")
        self.assertEqual(self.adapter.created, 1)
        historical = drafts.status(self.book, self.plan["id"])
        self.assertTrue(historical["ok"])
        self.assertFalse(historical["source_check_performed"])
        self.assertIsNone(historical["source_matches_current"])
        self.assertFalse(historical["all_chapters_verified_this_run"])
        self.assertTrue(historical["historical_all_title_body_match"])
        self.assertFalse(historical["results"][0]["observed_this_run"])

    def test_only_newlines_are_normalized_and_mismatches_are_never_overwritten(self):
        self.adapter.seed(self.frozen, body=self.frozen["body"].replace("\n", "\r\n"))
        self.assertTrue(self.execute()["ok"])
        self.adapter.remote["existing"]["body"] += " "
        result = self.execute()
        self.assertFalse(result["ok"])
        self.assertEqual(result["results"][0]["state"], "conflict")
        self.assertFalse(result["recent_events"][-1]["body_matches"])
        self.assertEqual(self.adapter.created, 0)

    def test_multiple_drafts_or_non_draft_chapter_stop_without_create(self):
        self.adapter.seed(self.frozen, "a")
        self.adapter.seed(self.frozen, "b")
        result = self.execute()
        self.assertEqual(result["results"][0]["reason"], "multiple_remote_drafts")
        self.adapter.remote = {}
        self.adapter.seed(self.frozen, state="live")
        result = self.execute()
        self.assertEqual(result["results"][0]["state"], "blocked")
        self.assertEqual(self.adapter.created, 0)

    def test_failure_before_any_write_can_retry_but_durable_intent_without_result_cannot(self):
        self.adapter.fail_find = True
        first = self.execute()
        self.assertEqual(first["results"][0]["state"], "blocked")
        self.adapter.fail_find = False
        self.adapter.fail_create = "before"
        second = self.execute()
        self.assertEqual(second["results"][0]["state"], "unknown")
        self.adapter.fail_create = None
        third = self.execute()
        self.assertEqual(third["results"][0]["state"], "unknown")
        self.assertEqual(self.adapter.created, 1)
        self.assertNotIn("secret URL", json.dumps(third))

    def test_lost_create_response_is_reconciled_without_repeating_write(self):
        self.adapter.fail_create = "after"
        self.assertEqual(self.execute()["results"][0]["state"], "unknown")
        self.adapter.fail_create = None
        self.assertTrue(self.execute()["ok"])
        self.assertEqual(self.adapter.created, 1)
        self.assertEqual(self.execute()["results"][0]["state"], "reconciled")

    def test_process_interruption_leaves_sending_then_recovers_as_unknown_before_read(self):
        self.adapter.fail_create = "interrupt"
        with self.assertRaises(KeyboardInterrupt):
            self.execute()
        pending = drafts.status(self.book, self.plan["id"])
        self.assertEqual(pending["results"][0]["state"], "unknown")
        self.assertTrue(pending["results"][0]["recovery_required"])
        self.adapter.fail_create = None
        resumed = self.execute()
        self.assertTrue(resumed["ok"])
        self.assertIn("interrupted_sending", [item["reason"] for item in resumed["recent_events"]])
        self.assertEqual(self.adapter.created, 1)

    def test_saved_receipt_failure_retains_intent_instead_of_reporting_success(self):
        actual = drafts._Journal._write

        def failing(journal, record):
            if record["event"] == "saved":
                raise OSError("disk unavailable")
            return actual(journal, record)

        with patch.object(drafts._Journal, "_write", failing):
            self.assert_code("platform_draft_journal_write_failed", self.execute)
        pending = drafts.status(self.book, self.plan["id"])
        self.assertTrue(pending["results"][0]["recovery_required"])
        self.assertTrue(self.execute()["ok"])
        self.assertEqual(self.adapter.created, 1)

    def test_new_plan_and_restored_book_journal_cannot_bypass_target_intent(self):
        self.adapter.fail_create = "after"
        self.assertEqual(self.execute()["results"][0]["state"], "unknown")
        self.adapter.fail_create = None
        self.adapter.hide_all = True
        story.publish.cancel(self.book, self.plan["id"])
        new_plan = self.fixture.prepare()
        self.assertNotEqual(new_plan["id"], self.plan["id"])
        self.assertEqual(self.execute(new_plan)["results"][0]["state"], "unknown")
        shutil.rmtree(self.fixture.root / drafts.DIRECTORY)
        self.assertEqual(self.execute(new_plan)["results"][0]["state"], "unknown")
        self.assertEqual(self.adapter.created, 1)

    def test_another_book_root_with_same_target_cannot_bypass_unknown_attempt(self):
        self.adapter.fail_create = "before"
        self.execute()
        other = fixtures.PublishTests()
        other.setUp()
        self.addCleanup(other.tearDown)
        other.commit()
        plan = other.prepare()
        adapter = FakeAdapter(other.book)
        result = drafts.run_plan(other.book, plan["id"], adapter, execute=True)
        self.assertEqual(result["results"][0]["state"], "unknown")
        self.assertEqual(adapter.created, 0)

    def test_reconcile_only_allows_cancelled_or_stale_sources_without_creation(self):
        self.adapter.seed(self.frozen)
        story.publish.cancel(self.book, self.plan["id"])
        result = self.execute(reconcile_only=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["plan_status"], "cancelled")
        self.assertEqual(self.adapter.created, 0)
        self.assert_code("platform_draft_plan_unusable", self.execute)

    def test_source_change_or_cancel_between_lookup_and_sending_prevents_write(self):
        self.adapter.on_find = lambda: self.fixture.commit(text=self.fixture.texts[1] + "她记住了新线索。\n", replace_last=True)
        result = self.execute()
        self.assertEqual(result["results"][0]["state"], "blocked")
        self.assertEqual(result["results"][0]["reason"], "platform_draft_source_changed")
        self.assertFalse(result["source_matches_current"])
        self.assertEqual(self.adapter.created, 0)

    def test_durable_sending_uses_frozen_content_and_reports_later_local_revision(self):
        self.adapter.on_create = lambda: self.fixture.commit(text=self.fixture.texts[1] + "本地已有新版。\n", replace_last=True)
        result = self.execute()
        self.assertTrue(result["ok"])
        self.assertTrue(result["local_version_changed"])
        self.assertFalse(result["source_matches_current"])
        self.assertEqual(self.adapter.remote["1"]["body"], self.frozen["body"])

    def test_previous_success_of_later_chapter_is_not_reported_as_this_run_verification(self):
        self.fixture.commit(2)
        plan = self.fixture.prepare((1, 2))
        self.assertTrue(self.execute(plan)["ok"])
        self.adapter.remote["1"]["body"] += "变化"
        result = self.execute(plan)
        self.assertFalse(result["ok"])
        self.assertTrue(result["results"][0]["observed_this_run"])
        self.assertFalse(result["results"][1]["observed_this_run"])
        self.assertFalse(result["results"][1]["title_body_verified"])
        self.assertTrue(result["results"][1]["historical_title_body_match"])

    def test_reconcile_empty_never_creates_and_preview_flag_never_contacts_browser(self):
        result = self.execute(reconcile_only=True)
        self.assertEqual(result["results"][0]["state"], "not_found")
        self.assertEqual(self.adapter.created, 0)
        self.adapter.calls.clear()
        drafts.run_plan(self.book, self.plan["id"], self.adapter, reconcile_only=True)
        self.assertEqual(self.adapter.calls, [])

    def test_journal_symlink_or_tampered_hash_chain_blocks_platform_access(self):
        directory = self.fixture.root / drafts.DIRECTORY
        if hasattr(os, "symlink"):
            directory.symlink_to(self.global_root, target_is_directory=True)
            self.assert_code("linked_path", self.execute)
            self.assertEqual(self.adapter.calls, [])
            directory.unlink()
        self.assertTrue(self.execute()["ok"])
        event = next((directory / self.plan["id"]).glob("event-00000002-*.json"))
        record = json.loads(event.read_text(encoding="utf-8"))
        record["previous_sha256"] = "0" * 64
        event.write_text(story.dumps(record), encoding="utf-8")
        self.adapter.calls.clear()
        self.assert_code("platform_draft_journal_invalid", self.execute)
        self.assertEqual(self.adapter.calls, [])

    def test_windows_directory_guard_names_are_not_mistaken_for_journal_history(self):
        self.assertTrue(self.execute()["ok"])
        directory = self.fixture.root / drafts.DIRECTORY / self.plan["id"]
        guard = directory / (".story-pin-" + "a" * 32)
        guard.write_bytes(b"")
        self.assertTrue(drafts.status(self.book, self.plan["id"])["ok"])
        guard.unlink()

    def test_atomic_publish_crash_has_no_hardlink_alias_window(self):
        original = drafts._atomic_publish

        def interrupt_after_rename(source, target):
            original(source, target)
            if target.name.startswith("event-"):
                raise KeyboardInterrupt()

        with patch.object(drafts, "_atomic_publish", interrupt_after_rename):
            with self.assertRaises(KeyboardInterrupt):
                self.execute()
        self.assertEqual(self.adapter.created, 0)
        pending = drafts.status(self.book, self.plan["id"])
        self.assertEqual(pending["results"][0]["state"], "unknown")
        for file in (self.fixture.root / drafts.DIRECTORY / self.plan["id"]).iterdir():
            self.assertEqual(file.stat().st_nlink, 1)
        result = self.execute()
        self.assertEqual(result["results"][0]["state"], "unknown")
        self.assertEqual(self.adapter.created, 0)


if __name__ == "__main__":
    unittest.main()

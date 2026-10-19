"""Workbench draft jobs run locally with isolated fake browser sessions."""
import copy
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

from tests import test_publish as fixtures
from tests.test_platform_browser import profile


story = fixtures.story
drafts = story.platform_drafts


class FakeBrowser:
    """Validation is real; only browser entry and platform observations are fake."""
    def __init__(self, config, *, validator, remote, entered, release, calls, fail_create=False,
                 progress_callback=None, **kwargs):
        validator(config, **kwargs)
        self.remote, self.entered, self.release = remote, entered, release
        self.calls, self.fail_create, self.callback = calls, fail_create, progress_callback

    def __enter__(self):
        self.calls.append(("enter", threading.get_ident()))
        self.entered.set()
        return self

    def __exit__(self, *args):
        self.calls.append(("exit", threading.get_ident()))

    def verify_target(self, **target):
        self.calls.append(("verify", threading.get_ident()))
        if self.callback:
            self.callback({"stage": "waiting_login", "message": "PRIVATE PASSWORD MUST NOT ESCAPE"})
        if not self.release.wait(5):
            raise RuntimeError("PRIVATE PASSWORD AND FULL MANUSCRIPT")
        if self.callback:
            self.callback({"stage": "target_verified"})

    def find_drafts(self, chapter):
        self.calls.append(("find", chapter))
        return [copy.deepcopy(item) for item in self.remote.values() if item["chapter"] == chapter]

    def create_draft(self, chapter, title, body):
        self.calls.append(("create", chapter))
        key = "remote-" + str(chapter)
        self.remote[key] = {"remote_id": key, "chapter": chapter, "state": "draft", "title": title, "body": body}
        if self.fail_create:
            raise RuntimeError("PRIVATE PASSWORD AND FULL MANUSCRIPT")
        return key

    def read_draft(self, remote_id):
        self.calls.append(("read", remote_id))
        return copy.deepcopy(self.remote[remote_id])


class WorkbenchPlatformDraftTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublishTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.commit()
        self.book, self.root = self.fixture.book, self.fixture.root
        self.plan = self.fixture.prepare()
        self.directory = self.root / drafts.PROFILE_DIRECTORY
        self.directory.mkdir()
        self.profile_path = self.directory / "番茄测试.json"
        self.write_profile()
        self.remote, self.calls = {}, []
        self.browser_login_timeouts = []
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.fail_create = False
        self.validator = story.platform_browser.BrowserAdapter
        self.jobs = []
        self.global_root = self.root.parent / "global-draft-state"
        self.global_root.mkdir(mode=0o700)
        self.patches = [patch.object(drafts, "_lock_directory", return_value=self.global_root),
                        patch.object(story.platform_browser, "BrowserAdapter", side_effect=self.browser),
                        patch.object(drafts, "_browser_runtime", return_value={"status": "browser_unverified",
                            "playwright_installed": True, "chromium_installed": None})]
        for mock in self.patches:
            mock.start()
        self.addCleanup(self.cleanup_jobs)

    def cleanup_jobs(self):
        self.release.set()
        for job in self.jobs:
            self.wait_job(job)
        for mock in reversed(self.patches):
            mock.stop()
        with drafts._WORKBENCH_LOCK:
            for key in list(drafts._WORKBENCH_JOBS):
                if drafts._WORKBENCH_JOBS[key]["_root"] == str(self.root):
                    del drafts._WORKBENCH_JOBS[key]

    def write_profile(self, config=None):
        self.profile_path.write_text(json.dumps(config or profile(), ensure_ascii=False), encoding="utf-8")

    def browser(self, config, **kwargs):
        if "login_timeout_ms" in kwargs:
            self.browser_login_timeouts.append(kwargs["login_timeout_ms"])
        return FakeBrowser(config, validator=self.validator, remote=self.remote, entered=self.entered,
                           release=self.release, calls=self.calls, fail_create=self.fail_create, **kwargs)

    def preview(self, **kwargs):
        return drafts.workbench_preview(self.root, self.plan["id"], self.profile_path.name,
                                        expected_book_id=self.book.meta("id"), **kwargs)

    def start(self, preview=None, **overrides):
        preview = preview or self.preview()
        arguments = {"expected_book_id": preview["book_id"],
                     "expected_profile_sha256": preview["profile_sha256"],
                     "preview_token": preview["preview_token"], "execute": True, **overrides}
        job = drafts.workbench_start(self.root, preview["plan_id"], self.profile_path.name, **arguments)
        self.jobs.append(job["id"])
        return job

    def wait_job(self, job_id):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            job = drafts.workbench_job(self.root, job_id)
            if job["done"]:
                return job
            threading.Event().wait(.01)
        self.fail("Fake browser job did not finish")

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(story.StoryError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_catalog_paginates_frozen_plans_without_body_or_selectors(self):
        for number in range(13):
            self.fixture.prepare(remote_book_id="extra-" + str(number))
        (self.directory / "待配置.json").write_text(json.dumps(story.platform_browser.template()), encoding="utf-8")
        before = self.fixture.writing_snapshot()
        seen, offset = [], 0
        while offset is not None:
            packet = drafts.workbench_catalog(self.root, offset=offset, limit=4)
            seen += [item["id"] for item in packet["plans"]]
            offset = packet["next_offset"]
        self.assertEqual(len(seen), 14)
        self.assertEqual(len(set(seen)), 14)
        profiles = {item["id"]: item for item in packet["profiles"]}
        self.assertTrue(profiles[self.profile_path.name]["valid"])
        self.assertFalse(profiles["待配置.json"]["valid"])
        serialized = json.dumps(packet, ensure_ascii=False)
        self.assertNotIn(fixtures.QUOTE, serialized)
        self.assertNotIn("#account", serialized)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.fixture.writing_snapshot(), before)

    def test_preview_checks_local_sources_without_browser_or_journal(self):
        packet = self.preview()
        self.assertTrue(packet["can_start"])
        self.assertEqual(packet["preview"]["remote_book_id"], "book-one")
        self.assertEqual(packet["preview"]["chapters"][0]["title"], self.plan["manifest"]["chapters"][0]["title"])
        self.assertNotIn(fixtures.QUOTE, json.dumps(packet, ensure_ascii=False))
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / drafts.DIRECTORY).exists())

    def test_preview_without_profile_lists_required_configuration(self):
        result = drafts.workbench_preview(self.root, self.plan["id"])
        self.assertFalse(result["can_start"])
        self.assertEqual(result["blockers"], ["profile_required"])
        self.assertIsNone(result["preview_token"])

    def test_missing_browser_dependency_prevents_start_authorization(self):
        with patch.object(drafts, "_browser_runtime", return_value={"playwright_installed": False}):
            result = self.preview()
        self.assertFalse(result["can_start"])
        self.assertIn("platform_browser_dependency_missing", result["blockers"])
        self.assertIsNone(result["preview_token"])

    def test_runtime_eligibility_uses_selected_browser_without_assuming_chromium_missing(self):
        self.assertTrue(self.preview()["can_start"])
        config = profile()
        config["browser_channel"] = "chrome"
        self.write_profile(config)
        with patch.object(drafts, "_browser_runtime", return_value={"playwright_installed": True,
                "chromium_installed": None, "chrome_executable_present": False}):
            result = self.preview()
        self.assertFalse(result["can_start"])
        self.assertEqual(result["browser_channel"], "chrome")
        self.assertEqual(result["blockers"], ["platform_browser_chrome_unavailable"])
        self.assertIsNone(result["preview_token"])

    def test_reconcile_mode_is_bound_to_preview_authorization(self):
        save_preview = self.preview()
        self.assertEqual(save_preview["mode"], "save_draft")
        self.assert_code("platform_draft_preview_required", self.start, save_preview, reconcile_only=True)
        read_preview = self.preview(reconcile_only=True)
        self.assertEqual(read_preview["mode"], "reconcile_only")
        self.assert_code("platform_draft_preview_required", self.start, read_preview)
        completed = self.wait_job(self.start(read_preview, reconcile_only=True)["id"])
        self.assertEqual(completed["mode"], "reconcile_only")
        self.assertEqual(completed["result"]["results"][0]["state"], "not_found")
        self.assertFalse(any(call[0] == "create" for call in self.calls))

    def test_unknown_cancelled_plan_can_reconcile_without_new_creation(self):
        self.fail_create = True
        unknown = self.wait_job(self.start()["id"])
        self.assertEqual(unknown["state"], "unknown")
        story.publish.cancel(self.book, self.plan["id"])
        self.fixture.commit(text=self.fixture.texts[1] + "本地另有改稿。\n", replace_last=True)
        self.assert_code("platform_draft_plan_unusable", self.preview)
        self.fail_create = False
        preview = self.preview(reconcile_only=True)
        self.assertFalse(preview["preview"]["source_check_performed"])
        reconciled = self.wait_job(self.start(preview, reconcile_only=True)["id"])
        self.assertEqual(reconciled["state"], "succeeded", reconciled)
        self.assertEqual(reconciled["result"]["results"][0]["state"], "reconciled")
        self.assertFalse(reconciled["result"]["source_matches_current"])
        self.assertEqual(sum(call[0] == "create" for call in self.calls), 1)

    def test_start_returns_while_login_waits_and_duplicate_click_reuses_job(self):
        self.release.clear()
        preview = self.preview()
        started = time.monotonic()
        first = self.start(preview)
        self.assertLess(time.monotonic() - started, 1)
        self.assertTrue(self.entered.wait(1))
        second = self.start(preview)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["reused"])
        self.assertEqual(sum(call[0] == "enter" for call in self.calls), 1)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            observed = drafts.workbench_job(self.root, first["id"])
            if observed["state"] == "waiting_login":
                break
            threading.Event().wait(.01)
        self.assertEqual(observed["state"], "waiting_login")
        self.assertEqual(observed["verified_chapters"], 0)
        self.assertNotEqual(self.calls[0][1], threading.get_ident())
        self.assertNotIn("PRIVATE", json.dumps(observed))
        self.release.set()
        completed = self.wait_job(first["id"])
        self.assertEqual(completed["state"], "succeeded", completed)
        self.assertEqual(completed["verified_chapters"], 1)
        self.assertFalse(completed["submission_performed"])

    def test_workbench_default_login_wait_reaches_adapter_without_changing_general_default(self):
        completed = self.wait_job(self.start()["id"])
        self.assertEqual(completed["state"], "succeeded", completed)
        self.assertEqual(self.browser_login_timeouts, [600000])
        self.assertEqual(self.validator(profile()).login_timeout_ms, 180000)

    def test_one_click_saves_whole_frozen_batch_and_reconnect_lists_job(self):
        self.fixture.commit(2)
        self.plan = self.fixture.prepare((1, 2))
        before = self.fixture.writing_snapshot()
        completed = self.wait_job(self.start()["id"])
        self.assertEqual(completed["state"], "succeeded", completed)
        self.assertEqual([call[1] for call in self.calls if call[0] == "create"], [1, 2])
        self.assertEqual(completed["verified_chapters"], 2)
        catalog = drafts.workbench_catalog(self.root)
        self.assertEqual(catalog["jobs"][0]["id"], completed["id"])
        self.assertFalse(completed["job_survives_process_exit"])
        self.assertTrue(completed["durable_attempt_journal"])
        self.assertEqual(self.fixture.writing_snapshot(), before)

    def test_status_poll_and_catalog_never_refresh_or_send(self):
        completed = self.wait_job(self.start()["id"])
        before = list(self.calls)
        for _ in range(4):
            drafts.workbench_job(self.root, completed["id"])
            drafts.workbench_catalog(self.root)
        self.assertEqual(self.calls, before)

    def test_catalog_tolerates_unpublished_stage_renamed_during_local_read(self):
        completed = self.wait_job(self.start()["id"])
        journal = self.root / drafts.DIRECTORY / self.plan["id"]
        stage = journal / (".draft-stage-" + "a" * 32)
        stage.write_text("uncommitted", encoding="utf-8")
        original = story.publish._bound_stat

        def renamed(file, missing=False):
            if file.name == stage.name:
                stage.unlink(missing_ok=True)
            return original(file, missing=missing)

        with patch.object(story.publish, "_bound_stat", side_effect=renamed):
            catalog = drafts.workbench_catalog(self.root)
        self.assertEqual(catalog["plans"][0]["draft_history"]["matched_chapters"], 1)
        self.assertEqual(catalog["jobs"][0]["id"], completed["id"])

    def test_configuration_change_invalidates_preview_before_browser_entry(self):
        preview = self.preview()
        config = profile()
        config["editor"]["title"]["selector"] = "#different-title"
        self.write_profile(config)
        self.assert_code("platform_draft_profile_changed", self.start, preview)
        self.assertEqual(self.calls, [])

    def test_wrong_book_wrong_platform_and_unapproved_execution_are_blocked(self):
        self.assert_code("platform_draft_book_changed", drafts.workbench_catalog, self.root, expected_book_id="other")
        self.write_profile(profile("qidian"))
        self.assert_code("platform_draft_profile_mismatch", self.preview)
        self.write_profile()
        self.assert_code("platform_draft_execute_required", self.start, execute=False)
        self.assert_code("platform_draft_preview_required", self.start, preview_token="invented")
        self.assertEqual(self.calls, [])

    def test_expired_preview_and_worker_side_profile_change_stop_without_browser(self):
        preview = self.preview()
        with patch.object(drafts.time, "time", return_value=time.time() + 700):
            self.assert_code("platform_draft_preview_required", self.start, preview)
        original = drafts._workbench_worker

        def changed_before_worker(*args):
            config = profile()
            config["editor"]["title"]["selector"] = "#moved"
            self.write_profile(config)
            original(*args)

        with patch.object(drafts, "_workbench_worker", side_effect=changed_before_worker):
            completed = self.wait_job(self.start()["id"])
        self.assertEqual(completed["state"], "failed")
        self.assertEqual(completed["error"], "platform_draft_preview_changed")
        self.assertEqual(self.calls, [])

    def test_unsafe_profile_paths_links_and_oversized_files_are_rejected(self):
        for name in ("../outside.json", str(self.profile_path), "other\\profile.json", ".json"):
            self.assert_code("platform_draft_invalid_profile_path", drafts.workbench_preview,
                             self.root, self.plan["id"], name)
        linked = self.directory / "link.json"
        linked.symlink_to(self.profile_path)
        self.assert_code("linked_path", drafts.workbench_preview, self.root, self.plan["id"], linked.name)
        linked.unlink()
        os.link(self.profile_path, linked)
        self.assert_code("unsafe_publish_path", self.preview)
        linked.unlink()
        self.profile_path.write_bytes(b" " * (drafts.MAX_PROFILE_BYTES + 1))
        self.assert_code("platform_draft_invalid_profile_file", self.preview)
        self.assertEqual(self.calls, [])

    def test_lost_result_stays_unknown_after_registry_loss_and_no_blind_replay(self):
        self.fail_create = True
        first = self.wait_job(self.start()["id"])
        self.assertEqual(first["state"], "unknown")
        self.assertNotIn("PRIVATE", json.dumps(first))
        with drafts._WORKBENCH_LOCK:
            del drafts._WORKBENCH_JOBS[first["id"]]
        self.jobs.remove(first["id"])
        catalog = drafts.workbench_catalog(self.root)
        self.assertEqual(catalog["plans"][0]["draft_history"]["unknown_chapters"], 1)
        self.remote.clear()  # Simulate a remote list that cannot find that write.
        self.fail_create = False
        second = self.wait_job(self.start()["id"])
        self.assertEqual(second["state"], "unknown")
        self.assertEqual(sum(call[0] == "create" for call in self.calls), 1)

    def test_different_plan_for_busy_target_cannot_start_another_browser(self):
        self.release.clear()
        first = self.start()
        self.assertTrue(self.entered.wait(1))
        self.fixture.commit(2)
        alternate = self.fixture.prepare((1, 2))
        preview = drafts.workbench_preview(self.root, alternate["id"], self.profile_path.name)
        self.assert_code("platform_draft_busy", self.start, preview)
        self.assertEqual(sum(call[0] == "enter" for call in self.calls), 1)
        self.release.set()
        self.wait_job(first["id"])


if __name__ == "__main__":
    unittest.main()

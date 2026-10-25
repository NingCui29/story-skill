"""Automatic draft connection jobs with no model, Chrome, or real book access.

The controller and browser are fixed fakes. Source eligibility, frozen manifests,
HMAC authorization, job dispatch, durable draft journal, and readback comparison
use the real implementation against an isolated temporary book.
"""
import copy
import json
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

from tests import test_publish as fixtures
from tests import test_platform_browser as browser_fixtures
from tests.test_platform_browser_preview import configuration


story = fixtures.story
drafts = story.platform_drafts
_REAL_MONOTONIC = time.monotonic


class FixedWorker:
    def __init__(self, test):
        self.test = test
        self.available, self.ready, self.owned = True, threading.Event(), True
        self.ready.set()
        self.started, self.disconnecting, self.disconnected = (threading.Event() for _ in range(3))
        self.disconnect_release = threading.Event()
        self.disconnect_release.set()
        self.calls, self.failure = [], None
        self.worker_phase = None
        self.on_start = self.on_status = None

    def availability(self):
        self.calls.append(("availability",))
        return {"available": self.available, "uses_codex_quota": True,
                **({"reason": "platform_chrome_connector_unavailable"} if not self.available else {})}

    def start(self, root, plan_id, profile_id, **kwargs):
        self.calls.append(("start", str(root), plan_id, profile_id, kwargs))
        self.started.set()
        if self.on_start:
            self.on_start()
        return {"ok": True, "ready": False, "state": "connecting", "owned": self.owned,
                "binding_sha256": "fake-owned-worker-receipt", "owned_worker_id": "f" * 32 if self.owned else None}

    def status(self, root, plan_id, profile_id, **kwargs):
        self.calls.append(("status",))
        if self.on_status:
            self.on_status()
        if self.failure:
            return {"ready": False, "state": "failed", "error": self.failure, "owned": self.owned}
        ready = self.ready.is_set()
        if ready:
            self.test.health_ready = True
        return {"ready": ready, "state": "ready" if ready else "connecting", "owned": self.owned,
                "worker_phase": self.worker_phase}

    def disconnect(self, root, plan_id, profile_id, **kwargs):
        self.calls.append(("disconnect", str(root), plan_id, profile_id, kwargs))
        self.disconnecting.set()
        if not self.disconnect_release.wait(5):
            raise RuntimeError("Fake cleanup did not release")
        self.test.health_ready = False
        self.disconnected.set()
        return {"ok": True, "ready": False, "state": "closed", "owned": self.owned}


class FixedHost:
    execution_mode = "host_assisted_cua"

    def __init__(self, test, root, binding, profile, callback=None, update_existing=False):
        self.test, self.binding = test, binding
        self.update_existing = update_existing

    def __enter__(self):
        if not self.test.health_ready:
            story.fail("platform_chrome_worker_unavailable", "Fake host requires a healthy worker")
        self.test.operations.append(("enter",))
        return self

    def __exit__(self, *args):
        self.test.operations.append(("exit",))

    def verify_target(self, **target):
        self.test.assertEqual(target, self.binding["target"])
        self.test.operations.append(("verify",))

    def find_drafts(self, chapter):
        self.test.operations.append(("find", chapter))
        return [copy.deepcopy(item) for item in self.test.remote.values() if item["chapter"] == chapter]

    def create_draft(self, chapter, title, body):
        self.test.operations.append(("create", chapter))
        remote_id = "fake-" + str(chapter)
        self.test.remote[remote_id] = {"remote_id": remote_id, "chapter": chapter,
            "state": "draft", "title": title, "body": body}
        if self.test.fail_create:
            raise RuntimeError("PRIVATE unconfirmed browser response")
        return remote_id

    def read_draft(self, remote_id):
        self.test.operations.append(("read", remote_id))
        return copy.deepcopy(self.test.remote[remote_id])

    def update_draft(self, remote_id, chapter, title, body, *, expected_title, expected_body):
        self.test.assertTrue(self.update_existing)
        before = self.test.remote[remote_id]
        self.test.assertEqual((before["title"], before["body"]), (expected_title, expected_body))
        self.test.assertTrue(list((self.test.root / ".story/platform-draft-update-backups").rglob("*.json")))
        self.test.operations.append(("update", chapter))
        before.update(title=title, body=body)
        if self.test.fail_create:
            raise RuntimeError("Unconfirmed update response")
        return remote_id


class PlatformDraftAutoConnectTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublishTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.commit()
        self.book, self.root = self.fixture.book, self.fixture.root
        self.plan = self.fixture.prepare()
        directory = self.root / drafts.PROFILE_DIRECTORY
        directory.mkdir()
        self.profile_path = directory / "host-auto.json"
        self.profile = {**configuration(), "execution_mode": "host_assisted_cua"}
        self.profile_path.write_text(json.dumps(self.profile), encoding="utf-8")
        self.health_ready, self.fail_create = False, False
        self.remote, self.operations, self.jobs = {}, [], []
        self.launcher = FixedWorker(self)
        self.actual_host = story.platform_chrome_bridge.HostAdapter
        self.global_root = self.root.parent / "target-locks"
        self.global_root.mkdir(mode=0o700)
        self.patches = [patch("subprocess.Popen", side_effect=AssertionError("No subprocess/model/browser is allowed in these tests")),
            patch.object(drafts.api, "chrome_worker", self.launcher, create=True),
            patch.object(story.platform_chrome_bridge, "worker_status", side_effect=self.bridge_status),
            patch.object(story.platform_chrome_bridge, "HostAdapter", side_effect=self.host),
            patch.object(story.platform_browser.BrowserAdapter, "__enter__",
                side_effect=AssertionError("Native browser must never start")),
            patch.object(drafts, "_lock_directory", return_value=self.global_root)]
        self.patch_mocks = [item.start() for item in self.patches]
        self.host_factory = self.patch_mocks[3]
        self.addCleanup(self.cleanup_jobs)

    def bridge_status(self, *_):
        return {"attached": self.health_ready, "ready": self.health_ready,
                "state": "connected" if self.health_ready else "worker_unavailable"}

    def host(self, *args, **kwargs):
        return FixedHost(self, *args, **kwargs)

    def cleanup_jobs(self):
        self.launcher.ready.set()
        self.launcher.disconnect_release.set()
        try:
            for job_id in self.jobs:
                self.wait_job(job_id)
            if self.launcher.started.is_set():
                self.assertTrue(self.launcher.disconnected.wait(2), "Owned connector was not cleaned up")
            self.patch_mocks[0].assert_not_called()
        finally:
            for item in reversed(self.patches):
                item.stop()
            with drafts._WORKBENCH_LOCK:
                for job_id in self.jobs:
                    drafts._WORKBENCH_JOBS.pop(job_id, None)

    def preview(self, **kwargs):
        return drafts.workbench_preview(self.root, self.plan["id"], self.profile_path.name,
            expected_book_id=self.book.meta("id"), **kwargs)

    def start(self, preview=None, **overrides):
        preview = preview or self.preview()
        arguments = {"expected_book_id": preview["book_id"],
            "expected_profile_sha256": preview["profile_sha256"],
            "preview_token": preview["preview_token"] or preview["connect_token"],
            "execute": True, "auto_connect": True, **overrides}
        job = drafts.workbench_start(self.root, preview["plan_id"], self.profile_path.name, **arguments)
        self.jobs.append(job["id"])
        return job

    def wait_job(self, job_id):
        deadline = _REAL_MONOTONIC() + 6
        while _REAL_MONOTONIC() < deadline:
            job = drafts.workbench_job(self.root, job_id)
            if job["done"]:
                return job
            threading.Event().wait(.005)
        self.fail("Fake connection job did not finish")

    def assert_code(self, code, callback, *args, **kwargs):
        return self.fixture.assert_code(code, callback, *args, **kwargs)

    def starts(self):
        return [item for item in self.launcher.calls if item[0] == "start"]

    def disconnects(self):
        return [item for item in self.launcher.calls if item[0] == "disconnect"]

    def test_no_blank_format_survives_automatic_connect_update_interruption_and_readonly_recovery(self):
        self.fixture.commit(text=self.fixture.texts[1] + "\n\n", replace_last=True)
        self.plan = self.fixture.prepare()
        frozen = self.plan["manifest"]["chapters"][0]
        self.remote["same-draft"] = {"remote_id": "same-draft", "chapter": 1, "state": "draft",
                                     "title": "旧章名", "body": "旧正文\n\n第二段"}
        before = self.fixture.writing_snapshot()
        encoding = "plain_paragraphs_v1"
        preview = self.preview(body_encoding=encoding, sync_drafts=True)
        self.assertFalse(preview["remote_accessed"])
        self.assertEqual((self.starts(), self.operations), ([], []))
        self.launcher.ready.clear()
        self.fail_create = True
        job = self.start(preview, body_encoding=encoding, sync_drafts=True)
        duplicate = self.start(preview, body_encoding=encoding, sync_drafts=True)
        self.assertEqual(job["id"], duplicate["id"])
        self.launcher.ready.set()
        result = self.wait_job(job["id"])
        self.assertEqual(result["state"], "unknown", result)
        self.assertTrue(self.launcher.disconnected.wait(2))
        self.assertEqual(self.starts()[0][-1]["body_encoding"], encoding)
        self.assertEqual(self.remote["same-draft"]["title"], frozen["title"])
        expected = drafts._encoded_content(frozen, encoding, "fanqie")["body"]
        self.assertEqual(self.remote["same-draft"]["body"], expected)
        self.assertTrue(all(line.strip() for line in expected.split("\n")))
        self.fail_create = False
        preview = self.preview(body_encoding=encoding, reconcile_only=True)
        result = self.wait_job(self.start(preview, body_encoding=encoding, reconcile_only=True)["id"])
        self.assertEqual(result["state"], "succeeded_equivalent", result)
        self.assertEqual(result["accepted_chapters"], 1)
        history = drafts.status(self.book, self.plan["id"])
        self.assertEqual(history["recent_events"][-1]["encoded_content_sha256"],
                         preview["preview"]["chapters"][0]["encoded_content_sha256"])
        self.assertEqual([row for row in self.operations if row[0] == "update"], [("update", 1)])
        self.assertFalse(any(row[0] == "create" for row in self.operations))
        self.assertEqual(self.fixture.writing_snapshot(), before)

    def test_unified_sync_updates_creates_and_skips_identical_drafts_with_strict_readback(self):
        self.fixture.commit(2)
        self.fixture.commit(3)
        self.plan = self.fixture.prepare((1, 2, 3))
        frozen = self.plan['manifest']['chapters']
        encoding = 'plain_paragraphs_v1'
        self.remote['existing-one'] = {'remote_id': 'existing-one', 'chapter': 1,
            'state': 'draft', 'title': '旧章名', 'body': '旧正文\n\n第二段'}
        identical = drafts._encoded_content(frozen[2], encoding, 'fanqie')
        self.remote['existing-three'] = {'remote_id': 'existing-three', 'chapter': 3,
            'state': 'draft', **identical}
        before = self.fixture.writing_snapshot()
        options = dict(body_encoding=encoding, comparison_policy='strict', sync_drafts=True)
        preview = self.preview(**options)
        self.assertEqual(preview['mode'], 'sync_drafts')
        result = self.wait_job(self.start(preview, **options)['id'])
        self.assertEqual(result['state'], 'succeeded_equivalent', result)
        self.assertEqual(result['accepted_chapters'], 3)
        packet = result['result']
        self.assertEqual((packet['created_chapters'], packet['updated_chapters'], packet['unchanged_chapters']), (1, 1, 1))
        self.assertEqual([row for row in self.operations if row[0] in ('update', 'create')],
                         [('update', 1), ('create', 2)])
        for chapter in frozen:
            actual = next(row for row in self.remote.values() if row['chapter'] == chapter['chapter'])
            encoded = drafts._encoded_content(chapter, encoding, 'fanqie')
            self.assertEqual((actual['title'], actual['body']), (encoded['title'], encoded['body']))
        preview = self.preview(**options)
        again = self.wait_job(self.start(preview, **options)['id'])
        self.assertEqual(again['result']['unchanged_chapters'], 3)
        self.assertEqual([row for row in self.operations if row[0] in ('update', 'create')],
                         [('update', 1), ('create', 2)])
        self.assertEqual(self.fixture.writing_snapshot(), before)

    def test_preview_offers_connect_without_claiming_platform_readiness_or_starting_model(self):
        before = self.fixture.writing_snapshot()
        preview = self.preview()
        self.assertFalse(preview["can_start"])
        self.assertFalse(preview["connection_ready"])
        self.assertIsNone(preview["preview_token"])
        self.assertTrue(preview["can_connect"])
        self.assertTrue(preview["connect_token"])
        self.assertTrue(preview["automatic_connection"]["uses_codex_quota"])
        self.assertFalse(preview["remote_accessed"])
        self.assertEqual((self.starts(), self.operations), ([], []))
        self.assertEqual(self.fixture.writing_snapshot(), before)

    def test_other_platform_profiles_never_offer_automatic_connection_or_launch(self):
        for platform in ("qimao", "qidian"):
            with self.subTest(platform=platform):
                self.plan = self.fixture.prepare(platform=platform)
                self.profile = {**browser_fixtures.BrowserTests().url_guard_profile(platform), "execution_mode": "host_assisted_cua"}
                self.profile["editor"]["ready"] = browser_fixtures.field("#save", expected="保存草稿", enabled=True)
                self.profile_path.write_text(json.dumps(self.profile), encoding="utf-8")
                self.launcher.calls.clear()
                preview = self.preview()
                self.assertFalse(preview["can_start"])
                self.assertFalse(preview["can_connect"])
                self.assertIsNone(preview["connect_token"])
                self.assertEqual(preview["automatic_connection"]["reason"],
                                 "platform_chrome_worker_unsupported_platform")
                # Even a valid local preview signature cannot opt the platform
                # into the owned Fanqie worker via a crafted start request.
                _, _, _, binding = drafts._preflight(self.root, self.plan["id"], self.profile_path.name,
                                                     self.book.meta("id"))
                self.assert_code("platform_chrome_worker_unavailable", self.start, preview,
                                 preview_token=drafts._preview_token(binding))
                self.assertEqual(self.launcher.calls, [])
                self.assertEqual(self.operations, [])

    def wait_stage(self, job_id, stage):
        deadline = _REAL_MONOTONIC() + 3
        while _REAL_MONOTONIC() < deadline:
            job = drafts.workbench_job(self.root, job_id)
            if job["stage"] == stage:
                return job
            threading.Event().wait(.005)
        self.fail(f"Fake connection did not enter {stage}")

    def test_missing_chrome_wait_is_visible_and_resumes_same_job_after_health(self):
        self.launcher.ready.clear()
        self.launcher.worker_phase = "waiting_for_chrome"
        job = self.start()
        waiting = self.wait_stage(job["id"], "waiting_for_chrome")
        self.assertFalse(waiting["done"])
        self.assertEqual((len(self.starts()), self.operations), (1, []))
        self.launcher.worker_phase = "existing_tab_probe"
        self.wait_stage(job["id"], "connecting_chrome")
        self.assertEqual(self.operations, [])
        self.launcher.ready.set()
        result = self.wait_job(job["id"])
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(len(self.starts()), 1)
        self.assertEqual([op for op in self.operations if op[0] == "create"], [("create", 1)])

    def test_missing_chrome_wait_timeout_stops_without_platform_access_or_second_job(self):
        before = self.fixture.writing_snapshot()
        self.launcher.ready.clear()
        self.launcher.worker_phase = "waiting_for_chrome"
        job = self.start()
        self.wait_stage(job["id"], "waiting_for_chrome")
        self.launcher.failure = "platform_chrome_worker_browser_wait_timeout"
        result = self.wait_job(job["id"])
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"], self.launcher.failure)
        self.assertEqual((len(self.starts()), self.operations), (1, []))
        self.assertEqual(len(self.disconnects()), 1)
        self.assertEqual(self.fixture.writing_snapshot(), before)

    def test_unavailable_launcher_does_not_offer_connect_or_authorize_start(self):
        self.launcher.available = False
        preview = self.preview()
        self.assertEqual((preview["can_start"], preview["can_connect"], preview["connect_token"]), (False, False, None))
        self.assert_code("platform_draft_preview_required", self.start, preview)
        self.assertEqual(self.starts(), [])

    def test_execute_and_valid_connect_token_are_required_before_model_launch(self):
        preview = self.preview()
        self.assert_code("platform_draft_execute_required", self.start, preview, execute=False)
        self.assert_code("platform_draft_preview_required", self.start, preview, preview_token="invented")
        self.assert_code("platform_draft_preview_required", self.start, preview, reconcile_only=True)
        self.assert_code("invalid_input", self.start, preview, auto_connect="yes")
        with patch.object(drafts.time, "time", return_value=time.time() + 601):
            self.assert_code("platform_draft_preview_required", self.start, preview)
        self.assertEqual((self.starts(), self.operations), ([], []))

    def test_legacy_start_does_not_start_model_without_explicit_auto_connect(self):
        preview = self.preview()
        self.assert_code("platform_chrome_worker_unavailable", drafts.workbench_start,
            self.root, preview["plan_id"], self.profile_path.name,
            expected_book_id=preview["book_id"], expected_profile_sha256=preview["profile_sha256"],
            preview_token=preview["connect_token"], execute=True)
        self.assertEqual((self.starts(), self.operations), ([], []))

    def test_connect_token_cannot_authorize_a_changed_profile_or_other_target(self):
        preview = self.preview()
        changed = copy.deepcopy(self.profile)
        changed["editor"]["title"]["selector"] = "#changed-title"
        self.profile_path.write_text(json.dumps(changed), encoding="utf-8")
        self.assert_code("platform_draft_profile_changed", self.start, preview)
        self.profile_path.write_text(json.dumps(self.profile), encoding="utf-8")
        other = self.fixture.prepare(remote_book_id="other-target")
        other_preview = drafts.workbench_preview(self.root, other["id"], self.profile_path.name,
            expected_book_id=self.book.meta("id"))
        self.assert_code("platform_draft_preview_required", self.start, other_preview,
                         preview_token=preview["connect_token"])
        self.assertEqual((self.starts(), self.operations), ([], []))

    def test_changed_formal_source_invalidates_connect_token_before_launch(self):
        preview = self.preview()
        self.fixture.commit(text=self.fixture.texts[1] + "她收好另一张收据。\n", replace_last=True)
        self.assert_code("platform_draft_source_changed", self.start, preview)
        self.assertEqual((self.starts(), self.operations), ([], []))

    def test_no_host_entry_before_health_and_duplicate_start_reuses_one_connector(self):
        self.launcher.ready.clear()
        preview = self.preview()
        first = self.start(preview)
        self.assertTrue(self.launcher.started.wait(1))
        self.assertEqual(self.operations, [])
        self.host_factory.assert_not_called()
        second = self.start(preview)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["reused"])
        self.assertEqual(len(self.starts()), 1)
        self.launcher.ready.set()
        completed = self.wait_job(first["id"])
        self.assertEqual(completed["state"], "succeeded", completed)
        self.assertTrue(self.launcher.disconnected.wait(1))
        self.assertEqual(sum(item[0] == "create" for item in self.operations), 1)

    def test_connection_failure_cleans_worker_without_platform_entry(self):
        self.launcher.failure = "platform_chrome_worker_connect_failed"
        completed = self.wait_job(self.start()["id"])
        self.assertEqual((completed["state"], completed["error"]), ("failed", self.launcher.failure))
        self.assertEqual(self.operations, [])
        self.host_factory.assert_not_called()
        self.assertTrue(self.launcher.disconnected.wait(1))
        self.assertIs(self.disconnects()[0][-1]["keep_readback"], False)

    def test_connection_timeout_never_enters_host_and_cleans_owned_worker(self):
        self.launcher.ready.clear()
        clock = [0.]
        self.launcher.on_status = lambda: clock.__setitem__(0, 76.)
        with patch.object(drafts.time, "monotonic", side_effect=lambda: clock[0]):
            completed = self.wait_job(self.start()["id"])
        self.assertEqual((completed["state"], completed["error"]), ("failed", "platform_chrome_connection_timeout"))
        self.assertEqual(self.operations, [])
        self.host_factory.assert_not_called()
        self.assertTrue(self.launcher.disconnected.wait(1))

    def test_profile_changed_during_connection_is_rechecked_before_host_entry(self):
        def changed():
            profile = copy.deepcopy(self.profile)
            profile["editor"]["title"]["selector"] = "#changed-during-connect"
            self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        self.launcher.on_start = changed
        completed = self.wait_job(self.start()["id"])
        self.assertEqual((completed["state"], completed["error"]), ("failed", "platform_draft_preview_changed"))
        self.assertEqual(self.operations, [])
        self.host_factory.assert_not_called()
        self.assertTrue(self.launcher.disconnected.wait(1))
        self.assertEqual(self.disconnects()[0][-1]["owned_worker_id"], "f" * 32)

    def test_formal_file_changed_during_connection_is_rechecked_before_host_entry(self):
        chapter = self.root / self.book.chapter_path(1)
        original = chapter.read_bytes()
        self.launcher.on_start = lambda: chapter.write_bytes(original + "外部编辑。\n".encode())
        completed = self.wait_job(self.start()["id"])
        self.assertEqual((completed["state"], completed["error"]), ("failed", "platform_draft_source_changed"))
        self.assertEqual(self.operations, [])
        self.host_factory.assert_not_called()
        self.assertTrue(self.launcher.disconnected.wait(1))

    def test_plan_cancelled_during_connection_is_rechecked_before_host_entry(self):
        def cancelled():
            book = story.Book(self.root)
            try:
                story.publish.cancel(book, self.plan["id"])
            finally:
                book.close()
        self.launcher.on_start = cancelled
        completed = self.wait_job(self.start()["id"])
        self.assertEqual((completed["state"], completed["error"]), ("failed", "platform_draft_plan_unusable"))
        self.assertEqual(self.operations, [])
        self.host_factory.assert_not_called()
        self.assertTrue(self.launcher.disconnected.wait(1))

    def test_controller_ready_claim_cannot_replace_real_bridge_health(self):
        self.launcher.status = lambda *args, **kwargs: {"ready": True, "state": "ready", "owned": True}
        self.host_factory.side_effect = self.actual_host
        completed = self.wait_job(self.start()["id"])
        self.assertEqual((completed["state"], completed["error"]), ("failed", "platform_chrome_worker_unavailable"))
        self.assertEqual(self.operations, [])
        self.assertTrue(self.launcher.disconnected.wait(1))

    def test_completed_readback_closes_owned_pages_and_never_modifies_creative_database(self):
        before = self.fixture.writing_snapshot()
        completed = self.wait_job(self.start()["id"])
        self.assertEqual(completed["state"], "succeeded", completed)
        self.assertTrue(completed["result"]["all_chapters_verified_this_run"])
        self.assertTrue(self.launcher.disconnected.wait(1))
        self.assertIs(self.disconnects()[0][-1]["keep_readback"], False)
        self.assertEqual(self.fixture.writing_snapshot(), before)

    def test_unconfirmed_write_does_not_preserve_readback_or_replay(self):
        self.fail_create = True
        completed = self.wait_job(self.start()["id"])
        self.assertEqual(completed["state"], "unknown", completed)
        self.assertTrue(self.launcher.disconnected.wait(1))
        self.assertIs(self.disconnects()[0][-1]["keep_readback"], False)
        self.assertEqual(sum(item[0] == "create" for item in self.operations), 1)
        self.assertNotIn("PRIVATE", json.dumps(completed))

    def test_readonly_reconcile_closes_owned_pages_without_recreating_drafts(self):
        saved = self.wait_job(self.start()["id"])
        self.assertEqual(saved["state"], "succeeded", saved)
        before = self.fixture.writing_snapshot()
        remote_before = copy.deepcopy(self.remote)
        self.operations.clear()
        completed = self.wait_job(self.start(self.preview(reconcile_only=True), reconcile_only=True)["id"])
        self.assertEqual(completed["state"], "succeeded", completed)
        self.assertTrue(completed["result"]["all_chapters_verified_this_run"])
        self.assertTrue(any(item[0] == "read" for item in self.operations))
        self.assertFalse(any(item[0] == "create" for item in self.operations))
        self.assertIs(self.disconnects()[-1][-1]["keep_readback"], False)
        self.assertEqual(self.remote, remote_before)
        self.assertEqual(self.fixture.writing_snapshot(), before)

    def test_existing_healthy_manual_worker_is_used_without_model_launch_or_disconnect(self):
        self.health_ready = True
        preview = self.preview()
        self.assertEqual((preview["can_start"], preview["can_connect"]), (True, False))
        completed = self.wait_job(self.start(preview, auto_connect=False)["id"])
        self.assertEqual(completed["state"], "succeeded", completed)
        self.assertEqual((self.starts(), self.disconnects()), ([], []))

    def test_job_stays_busy_until_owned_worker_cleanup_finishes(self):
        self.launcher.disconnect_release.clear()
        job = self.start()
        self.assertTrue(self.launcher.disconnecting.wait(2))
        observed = drafts.workbench_job(self.root, job["id"])
        self.assertFalse(observed["done"], "A new job must not reuse a worker while its owner is disconnecting it")
        self.launcher.disconnect_release.set()
        self.assertTrue(self.wait_job(job["id"])["done"])

    def test_cleanup_exception_still_finishes_recorded_result_without_retry(self):
        original = self.launcher.disconnect
        class ControllerFailure(Exception):
            code = "platform_chrome_worker_close_failed"
        def failed_cleanup(*args, **kwargs):
            original(*args, **kwargs)
            raise ControllerFailure("PRIVATE controller exception")
        self.launcher.disconnect = failed_cleanup
        completed = self.wait_job(self.start()["id"])
        self.assertTrue(completed["done"])
        self.assertEqual(completed["error"], "platform_chrome_worker_close_failed")
        self.assertTrue(completed["result"]["all_chapters_verified_this_run"])
        self.assertEqual(sum(item[0] == "create" for item in self.operations), 1)
        self.assertNotIn("PRIVATE", json.dumps(completed))


    def test_specific_owned_stop_is_persisted_without_turning_an_attempt_into_readback(self):
        for sending in (False, True):
            with self.subTest(sending=sending):
                original_disconnect=self.launcher.disconnect
                def stopped(*args, **kwargs):
                    packet=original_disconnect(*args, **kwargs)
                    return {**packet,'owned_worker_id':kwargs['owned_worker_id'],'error':'platform_chrome_worker_target_tab_changed'}
                operation='create_draft' if sending else 'find_drafts'
                def fail(*args, **kwargs):
                    story.fail('platform_chrome_worker_unavailable','PRIVATE target URL')
                with patch.object(self.launcher,'disconnect',side_effect=stopped), patch.object(FixedHost,operation,side_effect=fail):
                    completed=self.wait_job(self.start()["id"])
                self.assertEqual(completed['state'],'unknown' if sending else 'failed')
                row=completed['result']['results'][0]
                self.assertTrue(row['recorded_this_run'])
                self.assertFalse(row['observed_this_run'])
                self.assertFalse(row['accepted_this_run'])
                self.assertEqual(row['reason'],'platform_chrome_worker_target_tab_changed')
                history=drafts.status(self.book,self.plan['id'])
                self.assertEqual(history['results'][0]['reason'],row['reason'])
                self.assertFalse(history['results'][0]['recorded_this_run'])
                self.assertNotIn('PRIVATE',json.dumps(completed))

    def test_unowned_cleanup_receipt_cannot_refine_a_draft_failure(self):
        original_disconnect=self.launcher.disconnect
        def stopped(*args, **kwargs):
            return {**original_disconnect(*args, **kwargs),'owned_worker_id':'e'*32,'error':'platform_chrome_worker_target_tab_changed'}
        def fail(*args, **kwargs):story.fail('platform_chrome_worker_unavailable','No browser')
        with patch.object(self.launcher,'disconnect',side_effect=stopped), patch.object(FixedHost,'find_drafts',side_effect=fail):
            completed=self.wait_job(self.start()["id"])
        self.assertEqual(completed['result']['results'][0]['reason'],'platform_chrome_worker_unavailable')


if __name__ == "__main__":
    unittest.main()

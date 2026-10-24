"""Real browser-adapter batches against in-memory pages; no browser or network."""
import copy
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import test_publish as fixtures
from tests.test_platform_browser_preview import PreviewContext, PreviewPage, configuration


story = fixtures.story
drafts = story.platform_drafts
POLICY = "fanqie_terminal_lf_v1"


class SavedPreviewContext(PreviewContext):
    def persist(self, page):
        # Simulate independent remote chapters and normalization after saving,
        # while leaving the exact input visible to the adapter's input check.
        chapter = page.nodes["#chapter"][0].value
        page.nodes["#remote-id"][0].attrs["data-id"] = "saved-" + chapter
        super().persist(page)
        data = self.editors["saved-" + chapter]
        change = self.options.get("saved_change", "terminal_lf")
        if change == "terminal_lf":
            data["body"] = data["body"].rstrip("\n")
        elif change == "internal_whitespace":
            data["body"] = data["body"].replace("\n", "\n ", 1)
        elif change == "title":
            data["title"] += "变动"


class BrowserBatchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublishTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.commit()
        self.fixture.commit(2)
        self.book, self.root = self.fixture.book, self.fixture.root
        self.plan = self.fixture.prepare((1, 2), account_id="author-1", remote_book_id="100")
        directory = self.root / drafts.PROFILE_DIRECTORY
        directory.mkdir()
        self.profile_path = directory / "synthetic-preview.json"
        self.config = configuration()
        self.profile_path.write_text(json.dumps(self.config), encoding="utf-8")
        self.contexts, self.adapters, self.jobs = [], [], []
        self.saved_change = "terminal_lf"
        self.preview_options = {}
        global_root = self.root.parent / "global-draft-state"
        global_root.mkdir(mode=0o700)
        def enter(adapter):
            context = SavedPreviewContext(adapter.profile,
                lists={item["url"]: [[]] for item in adapter.profile["chapter_lists"]},
                sync_drafts=True, saved_change=self.saved_change, **self.preview_options)
            adapter._context = context
            adapter._list_page = context.new_page()
            self.contexts.append(context)
            self.adapters.append(adapter)
            return adapter
        self.patches = [
            patch.object(drafts, "_lock_directory", return_value=global_root),
            patch.object(story.platform_browser.BrowserAdapter, "__enter__", enter),
            patch.object(drafts, "_browser_runtime", return_value={"status": "browser_unverified",
                "playwright_installed": True, "chromium_installed": None}),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for job_id in self.jobs:
            self.wait_job(job_id)
        with drafts._WORKBENCH_LOCK:
            for job_id in self.jobs:
                drafts._WORKBENCH_JOBS.pop(job_id, None)
        for item in reversed(self.patches):
            item.stop()

    def arguments(self, policy=POLICY, reconcile_only=False):
        return SimpleNamespace(command="publish-draft-run", id=self.plan["id"], execute=True,
            profile=str(self.profile_path), comparison_policy=policy, body_encoding="verbatim",
            reconcile_only=reconcile_only, timeout_ms=1000, login_timeout_ms=100)

    def wait_job(self, job_id):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            result = drafts.workbench_job(self.root, job_id)
            if result["done"]:
                return result
            time.sleep(0.01)
        self.fail("In-memory browser job did not finish")

    def assert_saved_equivalent_batch(self, result):
        if "ok" in result:
            self.assertTrue(result["ok"], result)
        self.assertEqual([row["state"] for row in result["results"]], ["saved_equivalent"] * 2)
        self.assertEqual(result["equivalent_chapters"], 2)
        self.assertTrue(result["all_chapters_accepted_this_run"])
        self.assertFalse(result["all_chapters_verified_this_run"])
        self.assertEqual(self.adapters[-1].comparison_policy, POLICY)
        with drafts._checked_plan(self.book, self.plan["id"]) as (manifest, fingerprint, _):
            with drafts._journal(self.book, self.plan["id"], manifest, fingerprint) as journal:
                events = copy.deepcopy(journal.events)
        saved = [event for event in events if event["event"] == "saved_equivalent"]
        self.assertEqual(len(saved), 2)
        for frozen, event in zip(self.plan["manifest"]["chapters"], saved):
            actual = self.contexts[-1].editors[event["remote_id"]]
            self.assertEqual(actual["body"], frozen["body"].rstrip("\n"))
            self.assertEqual(event["observed_sha256"], drafts._hash(drafts._content(actual)))
            self.assertEqual(event["content_sha256"], drafts._hash(drafts._content(frozen)))
            self.assertNotEqual(event["observed_sha256"], event["content_sha256"])
            self.assertEqual(event["observed_terminal_lf"], 0)
        self.assertFalse(any(event[0] == "goto" and "/edit/" in event[1]
                             for event in self.contexts[-1].events))

    def test_cli_first_save_accepts_bound_policy_and_continues_batch(self):
        self.assert_saved_equivalent_batch(drafts.run(self.book, self.arguments()))

    def test_workbench_first_save_accepts_bound_policy_and_continues_batch(self):
        preview = drafts.workbench_preview(self.root, self.plan["id"], self.profile_path.name,
                                           comparison_policy=POLICY)
        job = drafts.workbench_start(self.root, self.plan["id"], self.profile_path.name,
            expected_book_id=preview["book_id"], expected_profile_sha256=preview["profile_sha256"],
            preview_token=preview["preview_token"], comparison_policy=POLICY, execute=True,
            timeout_ms=1000, login_timeout_ms=100)
        self.jobs.append(job["id"])
        finished = self.wait_job(job["id"])
        self.assertIsNone(finished["error"], finished)
        self.assertEqual(finished["state"], "succeeded_equivalent")
        self.assert_saved_equivalent_batch(finished["result"])

    def assert_failed_save_cannot_blindly_repeat(self, policy):
        first = drafts.run(self.book, self.arguments(policy))
        self.assertEqual(first["results"][0]["state"], "unknown")
        self.assertEqual(first["results"][1]["state"], "pending")
        self.assertEqual(list(self.contexts[-1].editors), ["saved-1"])
        # Reopen the real adapter, but emulate a stale directory hiding the
        # saved chapter. A durable unknown intent must forbid another create.
        adapter = story.platform_browser.BrowserAdapter(self.config, comparison_policy=policy,
                                                        timeout_ms=1000, login_timeout_ms=100)
        adapter._context = self.contexts[-1]
        adapter._list_page = adapter._context.new_page()
        self.addCleanup(adapter.__exit__, None, None, None)
        adapter._context.lists = {key: [[]] for key in adapter._context.lists}
        before = list(adapter._context.events)
        second = drafts.run_plan(self.book, self.plan["id"], adapter, execute=True, comparison_policy=policy)
        self.assertFalse(second["ok"])
        self.assertEqual(second["results"][0]["state"], "unknown")
        self.assertEqual(second["results"][0]["reason"], "previous_attempt_not_found")
        self.assertEqual(second["results"][1]["state"], "pending")
        new_events = adapter._context.events[len(before):]
        self.assertFalse(any(event[0] == "fill" or event == ("click", "#create") or
                             event == ("goto", self.config["urls"]["create"]) for event in new_events))
        self.assertEqual(list(adapter._context.editors), ["saved-1"])
        return first

    def test_strict_rejects_terminal_lf_and_never_blindly_repeats(self):
        self.assert_failed_save_cannot_blindly_repeat("strict")

    def test_preview_load_delay_completes_the_batch_without_another_save(self):
        self.preview_options = {"preview_pending_reads": 2}
        self.assert_saved_equivalent_batch(drafts.run(self.book, self.arguments()))
        self.assertEqual(self.contexts[-1].preview_waits, 2)
        self.assertEqual(sum(event == ("click", "#save") for event in self.contexts[-1].events), 2)

    def test_preview_load_timeout_leaves_unknown_and_cannot_blindly_repeat(self):
        self.preview_options = {"preview_pending_reads": 100000}
        elapsed_ms = [0]
        original_wait = PreviewPage.wait_for_timeout
        def wait(page, milliseconds):
            original_wait(page, milliseconds)
            elapsed_ms[0] += milliseconds
            self.assertLessEqual(elapsed_ms[0], 1000, "Preview exceeded its original deadline")
        # Advance only the adapter's clock at actual polling boundaries, not
        # process-global time or filesystem work on a loaded Windows runner.
        clock = SimpleNamespace(monotonic=lambda: elapsed_ms[0] / 1000)
        with patch.object(story.platform_browser, "time", clock), patch.object(PreviewPage, "wait_for_timeout", wait):
            first = self.assert_failed_save_cannot_blindly_repeat(POLICY)
        self.assertEqual(first["results"][0]["reason"], "platform_browser_incomplete_dom")
        self.assertEqual(elapsed_ms[0], 1000)
        self.assertEqual(self.contexts[-1].preview_waits, 10)
        self.assertEqual(sum(event == ("click", "#save") for event in self.contexts[-1].events), 1)

    def test_terminal_policy_rejects_internal_whitespace_and_never_blindly_repeats(self):
        self.saved_change = "internal_whitespace"
        self.assert_failed_save_cannot_blindly_repeat(POLICY)

    def test_terminal_policy_rejects_title_change_and_never_blindly_repeats(self):
        self.saved_change = "title"
        self.assert_failed_save_cannot_blindly_repeat(POLICY)

    def test_missing_preview_blocks_cli_and_workbench_before_any_browser_entry(self):
        for mode in ("playwright", "host_assisted_cua"):
            for reconcile in (False, True):
                with self.subTest(mode=mode, reconcile=reconcile):
                    config = {**self.config, "execution_mode": mode}
                    config.pop("readback")
                    self.profile_path.write_text(json.dumps(config), encoding="utf-8")
                    for action in (lambda: drafts.run(self.book, self.arguments(reconcile_only=reconcile)),
                                   lambda: drafts.workbench_preview(self.root, self.plan["id"], self.profile_path.name,
                                                                    reconcile_only=reconcile)):
                        with self.assertRaises(story.StoryError) as caught:
                            action()
                        self.assertEqual(caught.exception.code, "platform_browser_readback_required")
        self.assertEqual(self.contexts, [])
        self.assertFalse((self.root / drafts.DIRECTORY).exists())


if __name__ == "__main__":
    unittest.main()

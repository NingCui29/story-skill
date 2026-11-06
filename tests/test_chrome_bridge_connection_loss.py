"""Real local queue checks for an owned execution failure; no browser is used."""
import json
from contextlib import closing
from pathlib import Path
import threading
import time
import unittest

from tests import test_platform_chrome_bridge as local_bridge


story = local_bridge.story
bridge = local_bridge.bridge
drafts = local_bridge.drafts


class ChromeBridgeConnectionLossTests(unittest.TestCase):
    def setUp(self):
        self.local = local_bridge.ChromeBridgeTests()
        self.local.setUp()
        self.addCleanup(self.local.doCleanups)
        bridge.attach(self.local.book, self.local.plan["id"], self.local.path.name, self.local.handle)
        # The shared fixture's convenience attach/driver helpers use the OS
        # default text encoding. Keep this new regression UTF-8 explicit.
        self.local.handles.append(json.loads(self.local.handle.read_text(encoding="utf-8")))
        self.local.health()
        self.binding = self.local.binding()
        self.failed = threading.Event()

    def check_connection(self):
        if self.failed.is_set():
            story.fail("platform_chrome_worker_turn_failed", "Synthetic owned controller failure")

    def adapter(self, check=None):
        return bridge.HostAdapter(self.local.root, self.binding, self.local.config,
                                  connection_check=self.check_connection if check is None else check)

    def run_async(self, action):
        results, errors = [], []
        def run():
            try:
                results.append(action())
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=run, name="connection-loss-request")
        self.local.request_threads.append(thread)
        thread.start()
        return thread, results, errors

    def claim(self, until_thread=None):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            claim = bridge.next_request(self.local.book, self.local.handle)
            if claim["request_id"]:
                return claim, json.loads(Path(claim["request_path"]).read_text(encoding="utf-8"))
            if until_thread is not None and not until_thread.is_alive():
                return None, None
            time.sleep(.01)
        self.fail("The local request was not queued")

    def respond(self, claim, request, result):
        self.local.write_response(request, result)
        bridge.respond(self.local.book, self.local.handle, claim["request_id"], self.local.response)

    def assert_ended(self, thread, errors=None):
        thread.join(2)
        self.assertFalse(thread.is_alive(), "The failed owned execution kept waiting for the operation deadline")
        if errors is not None:
            self.assertEqual([getattr(error, "code", None) for error in errors],
                             ["platform_chrome_worker_turn_failed"])

    def test_constructor_accepts_optional_connection_check(self):
        self.failed.set()
        adapter = bridge.HostAdapter(self.local.root, self.binding, self.local.config,
                                     connection_check=self.check_connection)
        with adapter, self.assertRaises(story.StoryError) as caught:
            adapter.verify_target(**self.binding["target"])
        self.assertEqual(caught.exception.code, "platform_chrome_worker_turn_failed")

    def test_claimed_900_second_read_ends_promptly_when_owned_execution_fails(self):
        self.assertEqual(bridge.OP_SECONDS["inspect_catalog"], 900)
        with self.adapter() as adapter:
            thread, results, errors = self.run_async(adapter.inspect_catalog)
            claim, request = self.claim()
            before = Path(claim["request_path"]).read_bytes()
            failed_at = time.monotonic()
            self.failed.set()
            self.assert_ended(thread, errors)
            self.assertLess(time.monotonic() - failed_at, 2)
        self.assertEqual(results, [])
        self.assertEqual(Path(claim["request_path"]).read_bytes(), before)
        self.assertIsNone(bridge.next_request(self.local.book, self.local.handle)["request_id"])

    def test_failed_claimed_write_stays_unknown_and_is_not_redispatched(self):
        def run():
            with closing(story.Book(self.local.root)) as book, self.adapter() as adapter:
                return drafts.run_plan(book, self.local.plan["id"], adapter, execute=True)
        thread, results, errors = self.run_async(run)
        operations = []
        while True:
            claim, request = self.claim()
            operations.append(request["operation"])
            if request["operation"] == "create_draft":
                before = Path(claim["request_path"]).read_bytes()
                self.failed.set()
                break
            self.respond(claim, request, self.binding["target"] if request["operation"] == "verify_target" else [])
        self.assert_ended(thread)
        self.assertEqual(errors, [])
        self.assertEqual(results[0]["results"][0]["state"], "unknown")
        self.assertEqual(results[0]["results"][0]["reason"], "platform_chrome_worker_turn_failed")
        self.assertEqual(Path(claim["request_path"]).read_bytes(), before)
        self.assertEqual(drafts.status(self.local.book, self.local.plan["id"])["results"][0]["state"], "unknown")
        self.failed.clear()
        subsequent = []
        retry_thread, retries, retry_errors = self.run_async(run)
        while True:
            later_claim, later_request = self.claim(until_thread=retry_thread)
            if later_claim is None:
                break
            subsequent.append(later_request["operation"])
            self.assertIn(later_request["operation"], {"verify_target", "find_drafts"})
            self.respond(later_claim, later_request,
                         self.binding["target"] if later_request["operation"] == "verify_target" else [])
        self.assert_ended(retry_thread)
        self.assertEqual(retry_errors, [])
        repeated = retries[0]
        self.assertEqual(operations.count("create_draft"), 1)
        self.assertNotIn("create_draft", subsequent)
        self.assertFalse(repeated["all_chapters_verified_this_run"])
        self.assertEqual(Path(claim["request_path"]).read_bytes(), before)

    def test_check_runs_outside_queue_lock_and_normal_response_is_accepted(self):
        checked = []
        waiting = threading.Event()
        def check():
            # A different thread must be able to enter the real bridge locks;
            # a same-thread status call would mask lock inversion via RLock.
            probe, results, errors = self.run_async(lambda: bridge.worker_status(self.local.root, self.binding))
            probe.join(.7)
            self.assertFalse(probe.is_alive(), "Connection checks must not hold the bridge queue lock")
            self.assertEqual(errors, [])
            self.assertTrue(results[0]["ready"])
            checked.append(True)
            if len(checked) >= 2:
                waiting.set()
        with self.adapter(check) as adapter:
            thread, results, errors = self.run_async(lambda: adapter.verify_target(**self.binding["target"]))
            self.assertTrue(waiting.wait(2), "No waiting connection check was observed")
            claim, request = self.claim()
            self.respond(claim, request, self.binding["target"])
            self.assert_ended(thread)
        self.assertEqual(errors, [])
        self.assertEqual(results, [self.binding["target"]])
        self.assertGreaterEqual(len(checked), 2)

    def test_valid_response_takes_precedence_over_a_later_execution_failure(self):
        waiting, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        checks = []
        def check():
            checks.append(True)
            if len(checks) == 2:
                waiting.set()
                if not release.wait(2):
                    raise AssertionError("The waiting check was not released")
                return
            self.check_connection()
        with self.adapter(check) as adapter:
            thread, results, errors = self.run_async(lambda: adapter.verify_target(**self.binding["target"]))
            self.assertTrue(waiting.wait(1), "No waiting connection check was observed")
            claim, request = self.claim()
            self.respond(claim, request, self.binding["target"])
            self.failed.set()
            release.set()
            self.assert_ended(thread)
        self.assertEqual(errors, [])
        self.assertEqual(results, [self.binding["target"]])

    def test_expired_browser_health_does_not_abort_an_inflight_request(self):
        with self.adapter() as adapter:
            thread, results, errors = self.run_async(lambda: adapter.verify_target(**self.binding["target"]))
            claim, request = self.claim()
            with bridge._directory(self.local.root, adapter.session_id) as directory, bridge._lock(directory):
                health = bridge._read(directory, "health.json")
                health["checked_epoch"] = time.time() - 100
                health["expires_epoch"] = health["checked_epoch"] + bridge.HEALTH_SECONDS
                bridge._write(directory, "health.json", health, replace=True)
            self.assertFalse(bridge.worker_status(self.local.root, self.binding)["ready"])
            self.respond(claim, request, self.binding["target"])
            self.assert_ended(thread)
        self.assertEqual(errors, [])
        self.assertEqual(results, [self.binding["target"]])

    def test_failure_before_dispatch_creates_no_request(self):
        self.failed.set()
        with self.adapter() as adapter:
            thread, results, errors = self.run_async(lambda: adapter.find_drafts(1))
            self.assert_ended(thread, errors)
        self.assertEqual(results, [])
        directory = self.local.root / bridge.DIRECTORY / adapter.session_id
        self.assertEqual(list(directory.glob("outbox-*.json")), [])
        self.assertEqual(list(directory.glob("claimed-*.json")), [])


if __name__ == "__main__":
    unittest.main()

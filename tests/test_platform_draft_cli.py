"""Exercise the installed command boundary without a browser or live account."""
import json
import subprocess
import sys
import unittest

from tests import test_publish as fixtures


class PlatformDraftCliTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublishTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.commit()
        self.plan = self.fixture.prepare(platform="qidian")

    def command(self, *args):
        result = subprocess.run([sys.executable, "-I", "-B", str(fixtures.TOOL), *args],
                                capture_output=True, timeout=20)
        return result, json.loads(result.stdout or result.stderr)

    def test_preview_needs_no_profile_browser_or_execution_journal(self):
        before = self.fixture.writing_snapshot()
        result, packet = self.command("publish-draft-run", "--book", str(self.fixture.root),
                                      "--id", self.plan["id"])
        self.assertEqual(result.returncode, 0, packet)
        self.assertTrue(packet["preview"])
        self.assertFalse(packet["remote_accessed"])
        self.assertEqual(packet["platform"], "qidian")
        self.assertEqual(packet["chapters"][0]["chapter"], 1)
        self.assertFalse((self.fixture.root / ".story/platform-drafts").exists())
        self.assertEqual(self.fixture.writing_snapshot(), before)

    def test_status_does_not_create_history_or_refresh_remote_facts(self):
        result, packet = self.command("publish-draft-status", "--book", str(self.fixture.root),
                                      "--id", self.plan["id"])
        self.assertEqual(result.returncode, 0, packet)
        self.assertFalse(packet["remote_accessed"])
        self.assertFalse(packet["source_check_performed"])
        self.assertIsNone(packet["source_matches_current"])
        self.assertEqual(packet["results"][0]["state"], "pending")
        self.assertFalse((self.fixture.root / ".story/platform-drafts").exists())

    def test_execute_without_profile_fails_before_creating_an_operation(self):
        result, packet = self.command("publish-draft-run", "--book", str(self.fixture.root),
                                      "--id", self.plan["id"], "--execute")
        self.assertEqual(result.returncode, 2, packet)
        self.assertFalse(packet["ok"])
        self.assertFalse((self.fixture.root / ".story/platform-drafts").exists())

    def test_each_platform_has_its_own_unconfigured_template(self):
        for platform in ("fanqie", "qimao", "qidian"):
            with self.subTest(platform=platform):
                result, packet = self.command("template", "platform-browser-" + platform)
                self.assertEqual(result.returncode, 0, packet)
                self.assertEqual(packet["platform"], platform)


if __name__ == "__main__":
    unittest.main()

"""Editor marker profile checks stop before browser or bridge side effects."""
import copy
import json
import unittest
from unittest.mock import patch

from tests import test_publish as fixtures
from tests.test_platform_browser import field
from tests.test_platform_browser_preview import configuration


story = fixtures.story
bridge = story.platform_chrome_bridge
drafts = story.platform_drafts


class EditorMarkerProfileTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublishTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.commit()
        self.book, self.root = self.fixture.book, self.fixture.root
        self.plan = self.fixture.prepare()
        self.directory = self.root / drafts.PROFILE_DIRECTORY
        self.directory.mkdir()
        self.path = self.directory / "markers.json"
        self.handle = self.root / "private-handle.json"

    def profile(self, mode):
        config = configuration()
        config["execution_mode"] = mode
        config["editor"]["new"] = field("#new", expected="新建草稿")
        return config

    def test_invalid_marker_source_blocks_catalog_preflight_and_attach(self):
        with patch.object(story.platform_browser.BrowserAdapter, "__enter__") as enter:
            for mode in ("playwright", "host_assisted_cua"):
                for marker in ("saved", "ready", "new"):
                    with self.subTest(mode=mode, marker=marker):
                        config = self.profile(mode)
                        # This formerly passed static validation and failed only
                        # when the driver tried to read the URL without templates.
                        config["editor"][marker]["source"] = "page_url"
                        self.fixture.assert_code("platform_browser_invalid_profile",
                                                 bridge.validate_profile, config)
                        self.path.write_text(json.dumps(config), encoding="utf-8")
                        catalog = drafts._profile_catalog(self.root)
                        self.assertEqual(len(catalog), 1)
                        self.assertEqual((catalog[0]["valid"], catalog[0]["error"]),
                                         (False, "platform_browser_invalid_profile"))
                        self.fixture.assert_code("platform_browser_invalid_profile",
                                                 drafts.workbench_preview, self.root,
                                                 self.plan["id"], self.path.name)
                        self.fixture.assert_code("platform_browser_invalid_profile", bridge.attach,
                                                 self.book, self.plan["id"], self.path.name, self.handle)
                        self.assertFalse(self.handle.exists())
                        self.assertFalse((self.root / bridge.DIRECTORY).exists())
            enter.assert_not_called()

    def test_valid_marker_sources_remain_available_in_profile_catalog(self):
        for mode in ("playwright", "host_assisted_cua"):
            for source in ("omitted", None, "dom"):
                with self.subTest(mode=mode, source=source):
                    config = self.profile(mode)
                    if source != "omitted":
                        for marker in ("saved", "ready", "new"):
                            config["editor"][marker]["source"] = source
                    original = copy.deepcopy(config)
                    self.assertEqual(bridge.validate_profile(config), mode)
                    self.assertEqual(config, original)
                    self.path.write_text(json.dumps(config), encoding="utf-8")
                    catalog = drafts._profile_catalog(self.root)
                    self.assertEqual(len(catalog), 1)
                    self.assertTrue(catalog[0]["valid"])
                    self.assertEqual(catalog[0]["execution_mode"], mode)


if __name__ == "__main__":
    unittest.main()

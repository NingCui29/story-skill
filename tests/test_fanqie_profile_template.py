"""Local Fanqie binding templates use inspected UI constants, never a browser."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import quote


SCRIPT = Path(__file__).resolve().parents[1] / "skills/story-skill/scripts/story_platform_browser.py"
SPEC = importlib.util.spec_from_file_location("fanqie_binding_template_browser", SCRIPT)
browser = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(browser)


class FanqieProfileTemplateTests(unittest.TestCase):
    def link(self, title="模板测试作品", mode="2"):
        return "https://fanqienovel.com/main/writer/chapter-manage/123456&" + quote(title, safe="-_.!~*'()") + "?type=" + mode

    def test_pasted_book_url_builds_inspected_preview_profile_without_browser_or_account(self):
        with patch.object(browser.BrowserAdapter, "__enter__", side_effect=AssertionError("must remain local")):
            result = browser.fanqie_profile_from_book_url(self.link(mode="1"))
        profile = result["profile"]
        self.assertEqual(result["remote_book_id"], "123456")
        self.assertEqual(result["remote_book_title"], "模板测试作品")
        self.assertEqual(profile["execution_mode"], "host_assisted_cua")
        self.assertEqual(profile["urls"]["drafts"], self.link(mode="2"))
        self.assertEqual(profile["chapter_lists"][0]["url"], self.link(mode="1"))
        self.assertEqual(profile["readback"]["body"]["source"], "fanqie_preview_paragraphs_v1")
        self.assertIn("/preview/123456&{remote_id}", profile["readback"]["url"])
        self.assertEqual(profile["chapter_lists"][0]["empty"], None)
        self.assertEqual(profile["chapter_lists"][0]["pagination"]["validated"], True)
        self.assertEqual(profile["chapter_lists"][1]["state"], {"source": "scope", "value": "草稿"})
        self.assertNotIn("account_id", profile)
        self.assertNotIn("password", str(profile).lower())

    def test_url_scope_and_invalid_encoded_title_fail_without_echoing_the_input(self):
        valid = self.link()
        cases = [valid.replace("https:", "http:"), valid.replace("fanqienovel.com", "fanqienovel.com.evil.example"),
                 valid.replace("fanqienovel.com", "user:secret@fanqienovel.com"),
                 valid.replace("fanqienovel.com", "fanqienovel.com:443"), valid + "#fragment", valid + "&redirect=PRIVATE",
                 valid.replace("?type=2", "?type=3"), valid.replace("?type=2", "?type=2&type=1"),
                 valid.replace("chapter-manage/123456&", "chapter-manage/not-digits&"),
                 "https://fanqienovel.com/main/writer/chapter-manage/123456&%FF?type=1",
                 "https://fanqienovel.com/main/writer/chapter-manage/123456&%0A?type=1",
                 self.link("含\u0085控制"), self.link("含\u202e格式"), self.link("含\u2028分隔"), self.link("含\u2029分隔"),
                 "https://fanqienovel.com/main/writer/chapter-manage/123456&%2F?type=1",
                 "https://fanqienovel.com/main/writer/chapter-manage/123456&%ZZ?type=1"]
        for url in cases:
            with self.subTest(url=url):
                with self.assertRaises(browser.BrowserAdapterError) as caught:
                    browser.fanqie_profile_from_book_url(url)
                self.assertEqual(caught.exception.code, "platform_browser_invalid_book_url")
                self.assertNotIn(url, str(caught.exception))
                self.assertNotIn("PRIVATE", str(caught.exception))

    def test_each_return_is_independent_and_contains_no_author_target_in_static_source(self):
        one = browser.fanqie_profile_from_book_url(self.link())
        two = browser.fanqie_profile_from_book_url(self.link("另一本书"))
        one["profile"]["identity"]["account"]["selector"] = "changed"
        self.assertNotEqual(two["profile"]["identity"]["account"]["selector"], "changed")
        self.assertNotIn("模板测试作品", SCRIPT.read_text(encoding="utf-8"))
        self.assertNotIn("123456", SCRIPT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

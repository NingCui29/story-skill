"""Strict read-only Fanqie preview contracts. No browser/platform access."""
import copy
import unittest
from unittest.mock import patch

from tests import test_platform_browser as fixtures
from tests import test_platform_browser_fanqie_body as dom


browser = fixtures.browser


def configuration():
    config = fixtures.BrowserTests().url_guard_profile()
    first = config["chapter_lists"][0]
    first["scope"] = "drafts"
    first["coverage"]["expected"] = "草稿箱"
    first["state"] = {"source": "scope", "value": "草稿"}
    second = copy.deepcopy(first)
    second.update(scope="non_drafts", url=config["origin"] + "/fixture/100/submitted")
    second["coverage"]["expected"] = "章节管理"
    second["state"] = fixtures.field(".state")
    config["chapter_lists"].append(second)
    config["identity"]["book"]["templates"].extend([
        config["origin"] + "/fixture/{book_id}/submitted", config["origin"] + "/fixture/{book_id}/preview/{remote_id}"])
    config["readback"] = {"url": config["origin"] + "/fixture/100/preview/{remote_id}",
        "remote_id": {"source": "page_url", "capture": "remote_id", "templates": [
            config["origin"] + "/fixture/{book_id}/preview/{remote_id}"]},
        "header": {"source": "dom", "selector": ".preview-title", "format": "chapter_title", "title_separator": " "},
        "body": {"source": "fanqie_preview_paragraphs_v1", "selector": ".preview-content > div"}}
    return config


class PreviewLocator(fixtures.Locator):
    def evaluate(self, expression, *arguments):
        if self.selector == ".preview-content > div":
            self.page.context.preview_reads += 1
            if self.page.context.options.get("bad_preview_dom"):
                return {"ok": False}
            value = self.page.context.options.get("preview_body", self.page.nodes[self.selector][0].text)
            if self.page.context.options.get("preview_changes") and self.page.context.preview_reads > 1:
                value += "正文变化"
            return {"ok": True, "value": value}
        return super().evaluate(expression, *arguments)


class PreviewPage(fixtures.Page):
    def goto(self, url, **kwargs):
        if "/preview/" not in url:
            return super().goto(url, **kwargs)
        self.context.events.append(("goto_preview", url))
        if any(getattr(page, "kind", None) in ("edit", "create") and not page.closed
               for page in self.context.pages if page is not self):
            raise AssertionError("An owned editor is still open during preview")
        self.url, self.kind = url, "preview"
        remote_id = url.rsplit("/", 1)[-1]
        data = self.context.editors[remote_id]
        header = self.context.options.get("preview_header", f"第{data['chapter']}章 " + data["title"])
        self.nodes = {".preview-title": [fixtures.Node(header)],
                      ".preview-content > div": [fixtures.Node(data["body"])]}
        if self.context.options.get("preview_redirect"):
            self.url = self.context.options["preview_redirect"]

    def locator(self, selector):
        return PreviewLocator(lambda: self._lookup(selector), self, selector)

    def close(self):
        self.context.events.append(("closed_page", getattr(self, "kind", None), self.url))
        super().close()


class PreviewContext(fixtures.Context):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.preview_reads = 0

    def new_page(self):
        page = PreviewPage(self)
        self.pages.append(page)
        return page

    def persist(self, page):
        super().persist(page)
        if page.kind == "create":
            remote_id = page.nodes["#remote-id"][0].attrs["data-id"]
            page.url = self.profile["urls"]["edit"].replace("{remote_id}", remote_id)


class NativePreviewTests(unittest.TestCase):
    def setUp(self):
        self.old_api, browser.api = browser.api, None
        self.addCleanup(setattr, browser, "api", self.old_api)

    def assert_code(self, code, action, *args, **kwargs):
        with self.assertRaises(browser.BrowserAdapterError) as caught:
            action(*args, **kwargs)
        self.assertEqual(caught.exception.code, "platform_browser_" + code)

    def opened(self, **options):
        config = configuration()
        stored = {"chapter": 12, "title": "完整标题", "body": "\n中文甲\n\n中文乙", "state": "草稿", "remote_id": "900"}
        lists = {config["chapter_lists"][0]["url"]: [[("900", 12, "草稿")]],
                 config["chapter_lists"][1]["url"]: [[]]}
        adapter = browser.BrowserAdapter(config, timeout_ms=10, login_timeout_ms=100)
        adapter._context = PreviewContext(config, lists=lists, editors={"900": stored}, **options)
        adapter._list_page = adapter._context.new_page()
        adapter.verify_target("fanqie", "author-1", "100")
        return adapter

    def test_profile_requires_exact_preview_route_for_both_ids(self):
        browser.BrowserAdapter(configuration())
        for change in (
            lambda c: c["identity"]["book"]["templates"].pop(),
            lambda c: c["readback"].update(url=c["origin"] + "/other/{remote_id}"),
            lambda c: c["readback"].update(url=c["urls"]["edit"]),
            lambda c: c["readback"]["remote_id"].update(source="dom"),
            lambda c: c["readback"]["body"].update(source="dom"),
            lambda c: c["readback"]["header"].update(attribute="title"),
            lambda c: c["readback"].update(script="untrusted()"),
        ):
            config = configuration(); change(config)
            with self.assertRaises(browser.BrowserAdapterError):
                browser.BrowserAdapter(config)

    def test_find_and_read_never_open_an_editor_with_readback_configured(self):
        adapter = self.opened()
        found = adapter.find_drafts(12)
        self.assertEqual(found[0]["body"], "\n中文甲\n\n中文乙")
        self.assertEqual(found[0]["title"], "完整标题")
        self.assertEqual(adapter.read_draft("900")["body"], found[0]["body"])
        self.assertFalse(any(getattr(page, "kind", None) in ("edit", "create") for page in adapter._context.pages))
        self.assertFalse(any(event[0] in ("fill", "click") for event in adapter._context.events))
        self.assertTrue(all(page.closed for page in adapter._context.pages if getattr(page, "kind", None) == "preview"))

    def test_header_url_and_body_changes_are_fail_closed(self):
        for options, code in (({"preview_header": " 第12章 完整标题"}, "invalid_chapter"),
                              ({"preview_header": "第13章 完整标题"}, "identity_mismatch"),
                              ({"preview_changes": True}, "editor_changed"),
                              ({"bad_preview_dom": True}, "unsupported_editor"),
                              ({"preview_redirect": configuration()["origin"] + "/fixture/100/preview/901"}, "identity_mismatch")):
            with self.subTest(options=options):
                adapter = self.opened(**options)
                adapter._chapter_ids["900"] = 12
                self.assert_code(code, adapter.read_draft, "900")

    def test_catalog_still_confirms_draft_before_and_after_preview(self):
        adapter = self.opened()
        original = adapter._confirm_listed_draft
        calls = []
        def confirm(remote_id, chapter=None):
            calls.append((remote_id, chapter))
            return original(remote_id, chapter)
        with patch.object(adapter, "_confirm_listed_draft", side_effect=confirm):
            adapter.read_draft("900")
        self.assertEqual(calls, [("900", None), ("900", 12)])
        adapter._context.lists[adapter.lists[0]["url"]] = [[]]
        self.assert_code("not_draft", adapter.read_draft, "900")

    def test_create_closes_owned_editor_before_preview_and_keeps_exact_body(self):
        adapter = self.opened(sync_drafts=True)
        value = "\n新中文\n\n末段"
        remote_id = adapter.create_draft(13, "新完整标题", value)
        self.assertEqual(remote_id, "new-1")
        self.assertEqual(adapter._context.editors[remote_id]["body"], value)
        events = adapter._context.events
        preview_index = next(i for i, event in enumerate(events) if event[0] == "goto_preview")
        self.assertTrue(any(event[:2] == ("closed_page", "create") for event in events[:preview_index]))
        self.assertFalse(any(event[0] == "goto" and "/edit/" in event[1] for event in events))


@unittest.skipUnless(dom.NODE, "Node.js required to execute the actual preview DOM reader")
class PreviewParagraphTests(unittest.TestCase):
    def setUp(self):
        self.adapter = browser.BrowserAdapter(fixtures.profile())

    def read(self, html):
        return self.adapter._preview_paragraph_value(dom.ParagraphLocator(html))

    def test_preserves_every_empty_paragraph_except_one_exact_final_sentinel(self):
        for html, value in (("<p>甲</p><p></p>", "甲"),
                            ("<p></p><p>甲</p><p></p><p>乙</p><p></p>", "\n甲\n\n乙"),
                            ("<p>甲</p><p></p><p></p><p></p>", "甲\n\n"),
                            ("<p><br></p><p>甲</p><p><br></p><p>乙</p><p></p>", "\n甲\n\n乙"),
                            ("<p>甲</p><p><br></p><p><br></p><p></p>", "甲\n\n"),
                            ("<p>  中文　</p><p></p>", "  中文　"),
                            ("<p></p>", "")):
            with self.subTest(html=html):self.assertEqual(self.read(html), value)

    def test_unknown_attributes_nodes_breaks_and_missing_sentinel_are_rejected(self):
        for html in ("", "<p>甲</p>", '<p class="x">甲</p><p></p>', "<p>甲<br></p><p></p>",
                     "<p><span>甲</span></p><p></p>", "<div>甲</div><p></p>", "\n<p>甲</p><p></p>",
                     "<p><br></p>", '<p><br class="x"></p><p></p>', "<p><br><br></p><p></p>",
                     '<p>甲</p><p id="last"></p>'):
            with self.subTest(html=html), self.assertRaises(browser.BrowserAdapterError):self.read(html)


if __name__ == "__main__":unittest.main()

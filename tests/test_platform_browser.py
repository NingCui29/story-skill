"""Offline browser-adapter contract tests. No real platform or network access."""
import builtins
import copy
import importlib.util
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urljoin


SCRIPT = Path(__file__).resolve().parents[1] / "skills/story-skill/scripts/story_platform_browser.py"
SPEC = importlib.util.spec_from_file_location("tested_story_platform_browser", SCRIPT)
browser = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(browser)


def field(selector, attribute=None, **extra):
    return {"selector": selector, "attribute": attribute, **extra}


def profile(platform="qimao"):
    """Selectors and paths exist only in the in-memory fixture below."""
    origin = "https://" + browser.OFFICIAL_HOSTS[platform]
    return {
        "schema_version": 1, "platform": platform, "origin": origin,
        "urls": {"drafts": origin + "/fixture/list", "create": origin + "/fixture/new",
                 "edit": origin + "/fixture/edit/{remote_id}"},
        "identity": {"account": field("#account", "data-id"), "book": field("#book", "data-id")},
        "chapter_lists": [{
            "scope": "all", "url": origin + "/fixture/list",
            "coverage": field("#tab", expected="章节管理"),
            "ready": field("#ready", expected="ready"),
            "filters": [field("#search", expected=""), field("#filter", expected="any")],
            "rows": ".row", "chapter": field(".number"), "state": field(".state"),
            "link": field(".link", "href"), "empty": field("#empty"),
            "pagination": {"next": field("#next"), "disabled_attribute": "disabled",
                           "disabled_value": "", "end": field("#last")}}],
        "editor": {"chapter": field("#chapter"), "title": field("#title"), "body": field("#body"),
                   "remote_id": field("#remote-id", "data-id"), "state": field("#state", expected="草稿"),
                   "save": {"mode": "button", "selector": "#save", "expected_text": "保存草稿"},
                   "saved": field("#saved", expected="已保存")}}


class Node:
    def __init__(self, text="", *, tag="DIV", value="", attrs=None, children=None,
                 visible=True, enabled=True, editable=False, click=None, fill=None):
        self.text, self.tag, self.value = text, tag, value
        self.attrs, self.children = attrs or {}, children or {}
        self.visible, self.enabled, self.editable = visible, enabled, editable
        self.click_action, self.fill_action = click, fill


def action_link(node, page):
    href = node.attrs.get("href")
    return {"url": page.url, "href": href, "documentHref": None if href is None else urljoin(page.url, href),
            "resolvedHref": None if href is None else
            urljoin(page.context.options.get("base_url", page.url), href)}


class Locator:
    def __init__(self, lookup, page, selector=""):
        self.lookup, self.page, self.selector = lookup, page, selector

    def count(self):
        return len(self.lookup())

    def nth(self, index):
        return Locator(lambda: [self.lookup()[index]], self.page)

    def locator(self, selector):
        return Locator(lambda: self.lookup()[0].children.get(selector, []), self.page, selector)

    def node(self):
        nodes = self.lookup()
        if len(nodes) != 1:
            raise RuntimeError("fixture locator missing or ambiguous")
        return nodes[0]

    def get_attribute(self, name):
        return self.node().attrs.get(name)

    def evaluate(self, expression, *arguments):
        if "draftActionLink" in expression:
            return action_link(self.node(), self.page)
        if "el.scrollTop=bottom" in expression:
            self.page.scroll_top = max(0, self.page.context.options.get("scroll_total", 100) - 100) if arguments[0] else 0
            if arguments[0] and self.page.context.options.get("rows_change_on_scroll"):
                self.page.context.lists[self.page.url] = [[("newly-loaded", 99, "草稿")]]
            return None
        if "total:el.scrollHeight" in expression:
            return {"height": 100, "total": self.page.context.options.get("scroll_total", 100),
                    "width": 100, "wide": 100,
                    "top": 0 if self.page.context.options.get("scroll_stuck") else self.page.scroll_top, "left": 0}
        return {"tag": self.node().tag, "editable": self.node().editable}

    def input_value(self):
        return self.node().value

    def inner_text(self):
        return self.node().text

    def text_content(self):
        return self.node().text

    def element_handles(self):
        return [ElementHandle(node, self.page, self.selector) for node in self.lookup()]

    def evaluate_all(self, expression, attribute=None):
        nodes = self.lookup()
        if "elements[0] === expected" in expression:
            return {"count": len(nodes), "same": len(nodes) == 1 and isinstance(attribute, ElementHandle) and
                    (nodes[0] is attribute.node or self.selector in ("#account", "#book", "#guard-id"))}
        if len(nodes) != 1:
            return {"count": len(nodes)}
        node = nodes[0]
        value = (node.attrs.get(attribute) if attribute is not None else
                 node.value if node.tag in ("INPUT", "TEXTAREA", "SELECT") else node.text)
        return {"count": 1, "value": value}

    def is_visible(self):
        return self.node().visible

    def is_enabled(self):
        return self.node().enabled

    def fill(self, value):
        self.page.context.events.append(("fill", self.selector))
        node = self.node()
        if node.editable:
            node.text = value
        else:
            node.value = value
        if node.fill_action:
            node.fill_action()

    def click(self):
        self.page.context.events.append(("click", self.selector))
        if self.node().click_action:
            self.node().click_action()

    def wait_for(self, state, timeout):
        self.page.context.events.append(("wait", self.selector))
        self.page.delayed.discard(self.selector)
        if not self.count() or (state == "visible" and not self.is_visible()):
            raise RuntimeError("fixture timeout containing PRIVATE FULL MANUSCRIPT")


class ElementHandle:
    def __init__(self, node, page, selector):
        self.node, self.page, self.selector = node, page, selector

    def dispose(self):
        pass

    def _locator(self):
        if self.node not in self.page._lookup(self.selector):
            raise RuntimeError("fixture element detached")
        return Locator(lambda: [self.node], self.page, self.selector)

    def fill(self, value):
        return self._locator().fill(value)

    def click(self):
        return self._locator().click()

    def evaluate(self, expression):
        if "currentDraftActionButton" in expression:
            node = self.node
            return {"connected": node in self.page._lookup(self.selector) and not self.page.closed,
                    "text": (node.text or node.value or "").strip(),
                    "aria": node.attrs.get("aria-label", ""), "title": node.attrs.get("title", ""),
                    **action_link(node, self.page)}
        return self._locator().evaluate(expression)


class Page:
    def __init__(self, context):
        self.context, self.url, self.nodes = context, "about:blank", {}
        self.list_index = 0
        self.delayed = set()
        self.closed = False
        self._popup_info = None
        self.scroll_top = 0
        self.section_index, self.section_menu = 0, False
        self._create_button = None

    def goto(self, url, **kwargs):
        self.context.events.append(("goto", url))
        self.url, self.list_index = url, 0
        self._create_button = None
        self.delayed = set(self.context.options.get("delayed", []))
        if url == self.context.profile["identity"]["account"].get("guard_url"):
            self.kind = "guard"
            self.context.guard_visits += 1
        elif url in self.context.lists:
            self.kind = "list"
        elif url == self.context.profile["urls"]["create"]:
            self.kind = "create"
            self._editor({"chapter": 0, "title": self.context.options.get("initial_title", ""),
                          "body": self.context.options.get("initial_body", ""), "state": "草稿", "remote_id": ""})
        else:
            self.kind = "edit"
            remote_id = next((key for key in self.context.editors if url == self.context.profile["urls"]["edit"].replace("{remote_id}", key)), url.rsplit("/", 1)[-1])
            self._editor(self.context.editors[remote_id])
        if self.context.options.get("redirect"):
            self.url = self.context.options["redirect"]

    def _editor(self, data):
        self.nodes = {
            "#chapter": [Node(tag="INPUT", value=str(data["chapter"]))],
            "#title": [Node(tag="INPUT", value=data["title"])],
            "#body": [Node(tag="DIV" if self.context.options.get("contenteditable") else "TEXTAREA",
                           value=data["body"], text=data["body"], editable=self.context.options.get("contenteditable", False))],
            "#state": [Node(data["state"])], "#remote-id": [Node(attrs={"data-id": data["remote_id"]})],
            "#saved": [Node("已保存" if self.kind == "edit" else "尚未保存")],
            "#new-marker": [Node("新建章节")],
            "#save": [Node(self.context.options.get("button_text", "保存草稿"), tag="BUTTON",
                           attrs={"aria-label": self.context.options.get("button_aria", "")},
                           click=lambda: self.context.persist(self))]}
        def on_fill():
            if self.context.options.get("autosave"):
                self.context.persist(self)
            if self.context.options.get("change_state_on_fill"):
                self.nodes["#state"][0].text = "已发布"
            if self.context.options.get("change_title_on_body") and self.nodes["#body"][0].value:
                self.nodes["#title"][0].value = "后台改动"
        for name in ("#chapter", "#title", "#body"):
            self.nodes[name][0].fill_action = on_fill

    def _lookup(self, selector):
        if selector in self.delayed or selector in self.context.options.get("missing", []):
            return []
        if selector == "#guard-id":
            if self.kind != "guard" or self.context.options.get("guard_missing"):
                return []
            value = self.context.options.get("guard_account", self.context.options.get("account", "author-1"))
            return [Node(value)]
        if selector == "#account":
            value = self.context.options.get("account", "author-1")
            if self.context.options.get("identity_ticks", 0) > 0:
                value = None
            return [Node(attrs={"data-id": value})]
        if selector == "#book":
            return [Node(attrs={"data-id": self.context.options.get("book", "book-1")})]
        if selector == "#scroll":
            return [Node()]
        if selector == ".forbidden":
            return [Node()] if self.context.options.get("forbidden") else []
        if self.kind != "list":
            return self.nodes.get(selector, [])
        sections = self.context.options.get("sections", {}).get(self.url)
        if sections:
            if selector == "#section-trigger":
                def toggle():
                    self.section_menu = not self.section_menu
                    if self.section_menu:
                        self.context.section_opens += 1
                return [Node(click=toggle)]
            if selector == "#section-selected":
                return [Node(sections[self.section_index][0])]
            if selector == ".section-option":
                if not self.section_menu:
                    return []
                def select(index):
                    self.section_index, self.list_index, self.section_menu = index, 0, False
                labels = [entry[0] for entry in sections]
                if self.context.options.get("sections_changed") and self.context.section_opens > 1:
                    labels.append("突然新增卷")
                return [Node(label, click=lambda index=index: select(index)) for index, label in enumerate(labels)]
        if selector == "#create":
            if self._create_button is not None:
                return [self._create_button]
            def create():
                self.context.created_blank += 1
                self.context.editors["empty-1"] = {"chapter": 0, "title": "", "body": "",
                                                     "state": "草稿", "remote_id": "empty-1"}
                self.context.sync_draft("empty-1", None)
                opens = self.context.options.get("creation_opens", self.context.profile["create_action"]["opens"])
                target = self.context.new_page() if opens == "popup" else self
                target.goto(self.context.profile["urls"]["edit"].replace("{remote_id}", "empty-1"))
                if self.context.options.get("creation_redirect"):
                    target.url = self.context.options["creation_redirect"]
                if self.context.options.get("creation_account"):
                    self.context.options["account"] = self.context.options["creation_account"]
                if self._popup_info is not None and not self.context.options.get("lose_popup"):
                    self._popup_info.value = target
            self._create_button = Node(self.context.options.get("create_text", "新建草稿"), tag="BUTTON",
                         attrs={"aria-label": self.context.options.get("create_aria", "")}, click=create)
            return [self._create_button]
        pages = sections[self.section_index][1] if sections else self.context.lists[self.url]
        rows = pages[self.list_index]
        last = self.list_index + 1 == len(pages)
        if selector == ".row":
            listing = next(item for item in self.context.profile["chapter_lists"] if item["url"] == self.url)
            def number(item):
                if item[1] is None:
                    return listing["chapter"].get("unnumbered_values", ["未命名草稿"])[0]
                return "第" + str(item[1]) + "章 " + item[0] if listing["chapter"].get("format") == "chapter_title" else str(item[1])
            return [Node(children={
                ".number": [Node(number(item))], ".state": [Node(item[2])],
                ".link": [Node(attrs={"href": self.context.profile["urls"]["edit"].replace("{remote_id}", item[0])})]})
                    for item in rows]
        if selector == "#tab":
            configured = next(item for item in self.context.profile["chapter_lists"] if item["url"] == self.url)
            return [Node(self.context.options.get("tab", configured["coverage"]["expected"]))]
        if selector == "#count":
            return [Node("共" + str(self.context.options.get("row_count", len(rows))) + "篇草稿")]
        if selector == "#ready":
            return [Node("loading" if self.context.options.get("loading") else "ready")]
        if selector == "#search":
            return [Node(tag="INPUT", value=self.context.options.get("search", ""))]
        if selector == "#filter":
            return [Node(tag="SELECT", value=self.context.options.get("filter", "any"))]
        if selector == "#empty":
            return [Node(visible=not rows and self.context.options.get("empty", True))]
        if selector == "#last":
            return [Node(visible=last and self.context.options.get("end", True))]
        if selector == "#next":
            if self.context.options.get("missing_next"):
                return []
            def next_page():
                if not self.context.options.get("stuck"):
                    self.list_index += 1
            return [Node(tag="BUTTON", enabled=not last,
                         attrs={"disabled": ""} if last else {}, click=next_page)]
        return []

    def locator(self, selector):
        return Locator(lambda: self._lookup(selector), self, selector)

    def expect_popup(self, timeout):
        page = self
        class PopupInfo:
            value = None
            def __enter__(self):
                page._popup_info = self
                return self
            def __exit__(self, kind, value, traceback):
                page._popup_info = None
                if kind is None and self.value is None:
                    raise RuntimeError("fixture popup timeout PRIVATE FULL MANUSCRIPT")
                return False
        return PopupInfo()

    def wait_for_load_state(self, state, timeout):
        self.context.events.append(("loaded", self.url))

    def evaluate(self, expression, *arguments):
        if "editorReadbackSnapshot" in expression:
            values = {}
            for name, spec, handles in arguments[0]:
                if len(handles) != 1:
                    values[name] = {"count": len(handles)}
                    continue
                handle = handles[0]
                node = handle.node
                connected = (handle.page is self and not self.closed and
                             (handle.selector in ("#account", "#book", "#guard-id") or
                              node in self._lookup(handle.selector)))
                attribute = spec.get("attribute")
                value = (node.attrs.get(attribute) if attribute is not None else
                         node.value if node.tag in ("INPUT", "TEXTAREA", "SELECT") else node.text)
                values[name] = {"count": 1, "connected": connected, "value": value}
            return {"url": self.url, "fields": values}
        return {"height": 100, "total": 100, "top": 0}

    def wait_for_timeout(self, timeout):
        if self.context.options.get("identity_ticks", 0) > 0:
            self.context.options["identity_ticks"] -= 1
        if self.context.options.get("guard_ticks", 0) > 0:
            self.context.options["guard_ticks"] -= 1
            if self.context.options["guard_ticks"] == 0:
                self.context.options["guard_missing"] = False
        if self.context.options.get("home_return_ticks", 0) > 0:
            self.context.options["home_return_ticks"] -= 1
            if self.context.options["home_return_ticks"] == 0:
                self.url = self.context.profile["identity"]["account"]["login_return_urls"][0]
        if self.context.options.get("loading") and self.context.options.get("loaded_pages"):
            self.context.options["loading"] = False
            self.context.lists[self.url] = self.context.options["loaded_pages"]
        if self.context.options.get("login_return_ticks", 0) > 0:
            self.context.options["login_return_ticks"] -= 1
            if self.context.options["login_return_ticks"] == 0:
                self.url = self.context.profile["urls"]["drafts"]
        time.sleep(min(timeout / 1000, 0.002))

    def close(self):
        self.closed = True

    def is_closed(self):
        return self.closed


class Context:
    def __init__(self, configuration, *, lists=None, editors=None, **options):
        self.profile, self.options, self.events, self.pages = configuration, options, [], []
        self.lists = lists or {configuration["chapter_lists"][0]["url"]: [[]]}
        self.editors = copy.deepcopy(editors or {})
        self.created_blank = 0
        self.guard_visits = 0
        self.section_opens = 0

    def new_page(self):
        page = Page(self)
        self.pages.append(page)
        return page

    def persist(self, page):
        if self.options.get("no_save"):
            return
        node = page.nodes["#body"][0]
        remote_id = page.nodes["#remote-id"][0].attrs["data-id"] or "new-1"
        data = {"chapter": int(page.nodes["#chapter"][0].value), "title": page.nodes["#title"][0].value,
                "body": node.text if node.editable else node.value,
                "state": page.nodes["#state"][0].text, "remote_id": remote_id}
        if self.options.get("persist_different_body"):
            data["body"] = "云端旧正文"
        self.editors[remote_id] = data
        self.sync_draft(remote_id, data["chapter"])
        page.nodes["#remote-id"][0].attrs["data-id"] = remote_id
        page.nodes["#saved"][0].text = "已保存"

    def sync_draft(self, remote_id, chapter):
        if self.options.get("sync_drafts"):
            for listing in self.profile["chapter_lists"]:
                if listing["scope"] == "drafts":
                    pages = self.lists[listing["url"]]
                    for page in pages:
                        page[:] = [item for item in page if item[0] != remote_id]
                    pages[-1].append((remote_id, chapter, "草稿"))

    def close(self):
        for page in self.pages:
            page.close()


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.old_api = browser.api
        browser.api = None

    def tearDown(self):
        browser.api = self.old_api

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(browser.BrowserAdapterError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, "platform_browser_" + code)
        self.assertNotIn("PRIVATE FULL MANUSCRIPT", str(caught.exception))
        return caught.exception

    def opened(self, configuration=None, **kwargs):
        configuration = configuration or profile()
        adapter = browser.BrowserAdapter(configuration, timeout_ms=5, login_timeout_ms=100)
        adapter._context = Context(configuration, **kwargs)
        adapter._list_page = adapter._context.new_page()
        return adapter

    def verified(self, configuration=None, **kwargs):
        adapter = self.opened(configuration, **kwargs)
        adapter.verify_target(adapter.platform, "author-1", "book-1")
        return adapter

    def test_three_templates_are_explicitly_unconfigured(self):
        for platform in browser.OFFICIAL_HOSTS:
            value = browser.template(platform)
            self.assertEqual(value["platform"], platform)
            error = self.assert_code("invalid_profile", browser.BrowserAdapter, value)
            self.assertEqual(error.details["field"], "urls.drafts")
        self.assert_code("invalid_profile", browser.template, "other")

    def test_editor_dom_markers_reject_non_dom_sources_before_browser_entry(self):
        for marker in ("saved", "ready", "new"):
            for source in ("page_url", "guard_dom", "listing", "prosemirror_paragraphs", "", False, []):
                with self.subTest(marker=marker, source=source):
                    configuration = profile()
                    configuration["editor"][marker] = field("#" + marker, expected="confirmed", source=source)
                    with patch.object(browser.BrowserAdapter, "__enter__") as enter:
                        error = self.assert_code("invalid_profile", browser.BrowserAdapter, configuration)
                    self.assertEqual(error.details["field"], "editor." + marker + ".source")
                    enter.assert_not_called()

    def test_editor_dom_markers_preserve_implicit_null_and_explicit_dom_sources(self):
        for source in ("omitted", None, "dom"):
            with self.subTest(source=source):
                configuration = profile()
                configuration["editor"]["ready"] = field("#save", expected="保存草稿", enabled=True)
                configuration["editor"]["new"] = field("#state", expected="草稿")
                if source != "omitted":
                    for marker in ("saved", "ready", "new"):
                        configuration["editor"][marker]["source"] = source
                adapter = self.verified(configuration)
                remote_id = adapter.create_draft(3, "标志兼容", "正文")
                self.assertEqual(adapter.read_draft(remote_id)["body"], "正文")
                self.assertEqual(adapter._context.events.count(("click", "#save")), 1)

    def test_strict_https_origin_and_routes(self):
        from tests.test_platform_browser_preview import configuration as preview_profile
        for platform in browser.OFFICIAL_HOSTS:
            browser.BrowserAdapter(preview_profile() if platform == "fanqie" else profile(platform))
        for url in ("http://zuozhe.qimao.com/x", "https://zuozhe.qimao.com:443/x",
                    "https://user:password@zuozhe.qimao.com/x", "https://zuozhe.qimao.com.evil.test/x",
                    "https://evil.test/x", "https://zuozhe.qimao.com/x#fragment", "https://zuozhe.qimao.com\\@evil.test/x"):
            configuration = profile()
            configuration["urls"]["create"] = url
            self.assert_code("unsafe_url", browser.BrowserAdapter, configuration)

    def url_guard_profile(self, platform="qimao"):
        configuration = profile(platform)
        origin = configuration["origin"]
        configuration["urls"] = {"drafts": origin + "/fixture/100/list", "create": origin + "/fixture/100/new",
                                 "edit": origin + "/fixture/100/edit/{remote_id}?from=edit"}
        configuration["chapter_lists"][0]["url"] = configuration["urls"]["drafts"]
        configuration["identity"] = {
            "account": {"source": "guard_dom", "guard_url": origin + "/fixture/guard", "selector": "#guard-id",
                        "attribute": None, "login_return_urls": [origin + "/fixture/home"]},
            "book": {"source": "page_url", "capture": "book_id", "id_kind": "digits", "templates": [
                origin + "/fixture/{book_id}/list", origin + "/fixture/{book_id}/new",
                origin + "/fixture/{book_id}/edit/{remote_id}?from=edit"]}}
        configuration["editor"]["remote_id"] = {"source": "page_url", "capture": "remote_id", "templates": [
            origin + "/fixture/{book_id}/edit/{remote_id}?from=edit"]}
        return configuration

    def test_url_fields_capture_only_explicit_templates_and_stable_id_formats(self):
        configuration = self.url_guard_profile()
        adapter = browser.BrowserAdapter(configuration)
        spec = configuration["identity"]["book"]
        self.assertEqual(adapter._url_value(configuration["urls"]["drafts"], spec), "100")
        for destination in (configuration["urls"]["drafts"] + "?unknown=1",
                            configuration["origin"] + "/fixture/not-digits/list",
                            "https://elsewhere.example/fixture/100/list"):
            with self.assertRaises(browser.BrowserAdapterError):
                adapter._url_value(destination, spec)
        for templates in ([configuration["origin"] + "/fixture/{other}"],
                          [configuration["origin"] + "/fixture/{book_id}/{book_id}"],
                          ["https://elsewhere.example/{book_id}"]):
            configuration["identity"]["book"]["templates"] = templates
            with self.assertRaises(browser.BrowserAdapterError):
                browser.BrowserAdapter(configuration)

    def test_guard_page_is_persistent_and_refreshed_at_sensitive_boundaries(self):
        configuration = self.url_guard_profile()
        adapter = self.opened(configuration)
        adapter.verify_target("qimao", "author-1", "100")
        guard = adapter._guard_page
        self.assertIsNotNone(guard)
        self.assertFalse(guard.closed)
        visits = adapter._context.guard_visits
        adapter._require_identity(adapter._list_page)
        self.assertIs(adapter._guard_page, guard)
        self.assertGreater(adapter._context.guard_visits, visits)
        adapter._context.options["guard_account"] = "changed-author"
        self.assert_code("identity_mismatch", adapter._require_identity, adapter._list_page)
        self.assertFalse(guard.closed)
        adapter.close()
        self.assertTrue(guard.closed)

    def test_login_guard_polling_never_reopens_or_reload_the_login_page(self):
        configuration = self.url_guard_profile()
        adapter = self.opened(configuration, guard_missing=True, guard_ticks=2)
        adapter.verify_target("qimao", "author-1", "100")
        # One visit starts the guard; one final post-login fresh identity check.
        self.assertEqual(adapter._context.guard_visits, 2)
        self.assertEqual(sum(page.kind == "guard" for page in adapter._context.pages), 1)
        self.assertFalse(adapter._guard_page.closed)

    def test_completed_login_home_is_returned_to_the_fixed_work_route(self):
        configuration = self.url_guard_profile()
        adapter = self.opened(configuration)
        original_goto = adapter._list_page.goto
        first = [True]
        def initial_login(url, **kwargs):
            original_goto(url, **kwargs)
            if first[0]:
                first[0] = False
                adapter._list_page.url = configuration["identity"]["account"]["login_return_urls"][0]
        with patch.object(adapter._list_page, "goto", side_effect=initial_login):
            adapter.verify_target("qimao", "author-1", "100")
        self.assertEqual(adapter._list_page.url, configuration["urls"]["drafts"])
        self.assertEqual(sum(event == ("goto", configuration["urls"]["drafts"]) for event in adapter._context.events), 2)
        self.assertEqual(adapter._target, ("qimao", "author-1", "100"))

    def test_guard_login_page_is_retained_until_an_observed_home_arrival(self):
        configuration = self.url_guard_profile()
        adapter = self.opened(configuration)
        account = configuration["identity"]["account"]
        adapter._login_active, adapter._login_deadline = True, time.monotonic() + 1
        self.assertEqual(adapter._guard_account_value(account), "author-1")
        guard = adapter._guard_page
        guard.url = configuration["origin"] + "/fixture/login"
        adapter._guard_wait_url = guard.url
        visits = adapter._context.guard_visits
        self.assertIsNone(adapter._guard_account_value(account))
        self.assertIsNone(adapter._guard_account_value(account))
        self.assertEqual(adapter._context.guard_visits, visits)
        self.assertFalse(guard.closed)
        guard.url = account["login_return_urls"][0]
        self.assertEqual(adapter._guard_account_value(account), "author-1")
        self.assertEqual(adapter._context.guard_visits, visits + 1)
        self.assertIs(adapter._guard_page, guard)

    def test_manual_login_uses_commit_without_reloading_a_pending_dom(self):
        configuration = self.url_guard_profile()
        adapter = self.opened(configuration, guard_missing=True, guard_ticks=2)
        adapter._native_navigation_timeout = TimeoutError
        original = Page.goto
        modes = []
        def navigation(page, url, **kwargs):
            modes.append((adapter._login_active, kwargs["wait_until"]))
            original(page, url, **kwargs)
            if adapter._login_active and kwargs["wait_until"] == "domcontentloaded":
                raise TimeoutError("fixture DCL delayed by manual login")
        with patch.object(Page, "goto", new=navigation):
            adapter.verify_target("qimao", "author-1", "100")
        self.assertTrue(all(mode == "commit" for active, mode in modes if active))
        self.assertIn((False, "domcontentloaded"), modes)
        self.assertEqual(adapter._context.guard_visits, 2)

    def test_committed_official_login_timeout_waits_without_repeating_navigation(self):
        adapter = self.opened()
        adapter._native_navigation_timeout = TimeoutError
        adapter._login_active, adapter._login_deadline = True, time.monotonic() + 1
        def committed(url, **kwargs):
            adapter._list_page.url = url
            raise TimeoutError("fixture native navigation timeout")
        with patch.object(adapter._list_page, "goto", side_effect=committed) as navigation:
            adapter._goto(adapter._list_page, adapter.urls["drafts"])
        navigation.assert_called_once()
        self.assertEqual(navigation.call_args.kwargs["wait_until"], "commit")
        self.assertFalse(adapter._list_page.closed)
        def uncommitted(url, **kwargs):
            adapter._list_page.url = "about:blank"
            raise TimeoutError("fixture native navigation timeout")
        with patch.object(adapter._list_page, "goto", side_effect=uncommitted):
            self.assert_code("navigation_timeout", adapter._goto, adapter._list_page, adapter.urls["drafts"])
        adapter._list_page.url = adapter.urls["drafts"]
        with patch.object(adapter._list_page, "goto", side_effect=RuntimeError("other failure")):
            with self.assertRaises(RuntimeError):
                adapter._goto(adapter._list_page, adapter.urls["drafts"])

    def test_manual_login_deadline_includes_initial_navigation(self):
        adapter = self.opened()
        adapter.login_timeout_ms = 20
        adapter._native_navigation_timeout = TimeoutError
        requested = []
        clock = [100.0]
        def delayed(url, **kwargs):
            requested.append(kwargs["timeout"])
            adapter._list_page.url = url
            # Windows 3.10's clock resolution can hide a short real sleep.
            # Advance the navigation beyond the original total budget exactly.
            clock[0] += .03
            raise TimeoutError("fixture committed after total deadline")
        with patch.object(adapter._list_page, "goto", side_effect=delayed), \
                patch.object(browser.time, "monotonic", side_effect=lambda: clock[0]):
            self.assert_code("login_required", adapter.verify_target, "qimao", "author-1", "book-1")
        self.assertEqual(len(requested), 1)
        self.assertLessEqual(requested[0], 20)
        self.assertFalse(adapter._login_active)
        self.assertIsNone(adapter._target)

    def test_guard_is_not_opened_until_login_reaches_an_observed_return_route(self):
        configuration = self.url_guard_profile()
        adapter = self.opened(configuration, redirect=configuration["origin"] + "/fixture/login")
        adapter.login_timeout_ms = 5
        self.assert_code("login_required", adapter.verify_target, "qimao", "author-1", "100")
        self.assertEqual(adapter._context.guard_visits, 0)
        self.assertIsNone(adapter._guard_page)

    def test_merged_chapter_fields_are_strict_and_unnumbered_only_when_explicit(self):
        adapter = browser.BrowserAdapter(profile())
        spec = {"format": "chapter_title", "title_separator": " ", "unnumbered_values": ["未命名草稿"]}
        self.assertEqual(adapter._chapter("第11章 多段测试", spec), 11)
        self.assertIsNone(adapter._chapter("未命名草稿", spec))
        for text in ("第11章标题", "第十一章 标题", "第0章 标题", "第11章 ", "其他 第11章 标题", "第11章 标题\n其他字段"):
            self.assert_code("invalid_chapter", adapter._chapter, text, spec)

    def all_rows_profile(self):
        configuration = profile()
        configuration["chapter_lists"][0]["pagination"] = {"mode": "all_rows", "validated": True,
            "container": {"selector": "#scroll"}, "reject_selectors": [".forbidden"]}
        return configuration

    def test_all_rows_mode_requires_positive_evidence_and_stable_scroll_endpoints(self):
        configuration = self.all_rows_profile()
        adapter = self.verified(configuration)
        self.assertEqual(adapter.find_drafts(1), [])
        for options in ({"forbidden": True}, {"rows_change_on_scroll": True}, {"scroll_total": 500, "scroll_stuck": True}):
            adapter = self.verified(configuration, **options)
            self.assert_code("incomplete_list", adapter.find_drafts, 1)
        configuration["chapter_lists"][0]["pagination"]["validated"] = False
        self.assert_code("invalid_profile", browser.BrowserAdapter, configuration)

    def test_missing_empty_evidence_refuses_zero_rows_even_when_all_rows_declared(self):
        configuration = self.all_rows_profile()
        configuration["chapter_lists"][0]["empty"] = None
        adapter = self.verified(configuration)
        self.assert_code("incomplete_list", adapter.find_drafts, 1)

    def test_sections_exhaust_all_volumes_and_refuse_dynamic_or_duplicate_entries(self):
        configuration = self.all_rows_profile()
        listing = configuration["chapter_lists"][0]
        listing["sections"] = {"trigger": {"selector": "#section-trigger"}, "options": ".section-option",
                               "selected": {"selector": "#section-selected", "attribute": None}}
        sections = {listing["url"]: [("第一卷", [[("other", 2, "草稿")]]), ("第二卷", [[("published", 1, "已发布")]])]}
        adapter = self.verified(configuration, sections=sections)
        self.assert_code("chapter_exists_not_draft", adapter.find_drafts, 1)
        sections[listing["url"]][1] = ("第二卷", [[("next", 3, "草稿")]])
        adapter = self.verified(configuration, sections=sections)
        self.assertEqual(adapter.find_drafts(1), [])
        adapter = self.verified(configuration, sections=sections, sections_changed=True)
        self.assert_code("incomplete_list", adapter.find_drafts, 1)
        sections[listing["url"]][1] = ("第一卷", [[("next", 3, "草稿")]])
        adapter = self.verified(configuration, sections=sections)
        self.assert_code("incomplete_list", adapter.find_drafts, 1)

    def listing_evidence_profile(self):
        configuration = self.all_rows_profile()
        first = configuration["chapter_lists"][0]
        first["scope"] = "drafts"
        first["coverage"]["expected"] = "草稿箱"
        first["chapter"].update(format="chapter_title", title_separator=" ", unnumbered_values=["未命名草稿"])
        first["state"] = {"source": "scope", "value": "草稿"}
        first["count"] = {"selector": "#count", "attribute": None, "format": "draft_count"}
        second = copy.deepcopy(first)
        second.update(scope="non_drafts", url=configuration["origin"] + "/fixture/submitted")
        second["coverage"]["expected"] = "章节管理"
        second["state"] = field(".state")
        second["chapter"].pop("unnumbered_values")
        second.pop("count")
        configuration["chapter_lists"].append(second)
        configuration["editor"]["state"] = {"source": "listing", "expected": "草稿"}
        configuration["editor"]["ready"] = field("#save", expected="保存草稿", enabled=True)
        return configuration

    def test_scope_constants_need_draft_scope_and_matching_count(self):
        configuration = self.listing_evidence_profile()
        adapter = self.verified(configuration, row_count=1)
        self.assert_code("incomplete_list", adapter.find_drafts, 1)
        configuration["chapter_lists"][0]["scope"] = "all"
        configuration["chapter_lists"][0]["chapter"].pop("unnumbered_values")
        self.assert_code("incomplete_scope", browser.BrowserAdapter, configuration)

    def test_editor_draft_evidence_comes_from_complete_lists_not_save_buttons(self):
        configuration = self.listing_evidence_profile()
        draft_url, other_url = [item["url"] for item in configuration["chapter_lists"]]
        stored = {"chapter": 1, "title": "标题", "body": "正文", "state": "草稿", "remote_id": "draft1"}
        adapter = self.verified(configuration, lists={draft_url: [[("draft1", 1, "草稿")]], other_url: [[]]}, editors={"draft1": stored})
        self.assertEqual(adapter.find_drafts(1)[0]["body"], "正文")
        adapter._context.lists[draft_url] = [[]]
        self.assert_code("not_draft", adapter.read_draft, "draft1")
        adapter._context.lists[other_url] = [[("draft1", 1, "已发布")]]
        self.assert_code("chapter_exists_not_draft", adapter.read_draft, "draft1")

    def test_new_empty_cloud_draft_is_listed_before_fill_and_full_read_reconfirms_state(self):
        configuration = self.listing_evidence_profile()
        configuration["urls"]["create"] = configuration["urls"]["drafts"]
        configuration["create_action"] = {"mode": "button", "selector": "#create", "expected_text": "新建草稿", "opens": "popup"}
        drafts_url, other_url = [item["url"] for item in configuration["chapter_lists"]]
        adapter = self.verified(configuration, lists={drafts_url: [[]], other_url: [[]]}, sync_drafts=True)
        self.assertEqual(adapter.create_draft(12, "完整链路", "\n 首段\n\n末段\n\n"), "empty-1")
        self.assertEqual(adapter._context.created_blank, 1)
        self.assertEqual(adapter.read_draft("empty-1")["body"], "\n 首段\n\n末段\n\n")
        self.assertGreater(sum(event == ("goto", drafts_url) for event in adapter._context.events), 4)
        original = adapter._value
        def move_to_published(root, spec):
            value = original(root, spec)
            if spec is configuration["editor"]["body"] or spec.get("selector") == "#body":
                adapter._context.lists[drafts_url] = [[]]
                adapter._context.lists[other_url] = [[("empty-1", 12, "已发布")]]
            return value
        with patch.object(adapter, "_value", side_effect=move_to_published):
            self.assert_code("chapter_exists_not_draft", adapter.read_draft, "empty-1")

    def test_unnamed_old_rows_cannot_be_assumed_to_have_no_chapter(self):
        configuration = self.listing_evidence_profile()
        draft_url, other_url = [item["url"] for item in configuration["chapter_lists"]]
        existing = {"chapter": 12, "title": "", "body": "", "state": "草稿", "remote_id": "manual-blank"}
        adapter = self.verified(configuration, lists={draft_url: [[("manual-blank", None, "草稿")]], other_url: [[]]},
                                editors={"manual-blank": existing})
        error = self.assert_code("unnumbered_draft", adapter.find_drafts, 12)
        self.assertIn("只读对账", str(error))
        self.assertFalse(any(event[0] in ("fill", "click") for event in adapter._context.events))
        self.assertFalse(any(event[0] == "goto" and "/edit/" in event[1] for event in adapter._context.events))

    def test_created_blank_exception_is_bound_to_its_exact_remote_id(self):
        configuration = self.listing_evidence_profile()
        draft_url, other_url = [item["url"] for item in configuration["chapter_lists"]]
        adapter = self.verified(configuration, lists={draft_url: [[("new-own", None, "草稿")]], other_url: [[]]})
        adapter._confirm_listed_draft("new-own")
        adapter._context.lists[draft_url][0].append(("manual-other", None, "草稿"))
        self.assert_code("unnumbered_draft", adapter._confirm_listed_draft, "new-own")
        adapter._context.lists[draft_url] = [[("new-own", 12, "草稿"), ("manual-other", None, "草稿")]]
        self.assert_code("unnumbered_draft", adapter._confirm_listed_draft, "new-own", 12)

    def test_other_unnamed_row_after_creation_leaves_durable_unknown_and_prevents_recreation(self):
        from tests import test_publish as fixtures
        fixture = fixtures.PublishTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.commit()
        plan = fixture.prepare(platform="qimao")
        configuration = self.listing_evidence_profile()
        configuration["urls"]["create"] = configuration["urls"]["drafts"]
        configuration["create_action"] = {"mode": "button", "selector": "#create", "expected_text": "新建草稿", "opens": "popup"}
        draft_url, other_url = [item["url"] for item in configuration["chapter_lists"]]
        adapter = self.opened(configuration, account="author-one", book="book-one",
                              lists={draft_url: [[]], other_url: [[]]}, sync_drafts=True)
        sync = adapter._context.sync_draft
        def concurrent_manual_draft(remote_id, chapter):
            sync(remote_id, chapter)
            if chapter is None:
                adapter._context.lists[draft_url][0].append(("manual-other", None, "草稿"))
        global_state = fixture.root.parent / "isolated-target-state"
        global_state.mkdir(mode=0o700)
        with patch.object(fixtures.story.platform_drafts, "_lock_directory", return_value=global_state), \
                patch.object(adapter._context, "sync_draft", side_effect=concurrent_manual_draft):
            result = fixtures.story.platform_drafts.run_plan(fixture.book, plan["id"], adapter, execute=True)
            self.assertEqual(result["results"][0]["state"], "unknown")
            self.assertEqual(adapter._context.created_blank, 1)
            self.assertFalse(any(event[0] == "fill" for event in adapter._context.events))
            # Losing visibility of both partial drafts is not evidence that the
            # earlier cloud creation failed. The durable target intent fences it.
            adapter._context.lists[draft_url] = [[]]
            retry = fixtures.story.platform_drafts.run_plan(fixture.book, plan["id"], adapter, execute=True)
            self.assertEqual(retry["results"][0]["state"], "unknown")
            self.assertEqual(adapter._context.created_blank, 1)

    def test_url_book_identity_rejects_a_row_link_to_another_work(self):
        configuration = self.url_guard_profile()
        configuration["urls"]["edit"] = configuration["urls"]["edit"].replace("/100/", "/200/")
        adapter = self.opened(configuration, lists={configuration["urls"]["drafts"]: [[("draft1", 1, "草稿")]]})
        adapter.verify_target("qimao", "author-1", "100")
        self.assert_code("identity_mismatch", adapter.find_drafts, 1)

    def test_editor_ready_and_exact_blank_checks_prevent_placeholder_or_whitespace_overwrite(self):
        configuration = profile()
        configuration["editor"]["ready"] = field("#save", expected="保存草稿", enabled=True)
        adapter = self.verified(configuration, button_text="尚未加载")
        self.assert_code("incomplete_dom", adapter.create_draft, 1, "标题", "正文")
        self.assertFalse(any(event[0] == "fill" for event in adapter._context.events))
        adapter = self.verified(configuration, initial_body="\n ")
        self.assert_code("not_new_draft", adapter.create_draft, 1, "标题", "正文")
        self.assertFalse(any(event[0] == "fill" for event in adapter._context.events))

    def test_login_origins_are_optional_exact_https_roots(self):
        self.assertEqual(browser.BrowserAdapter(profile()).login_origins, set())
        configuration = profile()
        configuration["login_origins"] = ["https://fixture-login.example"]
        self.assertEqual(browser.BrowserAdapter(configuration).login_origins, {"https://fixture-login.example"})
        for values in (None, "https://fixture-login.example", ["http://fixture-login.example"],
                       ["https://fixture-login.example/"], ["https://fixture-login.example/path"],
                       ["https://fixture-login.example?next=x"], ["https://fixture-login.example#hash"],
                       ["https://fixture-login.example:443"], ["https://user:secret@fixture-login.example"],
                       ["https://*.example"], ["https://fixture-login.example", "https://fixture-login.example"],
                       ["https://zuozhe.qimao.com"]):
            configuration["login_origins"] = values
            with self.assertRaises(browser.BrowserAdapterError):
                browser.BrowserAdapter(configuration)

    def test_login_allowlist_does_not_extend_draft_urls(self):
        for name in ("drafts", "create", "edit"):
            configuration = profile()
            configuration["login_origins"] = ["https://fixture-login.example"]
            configuration["urls"][name] = configuration["urls"][name].replace(configuration["origin"], configuration["login_origins"][0])
            self.assert_code("unsafe_url", browser.BrowserAdapter, configuration)

    def test_dynamic_urls_are_limited_to_one_remote_id(self):
        for key, suffix in (("create", "/{remote_id}"), ("edit", "/{book_id}"),
                            ("edit", "/{remote_id}/{remote_id}"), ("edit", "/static")):
            configuration = profile()
            configuration["urls"][key] = configuration["origin"] + suffix
            self.assert_code("invalid_profile", browser.BrowserAdapter, configuration)

    def test_css_child_combinators_are_valid_configured_selectors(self):
        configuration = profile()
        configuration["editor"]["body"]["selector"] = ".editor > textarea"
        adapter = browser.BrowserAdapter(configuration)
        self.assertEqual(adapter.profile["editor"]["body"]["selector"], ".editor > textarea")

    def test_required_identity_state_and_pagination_fail_early(self):
        for path in (("identity", "account"), ("editor", "state"),
                     ("chapter_lists", 0, "pagination"), ("editor", "saved")):
            configuration = profile()
            parent = configuration
            for part in path[:-1]:
                parent = parent[part]
            del parent[path[-1]]
            self.assert_code("invalid_profile", browser.BrowserAdapter, configuration)

    def test_only_draft_box_does_not_prove_no_published_chapter(self):
        configuration = profile()
        configuration["chapter_lists"][0]["scope"] = "drafts"
        self.assert_code("incomplete_scope", browser.BrowserAdapter, configuration)

    def test_forbidden_save_buttons_rejected_before_browser_open(self):
        for text in ("发布", "保存草稿并发布", "存草稿，下一步", "定时发布", "Submit draft", "送审草稿", "删除草稿", "管理草稿", "Delete draft"):
            configuration = profile()
            configuration["editor"]["save"]["expected_text"] = text
            self.assert_code("unsafe_save", browser.BrowserAdapter, configuration)

    def test_editor_cannot_read_stale_html_value_attributes(self):
        for name in ("chapter", "title", "body"):
            configuration = profile()
            configuration["editor"][name]["attribute"] = "value"
            self.assert_code("invalid_profile", browser.BrowserAdapter, configuration)

    def test_dependency_missing_is_optional_and_explicit(self):
        adapter = browser.BrowserAdapter(profile())
        original = builtins.__import__
        def importing(name, *args, **kwargs):
            if name == "playwright.sync_api":
                raise ImportError("fixture missing dependency")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=importing):
            self.assert_code("dependency_missing", adapter.__enter__)

    def test_runtime_status_is_read_only_and_does_not_claim_browser_availability(self):
        with patch.object(browser.util, "find_spec", return_value=None), patch.object(browser.metadata, "version") as version, patch.object(browser, "_chrome_executable_present", return_value=True):
            result = browser.browser_runtime_status()
        version.assert_not_called()
        self.assertEqual(result, {"playwright_installed": False, "playwright_version": None,
                                  "chromium_installed": None, "chrome_executable_present": True,
                                  "status": "dependency_missing"})
        with patch.object(browser.util, "find_spec", return_value=object()), patch.object(browser.metadata, "version", return_value="fixture-version"), patch.object(browser, "_chrome_executable_present", return_value=False):
            result = browser.browser_runtime_status()
        self.assertEqual(result, {"playwright_installed": True, "playwright_version": "fixture-version",
                                  "chromium_installed": None, "chrome_executable_present": False,
                                  "status": "browser_unverified"})
        with patch.object(browser.util, "find_spec", side_effect=ValueError("broken fixture import")):
            self.assertEqual(browser.browser_runtime_status()["status"], "dependency_missing")

    def test_chrome_channel_opens_a_fresh_visible_context(self):
        for channel in ("chromium", "chrome"):
            configuration = profile()
            configuration["browser_channel"] = channel
            adapter = browser.BrowserAdapter(configuration)
            context, launched, playwright = Mock(), Mock(), Mock()
            context.new_page.return_value = Mock()
            launched.new_context.return_value = context
            playwright.chromium.launch.return_value = launched
            entry = Mock()
            entry.start.return_value = playwright
            dependency = SimpleNamespace(sync_playwright=Mock(return_value=entry), TimeoutError=TimeoutError)
            with patch.dict("sys.modules", {"playwright.sync_api": dependency}):
                adapter.__enter__()
            options = {"headless": False}
            if channel == "chrome":
                options["channel"] = "chrome"
            playwright.chromium.launch.assert_called_once_with(**options)
            launched.new_context.assert_called_once_with(accept_downloads=False, service_workers="block")
            self.assertFalse(playwright.chromium.launch_persistent_context.called)
            adapter.close()
        configuration["browser_channel"] = "chrome-default-profile"
        self.assert_code("invalid_profile", browser.BrowserAdapter, configuration)

    def test_unavailable_chrome_is_explicit_and_does_not_fall_back(self):
        configuration = profile()
        configuration["browser_channel"] = "chrome"
        adapter = browser.BrowserAdapter(configuration)
        playwright = Mock()
        playwright.chromium.launch.side_effect = RuntimeError("PRIVATE FULL MANUSCRIPT")
        entry = Mock()
        entry.start.return_value = playwright
        dependency = SimpleNamespace(sync_playwright=Mock(return_value=entry), TimeoutError=TimeoutError)
        with patch.dict("sys.modules", {"playwright.sync_api": dependency}):
            error = self.assert_code("browser_unavailable", adapter.__enter__)
        self.assertIn("Google Chrome", str(error))
        playwright.chromium.launch.assert_called_once_with(headless=False, channel="chrome")

    def test_login_allowlist_only_applies_during_manual_verification(self):
        configuration = profile()
        configuration["login_origins"] = ["https://fixture-login.example"]
        adapter = self.opened(configuration, redirect="https://fixture-login.example/login#step", login_return_ticks=1)
        adapter.login_timeout_ms = 100
        original = adapter._try_value
        def only_read_work_identity(page, spec):
            self.assertTrue(page.url.startswith(configuration["origin"] + "/"))
            return original(page, spec)
        with patch.object(adapter, "_try_value", side_effect=only_read_work_identity):
            adapter.verify_target("qimao", "author-1", "book-1")
        self.assertFalse(adapter._login_active)
        self.assertIsNone(adapter._login_deadline)
        self.assertEqual(adapter._target, ("qimao", "author-1", "book-1"))
        self.assertFalse(any(event[0] in ("fill", "click") for event in adapter._context.events))
        # A later redirect back to even the configured SSO origin is refused.
        self.assert_code("unsafe_url", adapter.create_draft, 1, "标题", "正文")
        self.assertFalse(any(event[0] in ("fill", "click") for event in adapter._context.events))

    def test_login_timeout_or_wrong_return_identity_never_starts_writes(self):
        configuration = profile()
        configuration["login_origins"] = ["https://fixture-login.example"]
        adapter = self.opened(configuration, redirect="https://fixture-login.example/login")
        self.assert_code("login_required", adapter.verify_target, "qimao", "author-1", "book-1")
        self.assertFalse(adapter._login_active)
        self.assertIsNone(adapter._target)
        adapter = self.opened(configuration, redirect="https://fixture-login.example/login", login_return_ticks=1, account="someone-else")
        adapter.login_timeout_ms = 100
        self.assert_code("identity_mismatch", adapter.verify_target, "qimao", "author-1", "book-1")
        self.assertFalse(adapter._login_active)
        self.assertIsNone(adapter._target)
        self.assertFalse(any(event[0] in ("fill", "click") for event in adapter._context.events))

    def test_remaining_login_pages_close_and_identity_rechecks_after_lock(self):
        adapter = self.opened()
        popup = adapter._context.new_page()
        popup.url = "https://fixture-login.example/login"
        original = adapter._require_identity
        def after_lock(page):
            self.assertFalse(adapter._login_active)
            self.assertTrue(popup.closed)
            adapter._context.options["account"] = "changed-author"
            return original(page)
        with patch.object(adapter, "_require_identity", side_effect=after_lock):
            self.assert_code("identity_mismatch", adapter.verify_target, "qimao", "author-1", "book-1")
        self.assertIsNone(adapter._target)

    def test_login_origin_navigation_has_a_finite_deadline(self):
        configuration = profile()
        configuration["login_origins"] = ["https://fixture-login.example"]
        adapter = self.opened(configuration)
        class Request:
            url = "https://fixture-login.example/login"
            def is_navigation_request(self):
                return True
        class Route:
            request = Request()
            aborted, continued = False, False
            def abort(self):
                self.aborted = True
            def continue_(self):
                self.continued = True
        adapter._login_active, adapter._login_deadline = True, time.monotonic() + 1
        route = Route()
        adapter._route(route)
        self.assertTrue(route.continued)
        self.assertFalse(route.aborted)
        adapter._login_deadline = time.monotonic() - 1
        route = Route()
        adapter._route(route)
        self.assertTrue(route.aborted)
        self.assertFalse(route.continued)
        self.assert_code("unsafe_navigation", adapter.verify_target, "qimao", "author-1", "book-1")

    def test_login_origin_does_not_allow_neighbor_domains_or_later_account_switch(self):
        configuration = profile()
        configuration["login_origins"] = ["https://fixture-login.example"]
        for destination, during_login in (("https://fixture-login.example.evil.test/login", True),
                                          ("https://sub.fixture-login.example/login", True),
                                          ("https://fixture-login.example/login", False)):
            adapter = self.verified(configuration)
            adapter._login_active = during_login
            adapter._login_deadline = time.monotonic() + 1 if during_login else None
            request = SimpleNamespace(url=destination, is_navigation_request=lambda: True)
            route = Mock(request=request)
            adapter._route(route)
            route.abort.assert_called_once_with()
            route.continue_.assert_not_called()
            self.assert_code("unsafe_navigation", adapter.create_draft, 1, "标题", "正文")
            self.assertFalse(any(event[0] in ("fill", "click") for event in adapter._context.events))

    def test_progress_events_have_no_sensitive_values_and_callback_errors_are_isolated(self):
        events = []
        adapter = self.opened()
        adapter._progress_callback = events.append
        adapter.verify_target("qimao", "author-1", "book-1")
        adapter.find_drafts(1)
        adapter.create_draft(1, "PRIVATE FULL MANUSCRIPT", "PRIVATE FULL MANUSCRIPT")
        self.assertEqual(events, [{"stage": "waiting_login", "status": "running"},
                                  {"stage": "target_verified", "status": "completed"},
                                  {"stage": "listing_drafts", "status": "running"},
                                  {"stage": "saving_draft", "status": "running"},
                                  {"stage": "reading_back", "status": "running"}])
        def fail_callback(event):
            raise RuntimeError("PRIVATE FULL MANUSCRIPT")
        adapter = self.opened()
        adapter._progress_callback = fail_callback
        adapter.verify_target("qimao", "author-1", "book-1")
        self.assertEqual(adapter.create_draft(1, "标题", "正文"), "new-1")
        self.assertEqual(adapter.read_draft("new-1")["body"], "正文")
        self.assert_code("invalid_profile", browser.BrowserAdapter, profile(), progress_callback="invalid")

    def test_target_mismatch_and_manual_login_do_not_fill_anything(self):
        adapter = self.opened(account="someone-else")
        self.assert_code("identity_mismatch", adapter.verify_target, "qimao", "author-1", "book-1")
        self.assertFalse(any(event[0] == "fill" for event in adapter._context.events))
        adapter = self.opened(missing=["#account"])
        self.assert_code("login_required", adapter.verify_target, "qimao", "author-1", "book-1")
        self.assertFalse(any(event[0] == "fill" for event in adapter._context.events))

    def test_full_pagination_reopens_actual_draft_and_preserves_text(self):
        configuration = profile()
        stored = {"chapter": 2, "title": "  门后，还是你  ", "body": "  第一行。\r\n\n第二行！  ",
                  "state": "草稿", "remote_id": "draft_2"}
        adapter = self.verified(configuration, lists={configuration["chapter_lists"][0]["url"]:
                                [[("published-1", 1, "已发布")], [("draft_2", 2, "草稿")]]},
                                editors={"draft_2": stored})
        result = adapter.find_drafts(2)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], stored["title"])
        self.assertEqual(result[0]["body"], stored["body"])
        self.assertIn(("goto", configuration["urls"]["edit"].replace("{remote_id}", "draft_2")), adapter._context.events)
        self.assertNotIn(("goto", configuration["urls"]["create"]), adapter._context.events)
        self.assertFalse(any(event[0] == "fill" for event in adapter._context.events))

    def test_target_non_draft_in_later_page_blocks(self):
        configuration = profile()
        for state in ("已发布", "审核中", "定时发布", "状态未知"):
            adapter = self.verified(configuration, lists={configuration["chapter_lists"][0]["url"]:
                                    [[("other", 1, "草稿")], [("target", 2, state)]]})
            self.assert_code("chapter_exists_not_draft", adapter.find_drafts, 2)
            self.assertNotIn(("goto", configuration["urls"]["create"]), adapter._context.events)

    def test_split_drafts_and_non_drafts_are_both_exhausted(self):
        configuration = profile()
        first = configuration["chapter_lists"][0]
        first["scope"] = "drafts"
        second = copy.deepcopy(first)
        second.update(scope="non_drafts", url=configuration["origin"] + "/fixture/submitted")
        configuration["chapter_lists"].append(second)
        adapter = self.verified(configuration, lists={first["url"]: [[]], second["url"]: [[("target", 2, "审核中")]]})
        self.assert_code("chapter_exists_not_draft", adapter.find_drafts, 2)
        self.assertIn(("goto", second["url"]), adapter._context.events)

    def test_scope_marker_or_filter_mismatch_blocks(self):
        for options in ({"search": "only selected title"}, {"filter": "drafts"}, {"tab": "其他目录"}):
            adapter = self.verified(**options)
            self.assert_code("incomplete_scope", adapter.find_drafts, 1)

    def test_missing_empty_or_pagination_proof_blocks(self):
        adapter = self.verified(empty=False)
        self.assert_code("incomplete_list", adapter.find_drafts, 1)

    def test_initial_empty_placeholder_is_not_a_loaded_empty_list(self):
        adapter = self.verified(loading=True, loaded_pages=[[("already-published", 1, "已发布")]])
        self.assert_code("chapter_exists_not_draft", adapter.find_drafts, 1)
        self.assertFalse(adapter._context.options["loading"])
        adapter = self.verified(loading=True)
        self.assert_code("incomplete_list", adapter.find_drafts, 1)
        configuration = profile()
        adapter = self.verified(configuration, lists={configuration["chapter_lists"][0]["url"]: [[("other", 2, "草稿")]]},
                                end=False, missing_next=True)
        self.assert_code("incomplete_list", adapter.find_drafts, 1)

    def test_stuck_or_repeated_pagination_blocks(self):
        configuration = profile()
        pages = [[("other", 2, "草稿")], [("next", 3, "草稿")]]
        adapter = self.verified(configuration, lists={configuration["chapter_lists"][0]["url"]: pages}, stuck=True)
        self.assert_code("incomplete_list", adapter.find_drafts, 1)
        pages = [[("same", 2, "草稿")], [("same", 2, "草稿"), ("next", 3, "草稿")]]
        adapter = self.verified(configuration, lists={configuration["chapter_lists"][0]["url"]: pages})
        self.assert_code("incomplete_list", adapter.find_drafts, 1)

    def test_unknown_or_changed_editor_state_is_not_reported_as_draft(self):
        configuration = profile()
        stored = {"chapter": 1, "title": "标题", "body": "正文", "state": "已发布", "remote_id": "draft1"}
        adapter = self.verified(configuration, editors={"draft1": stored})
        self.assert_code("not_draft", adapter.read_draft, "draft1")
        stored["state"], stored["remote_id"] = "草稿", "other"
        adapter = self.verified(configuration, editors={"draft1": stored})
        self.assert_code("identity_mismatch", adapter.read_draft, "draft1")

    def test_same_target_reverification_preserves_observed_chapter_binding(self):
        configuration = profile()
        stored = {"chapter": 1, "title": "标题", "body": "正文", "state": "草稿", "remote_id": "draft1"}
        adapter = self.verified(configuration, lists={configuration["urls"]["drafts"]: [[("draft1", 1, "草稿")]]},
                                editors={"draft1": stored})
        self.assertEqual(len(adapter.find_drafts(1)), 1)
        adapter.verify_target("qimao", "author-1", "book-1")
        adapter._context.editors["draft1"]["chapter"] = 2
        self.assert_code("identity_mismatch", adapter.read_draft, "draft1")

    def test_creating_requires_prior_target_verification(self):
        adapter = self.opened()
        self.assert_code("target_unverified", adapter.create_draft, 1, "标题", "正文")
        self.assertEqual(adapter._context.events, [])

    def test_create_does_not_overwrite_existing_manuscript(self):
        for options in ({"initial_title": "已有章名"}, {"initial_body": "已有正文"}):
            adapter = self.verified(**options)
            self.assert_code("not_new_draft", adapter.create_draft, 1, "新章名", "新正文")
            self.assertFalse(any(event[0] == "fill" for event in adapter._context.events))

    def create_action_profile(self, opens="popup"):
        configuration = profile()
        configuration["urls"]["create"] = configuration["urls"]["drafts"]
        configuration["create_action"] = {"mode": "button", "selector": "#create",
                                            "expected_text": "新建草稿", "opens": opens}
        return configuration

    def test_create_action_profile_rejects_unsafe_labels_and_unknown_window_modes(self):
        for label in ("新建章节", "新建草稿并发布", "发布", "新建草稿下一步", "Create draft and submit"):
            configuration = self.create_action_profile()
            configuration["create_action"]["expected_text"] = label
            self.assert_code("unsafe_create", browser.BrowserAdapter, configuration)
        for field, value in (("mode", "navigate"), ("opens", "guess"), ("opens", None)):
            configuration = self.create_action_profile()
            configuration["create_action"][field] = value
            self.assert_code("invalid_profile", browser.BrowserAdapter, configuration)

    def test_create_action_is_never_clicked_by_verification_or_listing(self):
        adapter = self.verified(self.create_action_profile())
        self.assertEqual(adapter.find_drafts(1), [])
        adapter.verify_target("qimao", "author-1", "book-1")
        self.assertEqual(adapter._context.created_blank, 0)
        self.assertFalse(any(event[0] == "click" for event in adapter._context.events))

    def test_popup_and_same_page_actions_fill_the_created_editor_and_reopen_it(self):
        for opens in ("popup", "same_page"):
            adapter = self.verified(self.create_action_profile(opens))
            self.assertEqual(adapter.create_draft(11, "验收标题", "完整正文\n\n下一段"), "empty-1")
            self.assertEqual(adapter._context.created_blank, 1)
            observed = adapter.read_draft("empty-1")
            self.assertEqual(observed["title"], "验收标题")
            self.assertEqual(observed["body"], "完整正文\n\n下一段")
            self.assertEqual(sum(event == ("click", "#create") for event in adapter._context.events), 1)
            self.assertEqual(sum(event == ("click", "#save") for event in adapter._context.events), 1)
            self.assertTrue(all(page.closed for page in adapter._context.pages[1:]))

    def test_create_action_rechecks_label_and_identity_before_clicking(self):
        for options in ({"create_text": "发布"}, {"create_aria": "新建草稿后发布"}):
            adapter = self.verified(self.create_action_profile(), **options)
            self.assert_code("unsafe_create", adapter.create_draft, 1, "标题", "正文")
            self.assertEqual(adapter._context.created_blank, 0)
        adapter = self.verified(self.create_action_profile())
        adapter._context.options["account"] = "another-account"
        self.assert_code("identity_mismatch", adapter.create_draft, 1, "标题", "正文")
        self.assertEqual(adapter._context.created_blank, 0)

    def test_lost_popup_after_empty_draft_allocation_does_not_retry_or_fill(self):
        adapter = self.verified(self.create_action_profile(), lose_popup=True)
        self.assert_code("operation_failed", adapter.create_draft, 11, "标题", "正文")
        self.assertEqual(adapter._context.created_blank, 1)
        self.assertEqual(adapter._context.editors["empty-1"]["body"], "")
        self.assertEqual(sum(event == ("click", "#create") for event in adapter._context.events), 1)
        self.assertFalse(any(event[0] == "fill" for event in adapter._context.events))
        self.assertTrue(all(page.closed for page in adapter._context.pages[1:]))

    def test_created_popup_cannot_change_origin_or_account_before_filling(self):
        for options, code in (({"creation_redirect": "https://elsewhere.example/editor"}, "unsafe_url"),
                              ({"creation_account": "another-account"}, "identity_mismatch")):
            adapter = self.verified(self.create_action_profile(), **options)
            self.assert_code(code, adapter.create_draft, 11, "标题", "正文")
            self.assertEqual(adapter._context.created_blank, 1)
            self.assertFalse(any(event[0] == "fill" for event in adapter._context.events))
            self.assertTrue(all(page.closed for page in adapter._context.pages[1:]))

    def test_an_initial_saved_badge_cannot_hide_changed_or_missing_cloud_content(self):
        adapter = self.verified(self.create_action_profile(), persist_different_body=True)
        self.assert_code("save_unverified", adapter.create_draft, 11, "标题", "新正文")
        self.assertEqual(adapter._context.created_blank, 1)
        self.assertEqual(adapter._context.editors["empty-1"]["body"], "云端旧正文")
        adapter = self.verified(self.create_action_profile(), no_save=True)
        self.assert_code("invalid_chapter", adapter.create_draft, 11, "标题", "新正文")
        self.assertEqual(adapter._context.editors["empty-1"]["body"], "")

    def test_save_button_rechecks_actual_label_and_accessible_name(self):
        for options in ({"button_text": "发布"}, {"button_aria": "保存草稿并发布"}):
            adapter = self.verified(**options)
            self.assert_code("unsafe_save", adapter.create_draft, 1, "标题", "正文")
            self.assertFalse(any(event[0] == "click" for event in adapter._context.events))

    def test_changes_during_filling_prevent_explicit_save(self):
        adapter = self.verified(change_state_on_fill=True)
        self.assert_code("not_draft", adapter.create_draft, 1, "标题", "正文")
        self.assertFalse(any(event[0] == "click" for event in adapter._context.events))
        adapter = self.verified(change_title_on_body=True)
        self.assert_code("editor_changed", adapter.create_draft, 1, "标题", "正文")

    def test_button_save_reopens_fresh_page_and_reads_complete_values(self):
        adapter = self.verified()
        remote_id = adapter.create_draft(4, "  标题，四！  ", "首段。\n\n  次段，留空格。  ")
        self.assertEqual(remote_id, "new-1")
        reread = adapter.read_draft(remote_id)
        self.assertEqual(reread["title"], "  标题，四！  ")
        self.assertEqual(reread["body"], "首段。\n\n  次段，留空格。  ")
        self.assertEqual(sum(event[0] == "goto" and "/edit/" in event[1] for event in adapter._context.events), 2)
        self.assertTrue(all(page.closed for page in adapter._context.pages[1:]))

    def test_contenteditable_and_autosave_need_no_save_click(self):
        configuration = profile()
        configuration["editor"]["save"] = {"mode": "autosave"}
        adapter = self.verified(configuration, contenteditable=True, autosave=True)
        self.assertEqual(adapter.create_draft(1, "标题", "  正文。\n\n尾行  "), "new-1")
        self.assertEqual(adapter.read_draft("new-1")["body"], "  正文。\n\n尾行  ")
        self.assertFalse(any(event[0] == "click" for event in adapter._context.events))

    def test_no_saved_evidence_does_not_claim_success(self):
        adapter = self.verified(no_save=True)
        self.assert_code("save_unverified", adapter.create_draft, 1, "标题", "正文")

    def test_spa_identity_and_editor_fields_are_waited_for(self):
        adapter = self.verified()
        adapter._context.options["delayed"] = ["#account", "#book", "#state", "#title", "#body"]
        self.assertEqual(adapter.create_draft(1, "标题", "正文"), "new-1")
        for selector in ("#account", "#book", "#state", "#title", "#body"):
            self.assertIn(("wait", selector), adapter._context.events)

    def test_attached_identity_skeleton_waits_for_actual_attribute(self):
        adapter = self.verified(identity_ticks=1)
        adapter._context.options["identity_ticks"] = 1
        self.assertEqual(adapter.create_draft(1, "标题", "正文"), "new-1")
        self.assertEqual(adapter._context.options["identity_ticks"], 0)

    def test_remote_ids_cannot_be_titles_urls_or_credentials(self):
        adapter = self.verified()
        for remote_id in ("", "这只是章名", "https://user:secret@example.com/x", "draft/1", "a?token=secret", " a "):
            self.assert_code("invalid_remote_id", adapter.read_draft, remote_id)

    def test_cross_origin_navigation_is_blocked_and_latched(self):
        adapter = self.verified()
        class Request:
            url = "https://elsewhere.example/login?secret=PRIVATE"
            def is_navigation_request(self):
                return True
        class Route:
            request = Request()
            aborted = False
            def abort(self):
                self.aborted = True
            def continue_(self):
                raise AssertionError("must not continue")
        route = Route()
        adapter._route(route)
        self.assertTrue(route.aborted)
        self.assert_code("unsafe_navigation", adapter.create_draft, 1, "标题", "正文")

    def test_errors_do_not_expose_playwright_text_or_full_manuscript(self):
        adapter = self.verified()
        with patch.object(adapter._context, "new_page", side_effect=RuntimeError("PRIVATE FULL MANUSCRIPT")):
            error = self.assert_code("operation_failed", adapter.read_draft, "draft1")
        self.assertEqual(error.details, {})


if __name__ == "__main__":
    unittest.main()

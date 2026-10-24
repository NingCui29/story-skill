"""Strict read-only Fanqie preview contracts. No browser/platform access."""
import copy
import json
import subprocess
import unittest
from unittest.mock import patch

from tests import test_platform_browser as fixtures
from tests import test_platform_browser_fanqie_body as dom


browser = fixtures.browser


def configuration():
    config = fixtures.BrowserTests().url_guard_profile("fanqie")
    config["editor"]["ready"] = fixtures.field("#save", expected="保存草稿", enabled=True)
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
    def evaluate_all(self, expression, attribute=None):
        if "elements[0] === expected" in expression:
            return super().evaluate_all(expression, attribute)
        # Unlike locator.evaluate/text_content, evaluate_all reads current
        # matches immediately and cannot wait for a missing node to reappear.
        nodes = self.lookup()
        if len(nodes) != 1:
            return {"count": len(nodes)}
        node = nodes[0]
        value = node.attrs.get(attribute) if attribute is not None else (
            node.value if node.tag in ("INPUT", "TEXTAREA", "SELECT") else node.text)
        return {"count": 1, "value": value}

    def evaluate(self, expression, *arguments):
        if self.selector == ".preview-content > div":
            self.page.context.preview_reads += 1
            pending = self.page.context.options.get("preview_pending_reads", 0)
            if pending:
                self.page.context.options["preview_pending_reads"] = pending - 1
                return {"ok": False, "pending": True}
            if self.page.context.options.get("bad_preview_dom"):
                return {"ok": False}
            value = self.page.context.options.get("preview_body", self.page.nodes[self.selector][0].text)
            if self.page.context.options.get("preview_changes") and self.page.context.preview_reads > 1:
                value += "正文变化"
            return {"ok": True, "value": value}
        return super().evaluate(expression, *arguments)


class PreviewPage(fixtures.Page):
    def evaluate(self, expression, *arguments):
        packet = super().evaluate(expression, *arguments)
        if "editorReadbackSnapshot" in expression and "body" in packet["fields"]:
            body = packet["fields"]["body"]
            if body.get("count") == 1 and body.get("connected"):
                if self.context.options.get("preview_pending_reads"):
                    body.update(ok=False, pending=True)
                elif self.context.options.get("bad_preview_dom"):
                    body["ok"] = False
                else:
                    body["ok"] = True
                    body["value"] = self.context.options.get("preview_body", body["value"])
        return packet

    def wait_for_timeout(self, timeout):
        super().wait_for_timeout(timeout)
        if getattr(self, "kind", None) == "preview":
            self.context.preview_waits += 1
            changed = self.context.options.get("preview_change_while_pending")
            if changed == "remote":
                self.url = self.url.rsplit("/", 1)[0] + "/901"
            elif changed == "header":
                self.nodes[".preview-title"][0].text = "第12章 已改变标题"
            elif changed == "account":
                self.context.options["account"] = "other-author"

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
        self.preview_waits = 0

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

    def opened(self, comparison_policy=None, profile=None, **options):
        config = configuration() if profile is None else copy.deepcopy(profile)
        stored = {"chapter": 12, "title": "完整标题", "body": "\n中文甲\n\n中文乙", "state": "草稿", "remote_id": "900"}
        lists = {config["chapter_lists"][0]["url"]: [[("900", 12, "草稿")]],
                 config["chapter_lists"][1]["url"]: [[]]}
        arguments = {} if comparison_policy is None else {"comparison_policy": comparison_policy}
        adapter = browser.BrowserAdapter(config, timeout_ms=10, login_timeout_ms=100, **arguments)
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

    def test_fanqie_missing_preview_is_rejected_before_browser_entry(self):
        config = configuration()
        del config["readback"]
        with patch.object(browser.BrowserAdapter, "__enter__") as enter:
            self.assert_code("readback_required", browser.BrowserAdapter, config)
        enter.assert_not_called()

    def test_saved_preview_uses_explicit_terminal_lf_policy_only(self):
        adapter = self.opened(comparison_policy="fanqie_terminal_lf_v1", sync_drafts=True,
                              preview_body="中文甲\n\n中文乙")
        self.assertEqual(adapter.create_draft(13, "新完整标题", "中文甲\n\n中文乙\n"), "new-1")
        self.assertEqual(adapter._context.editors["new-1"]["body"], "中文甲\n\n中文乙\n")
        for policy, observed, header in (
            ("strict", "中文甲\n\n中文乙", "第13章 新完整标题"),
            ("fanqie_terminal_lf_v1", "中文甲\n中文乙", "第13章 新完整标题"),
            ("fanqie_terminal_lf_v1", "中文甲\n\n中文乙 ", "第13章 新完整标题"),
            ("fanqie_terminal_lf_v1", "中文甲\n\n中文乙", "第13章 其他标题"),
        ):
            with self.subTest(policy=policy, observed=observed, header=header):
                adapter = self.opened(comparison_policy=policy, sync_drafts=True,
                                      preview_body=observed, preview_header=header)
                self.assert_code("save_unverified", adapter.create_draft, 13, "新完整标题", "中文甲\n\n中文乙\n")

    def test_terminal_lf_policy_does_not_relax_editor_input_verification(self):
        adapter = self.opened(comparison_policy="fanqie_terminal_lf_v1", sync_drafts=True)
        original = fixtures.Locator.fill
        def lose_terminal_lf(locator, value):
            return original(locator, value.rstrip("\n") if locator.selector == "#body" else value)
        with patch.object(fixtures.Locator, "fill", lose_terminal_lf):
            self.assert_code("editor_changed", adapter.create_draft, 13, "新完整标题", "中文正文\n")
        self.assertNotIn("new-1", adapter._context.editors)

    def test_comparison_policy_rejects_unknown_and_non_fanqie_values(self):
        for value in (None, True, "trim", []):
            with self.subTest(value=value):
                self.assert_code("invalid_comparison_policy", browser.BrowserAdapter,
                                 configuration(), comparison_policy=value)
        for platform in ("qimao", "qidian"):
            self.assert_code("invalid_comparison_policy", browser.BrowserAdapter,
                             fixtures.profile(platform), comparison_policy="fanqie_terminal_lf_v1")

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

    def test_empty_preview_container_waits_for_complete_body_before_stable_recheck(self):
        adapter = self.opened(preview_pending_reads=2)
        with patch.object(browser.time, "monotonic", return_value=0):
            observed = adapter.read_draft("900")
        self.assertEqual(observed["body"], "\n中文甲\n\n中文乙")
        self.assertEqual(adapter._context.preview_waits, 2)
        self.assertEqual(adapter._context.preview_reads, 4)
        self.assertFalse(any(event[0] in ("fill", "click") for event in adapter._context.events))

    def test_permanent_empty_preview_container_times_out_with_no_second_create(self):
        adapter = self.opened(sync_drafts=True, preview_pending_reads=100)
        with patch.object(browser.time, "monotonic", side_effect=lambda: adapter._context.preview_waits / 100):
            self.assert_code("incomplete_dom", adapter.create_draft, 13, "新完整标题", "新正文")
        self.assertEqual(adapter._context.preview_waits, 1)
        self.assertEqual(adapter._context.editors["new-1"]["body"], "新正文")
        self.assertEqual(sum(event == ("click", "#save") for event in adapter._context.events), 1)

    def test_loaded_body_cannot_reset_or_exceed_the_original_readiness_deadline(self):
        adapter = self.opened(preview_pending_reads=1)
        with patch.object(browser.time, "monotonic", side_effect=lambda: adapter._context.preview_waits / 100):
            self.assert_code("incomplete_dom", adapter.read_draft, "900")
        self.assertEqual(adapter._context.preview_waits, 1)

    def test_pending_preview_rechecks_identity_and_title_before_accepting_loaded_body(self):
        for change, code in (("remote", "identity_mismatch"), ("header", "editor_changed"),
                             ("account", "identity_mismatch")):
            with self.subTest(change=change):
                adapter = self.opened(preview_pending_reads=1, preview_change_while_pending=change)
                with patch.object(browser.time, "monotonic", return_value=0):
                    self.assert_code(code, adapter.read_draft, "900")
                self.assertFalse(any(event[0] in ("fill", "click") for event in adapter._context.events))

    def test_pending_only_applies_before_first_complete_body_and_unknown_dom_fails_immediately(self):
        adapter = self.opened(preview_pending_reads=1, bad_preview_dom=True)
        with patch.object(browser.time, "monotonic", return_value=0):
            self.assert_code("unsupported_editor", adapter.read_draft, "900")
        self.assertEqual(adapter._context.preview_waits, 1)
        adapter = self.opened()
        original = adapter._confirm_listed_draft
        def confirm(remote_id, chapter=None):
            result = original(remote_id, chapter)
            if chapter is not None:
                adapter._context.options["preview_pending_reads"] = 1
            return result
        with patch.object(adapter, "_confirm_listed_draft", side_effect=confirm):
            self.assert_code("unsupported_editor", adapter.read_draft, "900")
        self.assertEqual(adapter._context.preview_waits, 0)

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

    def test_final_preview_rechecks_identity_after_last_body_read_without_waiting(self):
        cases = (("remote", "identity_mismatch"), ("book", "identity_mismatch"),
                 ("account", "identity_mismatch"), ("title", "editor_changed"),
                 ("missing_account", "incomplete_dom"), ("empty_account", "identity_unavailable"),
                 ("duplicate_account", "ambiguous_dom"), ("guard_redirect", "identity_mismatch"),
                 ("missing_header", "incomplete_dom"), ("duplicate_header", "ambiguous_dom"))
        original_evaluate, original_current = PreviewLocator.evaluate, PreviewLocator.evaluate_all
        for operation in ("read", "create"):
            for change, code in cases:
                with self.subTest(operation=operation, change=change):
                    adapter = self.opened(sync_drafts=True)
                    context, after_body_events = adapter._context, []
                    def evaluate(locator, expression, *arguments):
                        result = original_evaluate(locator, expression, *arguments)
                        if locator.selector != ".preview-content > div" or context.preview_reads != 2:
                            return result
                        page = locator.page
                        if change in ("remote", "book"):
                            remote = "901" if change == "remote" else page.url.rsplit("/", 1)[-1]
                            context.editors[remote] = dict(context.editors[page.url.rsplit("/", 1)[-1]])
                            page.goto(configuration()["origin"] + "/fixture/" + ("200" if change == "book" else "100") + "/preview/" + remote)
                        elif change == "account":
                            context.options["guard_account"] = "other-author"
                        elif change == "empty_account":
                            context.options["guard_account"] = ""
                        elif change == "missing_account":
                            adapter._guard_page.delayed.add("#guard-id")
                        elif change == "guard_redirect":
                            adapter._guard_page.url = configuration()["origin"] + "/fixture/login"
                        elif change == "title":
                            page.nodes[".preview-title"][0].text = "第12章 已改变标题"
                        elif change == "missing_header":
                            page.delayed.add(".preview-title")
                        after_body_events.append(len(context.events))
                        return result
                    def current(locator, expression, *arguments):
                        if context.preview_reads == 2 and ((change == "duplicate_account" and locator.selector == "#guard-id") or
                                (change == "duplicate_header" and locator.selector == ".preview-title")):
                            return {"count": 2}
                        return original_current(locator, expression, *arguments)
                    with patch.object(PreviewLocator, "evaluate", evaluate), patch.object(PreviewLocator, "evaluate_all", current):
                        if operation == "create":
                            self.assert_code(code, adapter.create_draft, 13, "新完整标题", "新正文")
                        else:
                            self.assert_code(code, adapter.read_draft, "900")
                    self.assertEqual(context.preview_reads, 2)
                    self.assertEqual(len(after_body_events), 1)
                    self.assertFalse(any(event[0] in ("wait", "goto") for event in context.events[after_body_events[0]:]),
                                     "Final checks cannot wait for or reload an identity back into existence")
                    self.assertTrue(all(page.closed for page in context.pages if getattr(page, "kind", None) == "preview"))
                    self.assertEqual(sum(event == ("click", "#save") for event in context.events), 1 if operation == "create" else 0)
                    self.assertEqual(sum(event == ("goto", adapter.urls["create"]) for event in context.events),
                                     1 if operation == "create" else 0)

    def test_final_preview_checks_url_again_after_current_account_dom_read(self):
        adapter = self.opened()
        context = adapter._context
        original = PreviewLocator.evaluate_all
        changed = []
        def current(locator, expression, *arguments):
            result = original(locator, expression, *arguments)
            if locator.selector == "#guard-id" and context.preview_reads == 2:
                page = next(page for page in context.pages if getattr(page, "kind", None) == "preview" and not page.closed)
                page.goto(configuration()["origin"] + "/fixture/200/preview/900")
                changed.append(page)
            return result
        with patch.object(PreviewLocator, "evaluate_all", current):
            self.assert_code("identity_mismatch", adapter.read_draft, "900")
        self.assertEqual(len(changed), 1)
        self.assertTrue(changed[0].closed)
        self.assertEqual(context.created_blank, 0)

    def test_final_preview_body_is_current_after_account_check_without_waiting(self):
        cases = (("changed", "editor_changed"), ("missing", "incomplete_dom"),
                 ("duplicate", "ambiguous_dom"), ("pending", "unsupported_editor"),
                 ("unsupported", "unsupported_editor"))
        for guard in (True, False):
            for operation in ("read", "create"):
                for fault, code in cases:
                    with self.subTest(guard=guard, operation=operation, fault=fault):
                        config = configuration()
                        if not guard:
                            config["identity"]["account"] = fixtures.field("#account", "data-id")
                        adapter = self.opened(profile=config, sync_drafts=True)
                        context, changed = adapter._context, []
                        original = adapter._preview_current_value
                        def current(page, spec):
                            value = original(page, spec)
                            if (spec is adapter.profile["identity"]["account"] and context.preview_reads >= 2
                                    and not changed):
                                preview = next(p for p in context.pages if getattr(p, "kind", None) == "preview" and not p.closed)
                                selector = ".preview-content > div"
                                if fault == "changed":
                                    preview.nodes[selector][0].text = "核对账号时正文被更新"
                                elif fault == "missing":
                                    preview.delayed.add(selector)
                                elif fault == "duplicate":
                                    preview.nodes[selector].append(fixtures.Node("另一份正文"))
                                elif fault == "pending":
                                    context.options["preview_pending_reads"] = 1
                                else:
                                    context.options["bad_preview_dom"] = True
                                changed.append(len(context.events))
                            return value
                        with patch.object(adapter, "_preview_current_value", current):
                            if operation == "create":
                                self.assert_code(code, adapter.create_draft, 13, "新完整标题", "新正文")
                            else:
                                self.assert_code(code, adapter.read_draft, "900")
                        self.assertEqual(len(changed), 1)
                        self.assertFalse(any(event[0] in ("wait", "goto") for event in context.events[changed[0]:]))
                        self.assertEqual(context.events.count(("click", "#save")), int(operation == "create"))
                        self.assertTrue(all(p.closed for p in context.pages if getattr(p, "kind", None) == "preview"))

    def test_final_preview_current_account_also_supports_page_dom_identity(self):
        config = configuration()
        config["identity"]["account"] = fixtures.field("#account", "data-id")
        adapter = self.opened(profile=config)
        self.assertEqual(adapter.read_draft("900")["body"], "\n中文甲\n\n中文乙")
        self.assertIsNone(adapter._guard_page)
        original = PreviewLocator.evaluate
        def evaluate(locator, expression, *arguments):
            result = original(locator, expression, *arguments)
            if locator.selector == ".preview-content > div" and adapter._context.preview_reads == 4:
                adapter._context.options["account"] = "other-author"
            return result
        with patch.object(PreviewLocator, "evaluate", evaluate):
            self.assert_code("identity_mismatch", adapter.read_draft, "900")
        self.assertEqual(adapter._context.created_blank, 0)

    def test_final_preview_current_dom_failure_is_not_retried(self):
        original = PreviewLocator.evaluate_all
        for selector in ("#guard-id", ".preview-title"):
            with self.subTest(selector=selector):
                adapter = self.opened()
                context, calls = adapter._context, []
                def current(locator, expression, *arguments):
                    if locator.selector == selector and context.preview_reads == 2:
                        calls.append(selector)
                        if len(calls) == 1:
                            raise RuntimeError("fixture current DOM read timed out")
                    return original(locator, expression, *arguments)
                with patch.object(PreviewLocator, "evaluate_all", current):
                    self.assert_code("operation_failed", adapter.read_draft, "900")
                self.assertEqual(calls, [selector])
                self.assertTrue(all(page.closed for page in context.pages if getattr(page, "kind", None) == "preview"))
                self.assertEqual(context.created_blank, 0)

    def test_failed_final_preview_of_saved_draft_recovers_by_readback_without_new_write(self):
        adapter = self.opened(sync_drafts=True)
        context = adapter._context
        original = PreviewLocator.evaluate
        def evaluate(locator, expression, *arguments):
            result = original(locator, expression, *arguments)
            if locator.selector == ".preview-content > div" and context.preview_reads == 2:
                locator.page.goto(configuration()["origin"] + "/fixture/200/preview/new-1")
            return result
        with patch.object(PreviewLocator, "evaluate", evaluate):
            self.assert_code("identity_mismatch", adapter.create_draft, 13, "新完整标题", "新正文")
        observed = adapter.find_drafts(13)
        self.assertEqual([(item["remote_id"], item["body"]) for item in observed], [("new-1", "新正文")])
        self.assertEqual(sum(event == ("click", "#save") for event in context.events), 1)
        self.assertEqual(sum(event == ("goto", adapter.urls["create"]) for event in context.events), 1)


@unittest.skipUnless(dom.NODE, "Node.js required to execute the actual preview DOM reader")
class PreviewParagraphTests(unittest.TestCase):
    def setUp(self):
        self.adapter = browser.BrowserAdapter(configuration())

    def read(self, html):
        return self.adapter._preview_paragraph_value(dom.ParagraphLocator(html))

    def test_final_current_value_runs_atomic_dom_reader_without_locator_waits(self):
        class CurrentPage:
            def __init__(self, nodes):
                self.nodes, self.calls = nodes, 0

            def locator(self, selector):
                return self

            def evaluate_all(self, expression, attribute):
                self.calls += 1
                program = "const nodes=" + json.dumps(self.nodes, ensure_ascii=False) + ";\n" + (
                    "for(const el of nodes)el.getAttribute=name=>el.attrs?.[name]??null;\n"
                    "const read=(" + expression + ");\nprocess.stdout.write(JSON.stringify(read(nodes," +
                    json.dumps(attribute) + ")));"
                )
                result = subprocess.run([dom.NODE, "-e", program], capture_output=True, text=True, timeout=5)
                if result.returncode:
                    raise AssertionError(result.stderr)
                return json.loads(result.stdout)

        for node, attribute, expected in (
            ({"tagName": "DIV", "textContent": "正文 中文"}, None, "正文 中文"),
            ({"tagName": "INPUT", "value": "账号"}, None, "账号"),
            ({"tagName": "DIV", "isContentEditable": True, "innerText": "甲\n乙"}, None, "甲\n乙"),
            ({"tagName": "DIV", "attrs": {"data-id": "author-1"}}, "data-id", "author-1"),
        ):
            with self.subTest(node=node, attribute=attribute):
                page = CurrentPage([node])
                self.assertEqual(self.adapter._preview_current_value(page, {"selector": "#field", "attribute": attribute}), expected)
                self.assertEqual(page.calls, 1)
        for nodes, code in (([], "incomplete_dom"), ([{}, {}], "ambiguous_dom"),
                            ([{"tagName": "DIV", "textContent": None}], "incomplete_dom")):
            with self.subTest(nodes=nodes):
                page = CurrentPage(nodes)
                with self.assertRaises(browser.BrowserAdapterError) as caught:
                    self.adapter._preview_current_value(page, {"selector": "#field"})
                self.assertEqual(caught.exception.code, "platform_browser_" + code)
                self.assertEqual(page.calls, 1)

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

    def test_only_zero_child_nodes_are_a_loading_state_when_explicitly_requested(self):
        self.assertIsNone(self.adapter._preview_paragraph_value(dom.ParagraphLocator(""), allow_pending=True))
        self.assertEqual(self.adapter._preview_paragraph_value(dom.ParagraphLocator("<p></p>"), allow_pending=True), "")
        for html in (" ", "<p>尚未完整</p>", "<p class=\"loading\"></p>", "<div></div>"):
            with self.subTest(html=html), self.assertRaises(browser.BrowserAdapterError):
                self.adapter._preview_paragraph_value(dom.ParagraphLocator(html), allow_pending=True)


if __name__ == "__main__":unittest.main()

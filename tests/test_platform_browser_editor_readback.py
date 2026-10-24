"""Ordinary editor readback must bind the returned text to its actual draft."""
import json
import shutil
import subprocess
from unittest.mock import patch
import unittest

try:
    from . import test_platform_browser as fixture
    from . import test_platform_drafts as ledger
except ImportError:
    import test_platform_browser as fixture
    import test_platform_drafts as ledger

browser = fixture.browser


class EditorReadbackTests(unittest.TestCase):
    def setUp(self):
        self.old_api = browser.api
        browser.api = None

    def tearDown(self):
        browser.api = self.old_api

    def opened(self, platform="qimao", *, listing=False, guard=False, **options):
        helper = fixture.BrowserTests()
        config = (helper.listing_evidence_profile() if listing else
                  helper.url_guard_profile(platform) if guard else fixture.profile(platform))
        if listing and platform != "qimao":
            old, new = config["origin"], fixture.profile(platform)["origin"]
            import json
            config = json.loads(json.dumps(config).replace(old, new))
            config["platform"] = platform
        if listing:
            config["urls"]["create"] = config["urls"]["drafts"]
            config["create_action"] = {"mode": "button", "selector": "#create", "expected_text": "新建草稿", "opens": "popup"}
        lists = {item["url"]: [[("900", 1, "草稿")]] if item["scope"] != "non_drafts" else [[]]
                 for item in config["chapter_lists"]}
        data = {"chapter": 1, "title": "原稿标题", "body": "原稿正文\n\n", "state": "草稿", "remote_id": "900"}
        other = {**data, "chapter": 99, "title": "另一稿", "body": "另一稿正文", "remote_id": "901"}
        adapter = helper.opened(config, lists=lists, editors={"900": data, "901": other}, **options)
        adapter.verify_target(platform, "author-1", "100" if guard else "book-1")
        return adapter

    def after_body(self, mutation, *, after_save=False):
        original = fixture.Locator.input_value
        changed = []
        def read(locator):
            value = original(locator)
            if (locator.selector == "#body" and locator.page.kind == "edit" and not changed and
                    (not after_save or ("click", "#save") in locator.page.context.events)):
                changed.append(locator.page)
                mutation(locator.page)
            return value
        return patch.object(fixture.Locator, "input_value", read), changed

    def rejected(self, operation):
        with self.assertRaises(browser.BrowserAdapterError) as caught:
            operation()
        self.assertNotIn("PRIVATE FULL MANUSCRIPT", str(caught.exception))
        return caught.exception

    def test_navigation_during_body_is_rejected_by_read_find_and_create(self):
        for platform in ("qimao", "qidian"):
            for listing in (False, True):
                for operation in ("read", "find", "create"):
                    with self.subTest(platform=platform, listing=listing, operation=operation):
                        adapter = self.opened(platform, listing=listing, sync_drafts=listing)
                        context = adapter._context
                        race, changed = self.after_body(lambda page: page.goto(
                            adapter.urls["edit"].replace("{remote_id}", "901")), after_save=operation == "create")
                        call = {"read": lambda: adapter.read_draft("900"),
                                "find": lambda: adapter.find_drafts(1),
                                "create": lambda: adapter.create_draft(2, "新标题", "新正文")}[operation]
                        with race:
                            self.rejected(call)
                        self.assertEqual(len(changed), 1)
                        self.assertTrue(changed[0].closed)
                        self.assertEqual(context.events.count(("click", "#save")), int(operation == "create"))
                        if operation == "create":
                            self.assertEqual(context.editors["empty-1" if listing else "new-1"]["body"], "新正文")

    def test_current_dom_identity_and_content_changes_are_rejected(self):
        mutations = {
            "remote_id": lambda p: p.nodes["#remote-id"][0].attrs.update({"data-id": "901"}),
            "chapter": lambda p: setattr(p.nodes["#chapter"][0], "value", "99"),
            "title": lambda p: setattr(p.nodes["#title"][0], "value", "换了标题"),
            "body": lambda p: setattr(p.nodes["#body"][0], "value", "换了正文"),
            "state": lambda p: setattr(p.nodes["#state"][0], "text", "已发布"),
            "account": lambda p: p.context.options.update(account="another-author"),
            "book": lambda p: p.context.options.update(book="another-book"),
        }
        for platform in ("qimao", "qidian"):
            for name, mutation in mutations.items():
                with self.subTest(platform=platform, field=name):
                    adapter = self.opened(platform)
                    race, changed = self.after_body(mutation)
                    with race:
                        self.rejected(lambda: adapter.read_draft("900"))
                    self.assertTrue(changed[0].closed)

    def test_final_missing_empty_duplicate_fields_do_not_wait_to_recover(self):
        for selector in ("#account", "#book", "#remote-id", "#chapter", "#title", "#body", "#state"):
            for fault in ("missing", "duplicate", "empty"):
                with self.subTest(selector=selector, fault=fault):
                    adapter = self.opened()
                    baseline = []
                    def mutate(page):
                        baseline.append(len(page.context.events))
                        if fault == "missing":
                            page.delayed.add(selector)
                        elif fault == "duplicate":
                            old_lookup = page._lookup
                            page._lookup = lambda value: old_lookup(value) * (2 if value == selector else 1)
                        elif selector in ("#account", "#book"):
                            page.context.options[selector[1:]] = ""
                        elif selector == "#remote-id":
                            page.nodes[selector][0].attrs["data-id"] = ""
                        else:
                            page.nodes[selector][0].value = ""
                            page.nodes[selector][0].text = ""
                    race, changed = self.after_body(mutate)
                    with race:
                        self.rejected(lambda: adapter.read_draft("900"))
                    self.assertFalse(any(event[0] in ("wait", "goto") for event in adapter._context.events[baseline[0]:]))
                    self.assertTrue(changed[0].closed)

    def test_guard_and_url_identity_are_checked_without_refresh_after_body(self):
        for platform in ("qimao", "qidian"):
            for fault in ("account", "missing", "closed", "url", "book", "draft"):
                with self.subTest(platform=platform, fault=fault):
                    adapter = self.opened(platform, guard=True)
                    before = []
                    def mutate(page):
                        before.append(adapter._context.guard_visits)
                        if fault == "account":
                            page.context.options["guard_account"] = "other-author"
                        elif fault == "missing":
                            page.context.options["guard_missing"] = True
                        elif fault == "closed":
                            adapter._guard_page.close()
                        elif fault == "url":
                            adapter._guard_page.url += "?changed=1"
                        elif fault == "book":
                            page.url = page.url.replace("/100/", "/101/")
                        else:
                            page.url = page.url.replace("/900?", "/901?")
                    race, _ = self.after_body(mutate)
                    with race:
                        self.rejected(lambda: adapter.read_draft("900"))
                    self.assertEqual(adapter._context.guard_visits, before[0])

    def test_listing_refresh_cannot_hide_a_changed_editor(self):
        for fault in ("draft", "chapter", "title", "body", "account"):
            with self.subTest(fault=fault):
                adapter = self.opened(listing=True)
                original = adapter._confirm_listed_draft
                count = []
                def confirm(*args, **kwargs):
                    original(*args, **kwargs)
                    count.append(True)
                    if len(count) == 2:
                        page = next(page for page in adapter._context.pages if page.kind == "edit" and not page.closed)
                        if fault == "draft":
                            page.nodes["#remote-id"][0].attrs["data-id"] = "901"
                        elif fault == "account":
                            page.context.options["account"] = "other-author"
                        else:
                            page.nodes["#" + fault][0].value = "99" if fault == "chapter" else "改动"
                with patch.object(adapter, "_confirm_listed_draft", confirm):
                    self.rejected(lambda: adapter.read_draft("900"))
                self.assertEqual(len(count), 2)

    def test_unchanged_loaded_editor_round_trips_without_normalizing_text(self):
        for platform in ("qimao", "qidian"):
            for listing, guard, editable in ((False, False, False), (True, False, False), (False, True, False), (False, False, True)):
                with self.subTest(platform=platform, listing=listing, guard=guard, editable=editable):
                    adapter = self.opened(platform, listing=listing, guard=guard, contenteditable=editable)
                    adapter._context.options["delayed"] = ["#account", "#book", "#state", "#title", "#body"]
                    result = adapter.read_draft("900")
                    self.assertEqual(result, {"remote_id": "900", "title": "原稿标题", "body": "原稿正文\n\n", "state": "draft",
                                              "url": adapter.urls["edit"].replace("{remote_id}", "900")})

    def test_configured_same_draft_redirect_is_pinned_at_arrival(self):
        for platform in ("qimao", "qidian"):
            for late_navigation in (False, True):
                with self.subTest(platform=platform, late_navigation=late_navigation):
                    adapter = self.opened(platform, guard=True)
                    origin = adapter.profile["origin"]
                    canonical = origin + "/fixture/{book_id}/canonical/{remote_id}"
                    for spec in (adapter.profile["identity"]["book"], adapter.profile["editor"]["remote_id"]):
                        spec["templates"].append(canonical)
                    adapter._validate_profile()
                    requested = adapter.urls["edit"].replace("{remote_id}", "900")
                    arrived = canonical.replace("{book_id}", "100").replace("{remote_id}", "900")
                    original_goto = fixture.Page.goto
                    def goto(page, url, **options):
                        original_goto(page, url, **options)
                        if url == requested:
                            page.url = arrived
                    race, _ = self.after_body(lambda page: setattr(page, "url", requested))
                    with patch.object(fixture.Page, "goto", goto):
                        if late_navigation:
                            with race:
                                self.rejected(lambda: adapter.read_draft("900"))
                        else:
                            result = adapter.read_draft("900")
                            self.assertEqual(result["remote_id"], "900")
                            self.assertEqual(result["url"], arrived)
                            self.assertEqual(result["body"], "原稿正文\n\n")

    def test_unconfigured_or_other_draft_redirect_cannot_supply_editor_readback(self):
        for guard in (False, True):
            for target in ("unconfigured", "other-draft"):
                with self.subTest(guard=guard, target=target):
                    adapter = self.opened(guard=guard)
                    requested = adapter.urls["edit"].replace("{remote_id}", "900")
                    arrived = (adapter.profile["origin"] + "/unconfigured/editor/900" if target == "unconfigured"
                               else adapter.urls["edit"].replace("{remote_id}", "901"))
                    original_goto = fixture.Page.goto
                    def goto(page, url, **options):
                        original_goto(page, url, **options)
                        if url == requested:
                            page.url = arrived
                    with patch.object(fixture.Page, "goto", goto):
                        self.rejected(lambda: adapter.read_draft("900"))
                    self.assertFalse(any(event[0] in ("fill", "click") for event in adapter._context.events))

    def test_listing_final_scan_does_not_refresh_a_changed_guard(self):
        for fault in ("account", "empty", "missing"):
            with self.subTest(fault=fault):
                adapter = self.opened(listing=True)
                config = adapter.profile
                config["identity"]["account"] = {"source": "guard_dom", "selector": "#guard-id", "attribute": None,
                    "guard_url": config["origin"] + "/fixture/guard"}
                adapter._context.profile = config
                before = []
                def change(page):
                    before.append(page.context.guard_visits)
                    page.context.options["guard_account"] = "" if fault == "empty" else "other-author"
                    if fault == "missing":
                        page.context.options["guard_missing"] = True
                race, _ = self.after_body(change)
                with race:
                    self.rejected(lambda: adapter.read_draft("900"))
                self.assertEqual(adapter._context.guard_visits, before[0])
                self.assertFalse(adapter._guard_current_only)

    def test_final_guard_read_cannot_hide_same_url_editor_changes(self):
        for field in ("remote-id", "chapter", "title", "body", "state"):
            with self.subTest(field=field):
                adapter = self.opened(guard=True)
                original = adapter._current_editor_guard
                count = []
                def guard():
                    original()
                    count.append(True)
                    if len(count) == 2:
                        page = next(p for p in adapter._context.pages if p.kind == "edit" and not p.closed)
                        node = page.nodes["#" + field][0]
                        if field == "remote-id":
                            node.attrs["data-id"] = "901"
                            # This profile uses the actual URL for remote ID.
                            page.nodes["#chapter"][0].value = "99"
                        elif field == "state":
                            node.text = "已发布"
                        else:
                            node.value = "99" if field == "chapter" else "变动"
                with patch.object(adapter, "_current_editor_guard", guard):
                    self.rejected(lambda: adapter.read_draft("900"))

    def test_resolved_handles_must_still_be_unique_current_selector_targets(self):
        for fault in ("duplicate", "replacement", "detached"):
            with self.subTest(fault=fault):
                adapter = self.opened()
                original_handles = fixture.Locator.element_handles
                original_evaluate = fixture.Page.evaluate
                changed = []
                def resolve(locator):
                    handles = original_handles(locator)
                    if locator.selector == "#body" and not changed:
                        changed.append(True)
                        page = locator.page
                        if fault == "duplicate":
                            page.nodes["#remote-id"].append(fixture.Node(attrs={"data-id": "900"}))
                        elif fault == "replacement":
                            page.nodes["#remote-id"] = [fixture.Node(attrs={"data-id": "900"})]
                        else:
                            page.nodes["#remote-id"] = []
                    return handles
                def evaluate(page, expression, *args):
                    packet = original_evaluate(page, expression, *args)
                    if "editorReadbackSnapshot" in expression and fault == "replacement":
                        # The old node stays connected but stops matching the
                        # selector; a different node is now its only match.
                        packet["fields"]["remote_id"]["connected"] = True
                    return packet
                with patch.object(fixture.Locator, "element_handles", resolve), patch.object(fixture.Page, "evaluate", evaluate):
                    self.rejected(lambda: adapter.read_draft("900"))
                self.assertEqual(changed, [True])

    def test_selector_rechecks_cannot_hide_content_changes_after_the_snapshot(self):
        for platform in ("qimao", "qidian"):
            for field in ("title", "body", "chapter", "state", "remote-id"):
                with self.subTest(platform=platform, field=field):
                    adapter = self.opened(platform)
                    original, changed = fixture.Locator.evaluate_all, []
                    def current(locator, expression, *args):
                        result = original(locator, expression, *args)
                        if locator.selector == "#book" and "elements[0] === expected" in expression and not changed:
                            node = locator.page.nodes["#" + field][0]
                            if field == "remote-id":
                                node.attrs["data-id"] = "901"
                            elif field == "state":
                                node.text = "已发布"
                            else:
                                node.value = "99" if field == "chapter" else "快照后变更"
                            changed.append(True)
                        return result
                    with patch.object(fixture.Locator, "evaluate_all", current):
                        self.rejected(lambda: adapter.read_draft("900"))
                    self.assertEqual(changed, [True])
                    self.assertFalse(any(event[0] in ("fill", "click") for event in adapter._context.events))

    def test_element_handles_are_disposed_on_success_and_snapshot_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                adapter = self.opened()
                resolved, disposed = [], []
                original_handles, original_evaluate = fixture.Locator.element_handles, fixture.Page.evaluate
                def handles(locator):
                    result = original_handles(locator)
                    resolved.extend(result)
                    return result
                def evaluate(page, expression, *args):
                    if fail and "editorReadbackSnapshot" in expression:
                        raise RuntimeError("PRIVATE FULL MANUSCRIPT")
                    return original_evaluate(page, expression, *args)
                with patch.object(fixture.Locator, "element_handles", handles), \
                        patch.object(fixture.ElementHandle, "dispose", lambda handle: disposed.append(handle)), \
                        patch.object(fixture.Page, "evaluate", evaluate):
                    if fail:
                        self.rejected(lambda: adapter.read_draft("900"))
                    else:
                        self.assertEqual(adapter.read_draft("900")["remote_id"], "900")
                self.assertEqual(resolved, disposed)
                self.assertEqual(len(resolved), 7)

    def test_playwright_extended_selectors_are_resolved_by_the_locator_engine(self):
        adapter = self.opened()
        selector = 'xpath=//*[@id="title"]'
        adapter.profile["editor"]["title"]["selector"] = selector
        original = fixture.Page.locator
        seen = []
        def locator(page, selected):
            seen.append(selected)
            return original(page, "#title" if selected == selector else selected)
        with patch.object(fixture.Page, "locator", locator):
            self.assertEqual(adapter.read_draft("900")["title"], "原稿标题")
        self.assertGreaterEqual(seen.count(selector), 2)

    def test_current_snapshot_javascript_preserves_dom_and_prosemirror_serialization(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required to execute the actual browser snapshot JavaScript")
        adapter = self.opened()
        source = []
        original = fixture.Page.evaluate
        def capture(page, expression, *args):
            if "editorReadbackSnapshot" in expression:
                source.append(expression)
            return original(page, expression, *args)
        with patch.object(fixture.Page, "evaluate", capture):
            adapter.read_draft("900")
        script = r'''
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
global.document = {}; global.location = {href: input.url};
const el = (tagName, attrs = {}, childNodes = []) => ({nodeType:1, tagName, childNodes,
  ownerDocument:document,isConnected:true,getAttribute:key=>attrs[key]??null,
  classList:{contains:key=>(attrs.class??'').split(' ').includes(key)}});
const text = textContent => ({nodeType:3,textContent});
const blank = () => el('P',{},[el('BR',{class:'ProseMirror-trailingBreak'})]);
const paragraph = el('DIV',{contenteditable:'true'},[
  el('P',{},[text(' 首段 ')]),blank(),el('P',{},[text('末段')]),blank(),blank()]);
const previewParagraph = value => ({...el('P',{},value === '' ? [] : [text(value)]),
  outerHTML:'<p>'+value+'</p>'});
const preview = el('DIV',{},['',' 首段 ','','末段','',''].map(previewParagraph));
const cases = [
  ['input',{},[{...el('INPUT'),value:'  标题  '}]],
  ['editable',{},[{...el('DIV'),isContentEditable:true,innerText:'内容\n\n'}]],
  ['attribute',{attribute:'data-id'},[el('DIV',{'data-id':'900'})]],
  ['paragraphs',{source:'prosemirror_paragraphs'},[paragraph]],
  ['preview',{source:'fanqie_preview_paragraphs_v1'},[preview]],
  ['previewPending',{source:'fanqie_preview_paragraphs_v1'},[el('DIV')]],
  ['previewUnsupported',{source:'fanqie_preview_paragraphs_v1'},[
    el('DIV',{},[{...previewParagraph('正文'),outerHTML:'<p class="unknown">正文</p>'},previewParagraph('')])]],
  ['duplicate',{},[el('DIV'),el('DIV')]],
  ['detached',{},[{...el('DIV'),isConnected:false}]],
  ['foreign',{},[{...el('DIV'),ownerDocument:{}}]],
  ['unsupported',{source:'prosemirror_paragraphs'},[el('DIV',{contenteditable:'true'},[el('P',{},[el('B')]),blank()])]]
];
const snapshot = eval('('+input.source+')');
process.stdout.write(JSON.stringify(snapshot(cases)));
'''
        result = subprocess.run([node, "-e", script], input=json.dumps({"source": source[0], "url": adapter.urls["edit"].replace("{remote_id}", "900")}),
                                text=True, capture_output=True, check=True, timeout=20)
        fields = json.loads(result.stdout)["fields"]
        self.assertEqual(fields["input"]["value"], "  标题  ")
        self.assertEqual(fields["editable"]["value"], "内容\n\n")
        self.assertEqual(fields["attribute"]["value"], "900")
        self.assertEqual(fields["paragraphs"]["value"], " 首段 \n\n末段\n")
        self.assertEqual(fields["preview"]["value"], "\n 首段 \n\n末段\n")
        self.assertTrue(fields["preview"]["ok"])
        self.assertTrue(fields["previewPending"]["pending"])
        self.assertFalse(fields["previewPending"]["ok"])
        self.assertFalse(fields["previewUnsupported"]["ok"])
        self.assertEqual(fields["duplicate"]["count"], 2)
        self.assertFalse(fields["detached"]["connected"])
        self.assertFalse(fields["foreign"]["connected"])
        self.assertFalse(fields["unsupported"]["ok"])

    def test_journal_never_reconciles_body_from_another_draft(self):
        for platform in ("qimao", "qidian"):
            for switch in (False, True):
                with self.subTest(platform=platform, switch=switch):
                    book = ledger.fixtures.PublishTests()
                    book.setUp()
                    try:
                        book.commit()
                        plan = book.prepare(platform=platform)
                        frozen = plan["manifest"]["chapters"][0]
                        config = fixture.profile(platform)
                        target = {"remote_id": "900", "chapter": 1, "title": frozen["title"], "body": "远端实际错误正文", "state": "草稿"}
                        other = {**target, "remote_id": "901", "chapter": 99, "body": frozen["body"]}
                        adapter = fixture.BrowserTests().opened(config, lists={config["urls"]["drafts"]: [[("900", 1, "草稿"), ("901", 99, "草稿")]]},
                            editors={"900": target, "901": other}, account="author-one", book="book-one")
                        lock_root = (book.root.parent / "global-draft-state").resolve()
                        lock_root.mkdir(mode=0o700)
                        raw = fixture.Locator.input_value
                        def read(locator):
                            if switch and locator.selector == "#body" and locator.page.url.endswith("/900"):
                                locator.page.goto(config["urls"]["edit"].replace("{remote_id}", "901"))
                            return raw(locator)
                        with patch.object(fixture.Locator, "input_value", read), patch.object(ledger.drafts, "_lock_directory", return_value=lock_root):
                            result = ledger.drafts.run_plan(book.book, plan["id"], adapter, execute=True)
                        self.assertFalse(result["ok"])
                        self.assertFalse(result["all_chapters_verified_this_run"])
                        self.assertNotEqual(result["results"][0]["state"], "reconciled")
                        self.assertFalse(result["results"][0]["title_body_verified"])
                        self.assertFalse(any(event[0] in ("fill", "click") for event in adapter._context.events))
                    finally:
                        book.tearDown()

    def test_create_does_not_replace_an_already_allocated_draft_id_after_save(self):
        for platform in ("qimao", "qidian"):
            for listing in (False, True):
                with self.subTest(platform=platform, listing=listing):
                    adapter = self.opened(platform, listing=listing, sync_drafts=listing)
                    context = adapter._context
                    context.editors["901"] = {"remote_id": "901", "chapter": 2, "title": "新标题", "body": "新正文", "state": "草稿"}
                    original_goto, original_persist = fixture.Page.goto, context.persist
                    def goto(page, url, **kwargs):
                        original_goto(page, url, **kwargs)
                        if page.kind == "create":
                            page.nodes["#remote-id"][0].attrs["data-id"] = "allocated-1"
                    def persist(page):
                        original_persist(page)
                        page.nodes["#remote-id"][0].attrs["data-id"] = "901"
                        if listing:
                            context.lists[adapter.urls["drafts"]] = [[("900", 1, "草稿"), ("901", 2, "草稿")]]
                    with patch.object(fixture.Page, "goto", goto), patch.object(context, "persist", persist):
                        self.rejected(lambda: adapter.create_draft(2, "新标题", "新正文"))
                    self.assertEqual(context.events.count(("click", "#save")), 1)
                    self.assertEqual(context.editors["empty-1" if listing else "allocated-1"]["body"], "新正文")

    def test_autosave_allocated_id_is_bound_before_filling_the_next_field(self):
        adapter = self.opened(autosave=True)
        adapter.profile["editor"]["save"] = {"mode": "autosave"}
        context = adapter._context
        original = context.persist
        writes = []
        def persist(page):
            original(page)
            writes.append(page.nodes["#remote-id"][0].attrs["data-id"])
            if len(writes) == 2:
                page.nodes["#remote-id"][0].attrs["data-id"] = "901"
        with patch.object(context, "persist", persist):
            self.rejected(lambda: adapter.create_draft(2, "新标题", "新正文"))
        self.assertEqual(writes, ["new-1", "new-1"])
        self.assertNotIn(("fill", "#body"), context.events)

    def test_empty_creation_check_cannot_rebind_to_another_draft_before_first_fill(self):
        for platform in ("qimao", "qidian"):
            with self.subTest(platform=platform):
                adapter = self.opened(platform)
                original = fixture.Locator.input_value
                changed = []
                def read(locator):
                    value = original(locator)
                    if locator.selector == "#body" and locator.page.kind == "create" and not changed:
                        changed.append(True)
                        locator.page.goto(adapter.urls["edit"].replace("{remote_id}", "901"))
                    return value
                with patch.object(fixture.Locator, "input_value", read):
                    self.rejected(lambda: adapter.create_draft(2, "新标题", "新正文"))
                self.assertEqual(changed, [True])
                self.assertFalse(any(event[0] in ("fill", "click") for event in adapter._context.events))

    def test_unallocated_create_navigation_between_fields_stops_before_next_fill(self):
        adapter = self.opened()
        original = fixture.Locator.fill
        def fill(locator, value):
            original(locator, value)
            if locator.selector == "#chapter":
                locator.page.goto(adapter.urls["edit"].replace("{remote_id}", "901"))
        with patch.object(fixture.Locator, "fill", fill):
            self.rejected(lambda: adapter.create_draft(2, "新标题", "新正文"))
        self.assertEqual([event for event in adapter._context.events if event[0] in ("fill", "click")], [("fill", "#chapter")])

    def test_unallocated_create_url_change_cannot_reuse_the_original_fields(self):
        for platform in ("qimao", "qidian"):
            for trigger in ("before-first-fill", "between-fields"):
                for missing in (False, True):
                    with self.subTest(platform=platform, trigger=trigger, missing=missing):
                        adapter = self.opened(platform)
                        original_evaluate, original_fill = fixture.Locator.evaluate, fixture.Locator.fill
                        changed = []
                        def change(page):
                            page.url = adapter.urls["edit"].replace("{remote_id}", "901")
                            if missing:
                                page.nodes["#remote-id"] = []
                            changed.append(True)
                        def evaluate(locator, expression, *args):
                            result = original_evaluate(locator, expression, *args)
                            if (trigger == "before-first-fill" and locator.selector == "#chapter" and
                                    "editable: el.isContentEditable" in expression and not changed):
                                change(locator.page)
                            return result
                        def fill(locator, value):
                            result = original_fill(locator, value)
                            if trigger == "between-fields" and locator.selector == "#chapter" and not changed:
                                change(locator.page)
                            return result
                        with patch.object(fixture.Locator, "evaluate", evaluate), patch.object(fixture.Locator, "fill", fill):
                            self.rejected(lambda: adapter.create_draft(2, "新标题", "新正文"))
                        self.assertEqual(changed, [True])
                        writes = [event for event in adapter._context.events if event[0] in ("fill", "click")]
                        self.assertEqual(writes, [("fill", "#chapter")] if trigger == "between-fields" else [])

    def test_first_allocated_id_allows_matching_route_change_with_original_fields(self):
        for platform in ("qimao", "qidian"):
            with self.subTest(platform=platform):
                adapter = self.opened(platform)
                original_fill = fixture.Locator.fill
                def fill(locator, value):
                    result = original_fill(locator, value)
                    if locator.selector == "#chapter":
                        locator.page.url = adapter.urls["edit"].replace("{remote_id}", "new-1")
                        locator.page.nodes["#remote-id"][0].attrs["data-id"] = "new-1"
                    return result
                with patch.object(fixture.Locator, "fill", fill):
                    self.assertEqual(adapter.create_draft(2, "新标题", "新正文"), "new-1")
                self.assertEqual(adapter._context.editors["new-1"]["body"], "新正文")
                self.assertEqual(adapter._context.events.count(("click", "#save")), 1)

    def test_create_stops_before_next_write_when_current_draft_state_is_lost(self):
        for platform in ("qimao", "qidian"):
            for trigger in ("between-fields", "before-save"):
                for fault in ("published", "missing", "duplicate"):
                    with self.subTest(platform=platform, trigger=trigger, fault=fault):
                        adapter = self.opened(platform)
                        original_fill, original_attribute = fixture.Locator.fill, fixture.Locator.get_attribute
                        changed = []
                        def change(page):
                            if fault == "published":
                                page.nodes["#state"][0].text = "已发布"
                            elif fault == "missing":
                                page.nodes["#state"] = []
                            else:
                                page.nodes["#state"].append(fixture.Node("草稿"))
                            changed.append(True)
                        def fill(locator, value):
                            result = original_fill(locator, value)
                            if trigger == "between-fields" and locator.selector == "#chapter" and not changed:
                                change(locator.page)
                            return result
                        def attribute(locator, name):
                            result = original_attribute(locator, name)
                            if trigger == "before-save" and locator.selector == "#save" and name == "title" and not changed:
                                change(locator.page)
                            return result
                        with patch.object(fixture.Locator, "fill", fill), patch.object(fixture.Locator, "get_attribute", attribute):
                            self.rejected(lambda: adapter.create_draft(2, "新标题", "新正文"))
                        self.assertEqual(changed, [True])
                        expected = [("fill", "#chapter")]
                        if trigger == "before-save":
                            expected += [("fill", "#title"), ("fill", "#body")]
                        self.assertEqual([event for event in adapter._context.events if event[0] in ("fill", "click")], expected)

    def test_internal_fill_type_read_cannot_redirect_the_actual_write(self):
        for platform in ("qimao", "qidian"):
            with self.subTest(platform=platform):
                adapter = self.opened(platform, listing=True, sync_drafts=True, autosave=True)
                original = fixture.Locator.evaluate
                previous = adapter._context.editors["900"].copy()
                changed = []
                def evaluate(locator, expression, *args):
                    result = original(locator, expression, *args)
                    if (locator.selector == "#title" and "editable: el.isContentEditable" in expression and
                            ("fill", "#chapter") in locator.page.context.events and not changed):
                        changed.append(True)
                        locator.page.goto(adapter.urls["edit"].replace("{remote_id}", "900"))
                    return result
                with patch.object(fixture.Locator, "evaluate", evaluate):
                    self.rejected(lambda: adapter.create_draft(2, "不得覆盖旧标题", "新正文"))
                self.assertEqual(changed, [True])
                self.assertEqual(adapter._context.editors["900"], previous)
                self.assertEqual([e for e in adapter._context.events if e[0] == "fill"], [("fill", "#chapter")])

    def test_internal_save_label_read_cannot_redirect_the_save_click(self):
        adapter = self.opened(listing=True, sync_drafts=True)
        original = fixture.Locator.get_attribute
        previous = adapter._context.editors["900"].copy()
        changed = []
        def read(locator, name):
            result = original(locator, name)
            if locator.selector == "#save" and name == "title" and not changed:
                changed.append(True)
                locator.page.goto(adapter.urls["edit"].replace("{remote_id}", "900"))
            return result
        with patch.object(fixture.Locator, "get_attribute", read):
            self.rejected(lambda: adapter.create_draft(2, "新标题", "新正文"))
        self.assertEqual(changed, [True])
        self.assertEqual(adapter._context.editors["900"], previous)
        self.assertNotIn(("click", "#save"), adapter._context.events)

    def test_save_button_changed_during_final_identity_check_is_not_clicked(self):
        for fault, trigger in ((fault, trigger) for fault in ("text", "aria-label", "title") for trigger in (1, 2)):
            with self.subTest(fault=fault, trigger=trigger):
                adapter = self.opened()
                checked, observed, changed = [], [], []
                original_attr = fixture.Locator.get_attribute
                original_current = adapter._preview_current_value
                def attribute(locator, name):
                    result = original_attr(locator, name)
                    if locator.selector == "#save" and name == "title":
                        checked.append(True)
                    return result
                def current(page, spec):
                    result = original_current(page, spec)
                    if checked and spec is adapter.profile["identity"]["account"]:
                        observed.append(True)
                    if len(observed) == trigger and not changed:
                        button = page.nodes["#save"][0]
                        if fault == "text":
                            button.text = "发布"
                        else:
                            button.attrs[fault] = "发布章节"
                        persist = button.click_action
                        def publish():
                            adapter._context.events.append(("publish", "#save"))
                            persist()
                        button.click_action = publish
                        changed.append(True)
                    return result
                caught = None
                with patch.object(fixture.Locator, "get_attribute", attribute), \
                        patch.object(adapter, "_preview_current_value", current):
                    try:
                        adapter.create_draft(2, "新标题", "新正文")
                    except browser.BrowserAdapterError as error:
                        caught = error
                self.assertEqual(changed, [True])
                self.assertNotIn(("publish", "#save"), adapter._context.events)
                self.assertNotIn(("click", "#save"), adapter._context.events)
                self.assertIsNotNone(caught)
                self.assertEqual(caught.code, "platform_browser_unsafe_save")

    def test_paragraph_focus_is_checked_after_the_last_identity_observation(self):
        try:
            from . import test_platform_browser_fanqie_body as paragraphs
        except ImportError:
            import test_platform_browser_fanqie_body as paragraphs
        state = {"focused": False, "checked": False, "typed_into": []}
        class Paragraph(paragraphs.ParagraphLocator):
            def evaluate(self, expression):
                if expression.strip() == '(el) => el.getAttribute("contenteditable") === "true"':
                    return True
                return {"ok": True, "value": "正文"}
            def element_handles(self):
                return [self]
            def evaluate_all(self, expression, handle=None):
                if "activeElement" in expression:
                    state["checked"] = True
                    return state["focused"]
                return {"count": 1, "same": True}
            def dispose(self):
                pass
            def focus(self):
                state["focused"] = True
        class Keyboard:
            def insert_text(self, value):
                state["typed_into"].append("body" if state["focused"] else "other-input")
        class Page:
            keyboard = Keyboard()
        def guard():
            if state["checked"]:
                state["focused"] = False
        self.opened()._paragraph_fill(Page(), Paragraph(), "正文", guard)
        self.assertEqual(state["typed_into"], ["body"])

    def test_create_button_changed_during_identity_check_is_not_clicked(self):
        for opens in ("popup", "same_page"):
            for fault, trigger in ((fault, trigger) for fault in ("text", "aria-label", "title") for trigger in (1, 2, 3)):
                with self.subTest(opens=opens, fault=fault, trigger=trigger):
                    adapter = self.opened(listing=True, sync_drafts=True)
                    adapter.profile["create_action"]["opens"] = opens
                    checked, observed, changed = [], [], []
                    original_attr = fixture.Locator.get_attribute
                    original_identity = adapter._require_identity
                    def attribute(locator, name):
                        result = original_attr(locator, name)
                        if locator.selector == "#create" and name == "title":
                            checked.append(True)
                        return result
                    def identity(page):
                        original_identity(page)
                        if checked:
                            observed.append(True)
                        if len(observed) == trigger and not changed:
                            button = page._lookup("#create")[0]
                            if fault == "text":
                                button.text = "发布"
                            else:
                                button.attrs[fault] = "发布章节"
                            create = button.click_action
                            def publish():
                                adapter._context.events.append(("publish", "#create"))
                                create()
                            button.click_action = publish
                            changed.append(True)
                    caught = None
                    with patch.object(fixture.Locator, "get_attribute", attribute), \
                            patch.object(adapter, "_require_identity", identity):
                        try:
                            adapter.create_draft(2, "新标题", "新正文")
                        except browser.BrowserAdapterError as error:
                            caught = error
                    self.assertEqual(changed, [True])
                    self.assertNotIn(("publish", "#create"), adapter._context.events)
                    self.assertNotIn(("click", "#create"), adapter._context.events)
                    self.assertEqual(adapter._context.created_blank, 0)
                    self.assertIsNotNone(caught)
                    self.assertEqual(caught.code, "platform_browser_unsafe_create")

    def test_draft_action_link_destination_cannot_change_after_initial_checks(self):
        for operation in ("save", "create-same-page", "create-popup"):
            for initial, final in ((None, "/publish"), ("/draft/new", "/publish"), ("/draft/new", None)):
                with self.subTest(operation=operation, initial=initial, final=final):
                    adapter = self.opened(listing=operation != "save", sync_drafts=operation != "save")
                    if operation == "create-same-page":
                        adapter.profile["create_action"]["opens"] = "same_page"
                        adapter._context.options["creation_opens"] = "same_page"
                    selector = "#save" if operation == "save" else "#create"
                    original_attribute, original_action = fixture.Locator.get_attribute, adapter._checked_editor_action
                    initialized, changed = [], []
                    def attribute(locator, name):
                        if locator.selector == selector and not initialized:
                            if initial is not None:
                                locator.node().attrs["href"] = initial
                                locator.node().tag = "A"
                            initialized.append(True)
                        return original_attribute(locator, name)
                    def checked(locator, action, before_write, *args, **kwargs):
                        def before():
                            before_write()
                            if locator.selector == selector and not changed:
                                node = locator.node()
                                if final is None:
                                    node.attrs.pop("href", None)
                                else:
                                    node.attrs["href"] = final
                                changed.append(True)
                        return original_action(locator, action, before, *args, **kwargs)
                    with patch.object(fixture.Locator, "get_attribute", attribute), patch.object(adapter, "_checked_editor_action", checked):
                        self.rejected(lambda: adapter.create_draft(2, "新标题", "新正文"))
                    self.assertEqual(changed, [True])
                    self.assertNotIn(("click", selector), adapter._context.events)

    def test_stable_link_and_nonlink_draft_buttons_remain_usable(self):
        for operation in ("save", "create-same-page", "create-popup"):
            for href in (None, "/draft/action"):
                with self.subTest(operation=operation, href=href):
                    adapter = self.opened(listing=operation != "save", sync_drafts=operation != "save")
                    if operation == "create-same-page":
                        adapter.profile["create_action"]["opens"] = "same_page"
                        adapter._context.options["creation_opens"] = "same_page"
                    selector = "#save" if operation == "save" else "#create"
                    original_attribute = fixture.Locator.get_attribute
                    def attribute(locator, name):
                        if locator.selector == selector and href is not None:
                            locator.node().attrs["href"] = href
                            locator.node().tag = "A"
                        return original_attribute(locator, name)
                    with patch.object(fixture.Locator, "get_attribute", attribute):
                        self.assertEqual(adapter.create_draft(2, "新标题", "新正文"),
                                         "new-1" if operation == "save" else "empty-1")
                    self.assertEqual(adapter._context.events.count(("click", selector)), 1)

    def test_paragraph_focus_keeps_editable_descendants_but_never_types_into_tool_controls(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required to execute the actual focus predicate")
        try:
            from . import test_platform_browser_fanqie_body as paragraphs
        except ImportError:
            import test_platform_browser_fanqie_body as paragraphs
        cases = (("root", True, True), ("P", True, True), ("SPAN", True, True),
                 ("INPUT", True, False), ("TEXTAREA", True, False), ("SELECT", True, False),
                 ("BUTTON", True, False), ("SPAN", False, False), ("blocked-SPAN", True, False),
                 ("shadow-INPUT", True, False))
        for tag, editable, allowed in cases:
            with self.subTest(tag=tag, editable=editable):
                written = []
                class Paragraph(paragraphs.ParagraphLocator):
                    def evaluate(self, expression):
                        if expression.strip() == '(el) => el.getAttribute("contenteditable") === "true"':
                            return True
                        return {"ok": True, "value": "正文"}
                    def element_handles(self):
                        return [self]
                    def evaluate_all(self, expression, handle=None):
                        if "activeElement" not in expression:
                            return {"count": 1, "same": True}
                        script = r'''
const fs=require('fs'); const input=JSON.parse(fs.readFileSync(0,'utf8'));
const root={tagName:'DIV',isContentEditable:true,getAttribute:()=>null};
const barrier={parentElement:root,getAttribute:key=>key==='contenteditable'?'false':null};
const active=input.tag==='root'?root:{tagName:input.tag.replace(/^(blocked-|shadow-)/,''),
  isContentEditable:input.editable,parentElement:input.tag.startsWith('blocked-')?barrier:root,
  getAttribute:()=>null};
const exposed=input.tag.startsWith('shadow-')?{tagName:'SPAN',isContentEditable:true,
  parentElement:root,getAttribute:()=>null,shadowRoot:{activeElement:active}}:active;
root.contains=value=>value===root||value===exposed;
global.document={activeElement:exposed};
const check=eval('('+input.expression+')');process.stdout.write(JSON.stringify(check([root])));
'''
                        result = subprocess.run([node, "-e", script], text=True, capture_output=True, check=True,
                            input=json.dumps({"tag": tag, "editable": editable, "expression": expression}), timeout=20)
                        return json.loads(result.stdout)
                    def dispose(self):
                        pass
                class Keyboard:
                    def insert_text(self, value):
                        written.append("body" if allowed else "tool-control")
                class Page:
                    keyboard = Keyboard()
                error = None
                try:
                    self.opened()._paragraph_fill(Page(), Paragraph(), "正文", lambda: None)
                except browser.BrowserAdapterError as caught:
                    error = caught
                self.assertNotIn("tool-control", written)
                if allowed:
                    self.assertEqual(written, ["body"])
                    self.assertIsNone(error)
                else:
                    self.assertEqual(written, [])
                    self.assertIsNotNone(error)
                    self.assertEqual(error.code, "platform_browser_editor_changed")

    def test_paragraph_writer_stops_before_the_next_action_after_target_change(self):
        try:
            from . import test_platform_browser_fanqie_body as paragraphs
        except ImportError:
            import test_platform_browser_fanqie_body as paragraphs
        expected = [("click",), ("press", "Control+A"), ("press", "Backspace"),
                    ("focus",), ("insert_text", "首段"), ("press", "Enter"), ("focus",), ("insert_text", "末段")]
        for trigger in ("editable", 1, 2, 3, 4, 5, 6):
            with self.subTest(after=trigger):
                events, changed = [], []
                class CurrentParagraph(paragraphs.ParagraphLocator):
                    def evaluate(self, expression):
                        if expression.strip() == '(el) => el.getAttribute("contenteditable") === "true"':
                            if trigger == "editable":
                                changed.append(True)
                            return True
                        return {"ok": True, "value": "首段\n末段"}
                    def element_handles(self):
                        return [self]
                    def evaluate_all(self, expression, handle=None):
                        return True if "activeElement" in expression else {"count": 1, "same": True}
                    def dispose(self):
                        pass
                    def _event(self, *event):
                        events.append(event)
                        if len(events) == trigger:
                            changed.append(True)
                    def click(self):
                        self._event("click")
                    def press(self, key):
                        self._event("press", key)
                    def focus(self):
                        self._event("focus")
                locator = CurrentParagraph()
                class Keyboard:
                    def insert_text(self, value):
                        locator._event("insert_text", value)
                class Page:
                    keyboard = Keyboard()
                def guard():
                    if changed:
                        browser._fail("identity_mismatch", "fixture target changed")
                with patch.object(browser.sys, "platform", "linux"):
                    self.rejected(lambda: self.opened()._paragraph_fill(Page(), locator, "首段\n末段", guard))
                self.assertEqual(events, expected[:0 if trigger == "editable" else trigger])


if __name__ == "__main__":
    unittest.main()

"""Independent full native create path: effective self-links and actual clicks."""
from pathlib import Path
import hashlib
import json
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from tests import test_platform_browser as f
source = ROOT / "skills/story-skill/scripts/story_platform_browser.py"
before = hashlib.sha256(source.read_bytes()).hexdigest()
old_api = f.browser.api
f.browser.api = None
rows = []
try:
    for platform in ("qimao", "qidian"):
        for href, normal_allowed in (("", True), ("#save", True), (" \t#save\r\n", True),
                                     ("\u00a0#save", False), ("\u3000#save", False), ("\u2000#save", False),
                                     ("\u00a0", False), ("\u3000", False), ("?draft=1", False),
                                     ("next", False), ("/draft/next", True), (None, True)):
            for override in (False, True):
                helper = f.BrowserTests()
                config = helper.url_guard_profile(platform)
                alias = config["origin"] + "/fixture/{book_id}/modify/{remote_id}?from=edit"
                config["editor"]["remote_id"]["templates"].append(alias)
                config["identity"]["book"]["templates"].append(alias)
                adapter = helper.opened(config)
                adapter.verify_target(platform, "author-1", "100")
                fill, attr, action, clicked = f.Locator.fill, f.Locator.get_attribute, adapter._checked_editor_action, f.Locator.click
                changed, destinations = [], []
                def filled(locator, value):
                    result = fill(locator, value)
                    if locator.selector == "#chapter":
                        locator.page.context.persist(locator.page)
                        locator.page.url = config["urls"]["edit"].replace("{remote_id}", "new-1")
                    return result
                def attribute(locator, name):
                    if locator.selector == "#save":
                        if href is None:
                            locator.node().attrs.pop("href", None)
                        else:
                            locator.node().attrs["href"] = href
                            locator.node().tag = "A"
                    return attr(locator, name)
                def guarded(locator, name, before_write, *args, **kwargs):
                    def before_write_and_alias():
                        before_write()
                        if locator.selector == "#save" and not changed:
                            locator.page.url = alias.replace("{book_id}", "100").replace("{remote_id}", "new-1")
                            if override:
                                locator.page.context.options["base_url"] = config["origin"] + "/publish/"
                            changed.append(True)
                    return action(locator, name, before_write_and_alias, *args, **kwargs)
                def click(locator):
                    if locator.selector == "#save":
                        destinations.append(f.action_link(locator.node(), locator.page))
                    return clicked(locator)
                try:
                    with patch.object(f.Locator, "fill", filled), patch.object(f.Locator, "get_attribute", attribute), \
                         patch.object(adapter, "_checked_editor_action", guarded), patch.object(f.Locator, "click", click):
                        try:
                            remote = adapter.create_draft(2, "新标题", "新正文")
                            error = None
                        except f.browser.BrowserAdapterError as caught:
                            remote, error = None, caught.code
                    allowed = normal_allowed and (not override or href is None or href == "/draft/next")
                    assert changed == [True]
                    assert len(destinations) == int(allowed), (platform, href, override, destinations, error)
                    if allowed:
                        assert remote == "new-1" and error is None, (platform, href, override, error)
                    else:
                        assert remote is None and error == "platform_browser_unsafe_save", (platform, href, override, error)
                    rows.append({"platform": platform, "href": href, "base_override": override,
                                 "returned": remote, "error": error, "actual_click_destinations": destinations})
                finally:
                    adapter.close()
finally:
    f.browser.api = old_api
after = hashlib.sha256(source.read_bytes()).hexdigest()
assert before == after
summary = {"ok": True, "scenarios": len(rows), "source_sha256": after,
           "scope": "Synthetic public create_draft full flow with actual fixture click recording, no real account or browser transport",
           "results": rows}
(OUT / "native-link-result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({key: value for key, value in summary.items() if key != "results"}, ensure_ascii=False))

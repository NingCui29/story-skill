"""Lossless paragraph serialization for the inspected Fanqie plain-text editor.

These are offline DOM-shape and native-input contract tests, not remote saves.
"""
import json
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from tests import test_platform_browser as fixtures


browser = fixtures.browser
NODE = shutil.which("node")
BR = '<br class="ProseMirror-trailingBreak">'
PLACEHOLDER = ('<p><span class="syl-placeholder ProseMirror-widget" ignoreel="true" '
               'contenteditable="false">请输入正文</span>'
               '<img class="ProseMirror-separator" alt="">' + BR + '</p>')


class DOMParser(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = {"tag": "DIV", "attrs": {"contenteditable": "true"}, "children": []}
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag.upper(), "attrs": dict(attrs), "children": []}
        self.stack[-1]["children"].append(node)
        if tag not in ("br", "img"):
            self.stack.append(node)

    def handle_endtag(self, tag):
        if self.stack[-1]["tag"] != tag.upper():
            raise ValueError("Unbalanced fixture HTML")
        self.stack.pop()

    def handle_data(self, value):
        self.stack[-1]["children"].append({"text": value})


DOM_RUNTIME = r"""
function inflate(spec) {
  const node = {nodeType: spec.tag ? 1 : 3, tagName: spec.tag,
    childNodes: (spec.children || []).map(inflate),
    getAttribute: key => (Object.hasOwn(spec.attrs || {}, key) ? spec.attrs[key] : null),
    classList: {contains: value => ((spec.attrs || {}).class || '').split(/\s+/).includes(value)}};
  Object.defineProperty(node, 'textContent', {get: () => spec.tag ?
    node.childNodes.map(child => child.textContent).join('') : spec.text});
  Object.defineProperty(node, 'outerHTML', {get: () => !spec.tag ? spec.text :
    '<' + spec.tag.toLowerCase() + Object.entries(spec.attrs || {}).map(([key,value]) =>
      ' ' + key + '="' + value + '"').join('') + '>' + node.childNodes.map(child => child.outerHTML).join('') +
    (['BR','IMG'].includes(spec.tag) ? '' : '</' + spec.tag.toLowerCase() + '>')});
  return node;
}
"""


class ParagraphLocator:
    def __init__(self, html=PLACEHOLDER, events=None, packet=None):
        self.dom = DOMParser(html).root
        self.events = events if events is not None else []
        self.packet = packet

    def evaluate(self, expression):
        if expression.strip() == '(el) => el.getAttribute("contenteditable") === "true"':
            return self.dom["attrs"].get("contenteditable") == "true"
        if self.packet is not None:
            return self.packet
        if NODE is None:
            raise RuntimeError("Node.js required to execute the actual DOM reader")
        program = DOM_RUNTIME + "\nconst read = (" + expression + ");\n" + (
            "process.stdout.write(JSON.stringify(read(inflate(" + json.dumps(self.dom, ensure_ascii=False) + "))));"
        )
        completed = subprocess.run([NODE, "-e", program], capture_output=True, text=True, timeout=5)
        if completed.returncode:
            raise AssertionError("DOM reader failed to execute")
        return json.loads(completed.stdout)

    def click(self):
        self.events.append(("click",))

    def focus(self):
        self.events.append(("focus",))

    def press(self, key):
        self.events.append(("press", key))

    def fill(self, value):
        raise AssertionError("Whole-field fill must not be used for this editor")


class Keyboard:
    def __init__(self, events):
        self.events = events

    def insert_text(self, value):
        self.events.append(("insert_text", value))


class Page:
    def __init__(self, events):
        self.keyboard = Keyboard(events)


def html_for(value):
    import html
    if value == "":
        return PLACEHOLDER
    return "".join("<p>" + (html.escape(line) if line else BR) + "</p>"
                   for line in value.split("\n")) + "<p>" + BR + "</p>"


@unittest.skipUnless(NODE, "Node.js required for the actual pure DOM serializer")
class FanqieParagraphBodyTests(unittest.TestCase):
    def setUp(self):
        self.adapter = browser.BrowserAdapter(fixtures.profile())

    def read(self, html):
        return self.adapter._paragraph_value(ParagraphLocator(html))

    def test_observed_saved_dom_cases_preserve_all_intended_empty_lines(self):
        evidence = Path(__file__).resolve().parents[1] / (
            "benchmarks/results/development-20261019-platform-drafts/fanqie-richtext-observations.json")
        observations = json.loads(evidence.read_text(encoding="utf-8"))["observations"]
        for sample in observations:
            with self.subTest(value=repr(sample["plain_text"])):
                self.assertEqual(self.read(sample["dom"]), sample["plain_text"])

    def test_chinese_whitespace_and_multiple_trailing_lines_round_trip_without_trimming(self):
        for value in ("\n中文甲\n\n中文乙\n\n", "  空格保留　\n\n末段  ", "\n\n", "请输入正文"):
            with self.subTest(value=repr(value)):
                self.assertEqual(self.read(html_for(value)), value)

    def test_observed_ai_tail_widget_is_only_ignored_in_its_exact_final_position(self):
        evidence = Path(__file__).resolve().parents[1] / (
            "benchmarks/results/development-20261019-platform-drafts/fanqie-richtext-observations.json")
        sample = next(row for row in json.loads(evidence.read_text(encoding="utf-8"))["observations"]
                      if "syl-inline" in row["dom"])
        dom = sample["dom"]
        self.assertEqual(self.read(dom), sample["plain_text"])
        variants = [dom.replace("AI帮你构思剧情", "AI帮你修改正文"),
                    dom.replace('ignoreel="true"', 'ignoreel="false"'),
                    dom.replace('contenteditable="false"', 'contenteditable="true"'),
                    dom.replace('class="tomato-ai-tool-inspire-v2"', 'class="unknown-widget"'),
                    dom.replace('</i>', '实际文字</i>'),
                    dom.replace('</syl-inline>', '</syl-inline>实际文字'),
                    dom.replace('alt=""', 'alt="实际文字"'),
                    dom.replace('<syl-inline ', '<syl-inline data-unknown="true" '),
                    dom + '<p>' + BR + '</p>']
        for variant in variants:
            with self.subTest(html=variant):
                with self.assertRaises(browser.BrowserAdapterError) as caught:
                    self.read(variant)
                self.assertEqual(caught.exception.code, "platform_browser_unsupported_editor")

    def test_unknown_nodes_missing_sentinel_and_mixed_placeholder_are_rejected(self):
        cases = ["<p>缺少哨兵</p>", "<div>错误块</div><p>" + BR + "</p>",
                 "泄露在段落外<p>" + BR + "</p>", "<p><strong>未知格式</strong></p><p>" + BR + "</p>",
                 "<p>文字" + BR + "</p><p>" + BR + "</p>", "<p></p><p>" + BR + "</p>",
                 "<p>正文</p><p></p>", PLACEHOLDER.replace("alt=\"\"", "alt=\"不能删除的文字\""),
                 PLACEHOLDER.replace("</p>", "有实际文字</p>")]
        for value in cases:
            with self.subTest(html=value):
                with self.assertRaises(browser.BrowserAdapterError) as caught:
                    self.read(value)
                self.assertEqual(caught.exception.code, "platform_browser_unsupported_editor")

    def test_native_writer_focuses_target_and_preserves_each_newline(self):
        value = "\n中文甲\n\n中文乙\n\n"
        for platform, shortcut in (("darwin", "Meta+A"), ("linux", "Control+A")):
            with self.subTest(platform=platform):
                events = []
                locator = ParagraphLocator(html_for(value), events)
                with patch.object(browser.sys, "platform", platform):
                    self.adapter._paragraph_fill(Page(events), locator, value)
                self.assertEqual(events[:3], [("click",), ("press", shortcut), ("press", "Backspace")])
                rebuilt = []
                for index, event in enumerate(events[3:], start=3):
                    if event == ("press", "Enter"):
                        rebuilt.append("\n")
                    if event[0] == "insert_text":
                        self.assertEqual(events[index - 1], ("focus",))
                        self.assertNotIn("\n", event[1])
                        rebuilt.append(event[1])
                self.assertEqual("".join(rebuilt), value)

    def test_native_writer_normalizes_only_crlf_and_cr(self):
        value = "\r\n甲\r\r乙\r\n"
        expected = "\n甲\n\n乙\n"
        events = []
        self.adapter._paragraph_fill(Page(events), ParagraphLocator(html_for(expected), events), value)
        self.assertEqual([row[1] for row in events if row[0] == "insert_text"], ["甲", "乙"])
        self.assertEqual(sum(row == ("press", "Enter") for row in events), 4)

    def test_partial_or_changed_readback_is_not_reported_as_success(self):
        events = []
        with self.assertRaises(browser.BrowserAdapterError) as caught:
            self.adapter._paragraph_fill(Page(events), ParagraphLocator(html_for("丢失空段"), events), "\n丢失空段\n")
        self.assertEqual(caught.exception.code, "platform_browser_editor_changed")

    def test_reader_errors_do_not_forward_dom_text_or_exception_details(self):
        with self.assertRaises(browser.BrowserAdapterError) as caught:
            self.adapter._paragraph_value(ParagraphLocator(packet={"ok": False, "reason": "PRIVATE FULL BODY"}))
        self.assertNotIn("PRIVATE", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

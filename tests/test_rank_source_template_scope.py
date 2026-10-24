"""Template fragments must not close ancestors outside their inert scope."""
from pathlib import Path
import tempfile
import unittest

import test_rank_sources as fixtures

sources = fixtures.sources


class RankSourceTemplateScopeTests(unittest.TestCase):
    def test_self_closed_inert_html_is_rejected_before_accepting_its_contents(self):
        for tag in ("template", "div hidden", "span hidden=\"false\"", "script", "style"):
            cards = "<" + tag + "/>" + fixtures.card("900") + "</" + tag.split()[0] + ">" + fixtures.card("100")
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, "重新导出.*完整结束标签"):
                sources.parse_qimao_page(fixtures.page(cards=cards), 1)

    def test_foreign_self_closing_decoration_and_void_html_remain_accepted(self):
        for decoration in ("<svg hidden/>", "<math hidden/>",
                           "<svg><path hidden/><circle/></svg>",
                           "<math><mspace hidden/><mi>x</mi></math>",
                           "<svg><mi><g hidden/></mi></svg>",
                           '<svg><annotation-xml encoding="text/html"><g hidden/></annotation-xml></svg>',
                           "<math><foreignObject><mspace hidden/></foreignObject></math>",
                           "<svg><foreignObject><svg><path hidden/></svg></foreignObject></svg>",
                           "<img hidden/><br/>"):
            with self.subTest(decoration=decoration):
                entries = sources.parse_qimao_page(fixtures.page(cards=decoration + fixtures.card("100")), 1)
                self.assertEqual([item["book_id"] for item in entries], ["100"])

    def test_html_integration_and_breakout_do_not_bypass_inert_self_closing_check(self):
        for prefix, suffix in (("<svg><foreignObject>", "</foreignObject></svg>"),
                               ("<svg><desc>", "</desc></svg>"),
                               ("<math><mtext>", "</mtext></math>"),
                               ("<math><annotation-xml encoding=\"text/html\">", "</annotation-xml></math>"),
                               ("<svg>", "</svg>")):
            cards = prefix + "<div hidden/>" + fixtures.card("900") + "</div>" + suffix + fixtures.card("100")
            with self.subTest(prefix=prefix), self.assertRaisesRegex(ValueError, "重新导出.*完整结束标签"):
                sources.parse_qimao_page(fixtures.page(cards=cards), 1)

    def test_foreign_breakout_keeps_later_inert_siblings_in_html_namespace(self):
        for namespace in ("svg", "math"):
            for breakout in ("<p>外部</p>", "<div>外部</div>", '<font color="red">外部</font>',
                             "<g></p>", "<g></br>"):
                for inert in ("template", "g hidden"):
                    cards = (f"<{namespace}>" + breakout + "<" + inert + "/>" + fixtures.card("900") +
                             "</" + inert.split()[0] + f"></{namespace}>" + fixtures.card("100"))
                    with self.subTest(namespace=namespace, breakout=breakout, inert=inert):
                        with self.assertRaisesRegex(ValueError, "重新导出.*完整结束标签"):
                            sources.parse_qimao_page(fixtures.page(cards=cards), 1)

    def test_template_end_tags_cannot_escape_into_outer_page(self):
        for closing in ("</ul>", "</body>", "</html>", "</ul></body></html>"):
            with self.subTest(closing=closing):
                cards = "<template>" + closing + fixtures.card("900") + "</template>" + fixtures.card("100")
                entries = sources.parse_qimao_page(fixtures.page(cards=cards), 1)
                self.assertEqual([(item["book_id"], item["rank"]) for item in entries], [("100", 1)])

    def test_template_controls_cannot_escape_their_scope(self):
        for control, message in (("<a class=\"tab-inner active\">按点击量</a>", "排序"),
                                 ("<a class=\"page-btn num active\">1</a>", "页码")):
            html = fixtures.page().replace(control, "<template></body>" + control + "</template>")
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                sources.parse_qimao_page(html, 1)

    def test_template_boundaries_preserve_visible_inline_text(self):
        card = fixtures.card().replace("样本&amp;书名",
            "样本<template></a></span></li></ul>旧版</template>&amp;书名")
        self.assertEqual(sources.parse_qimao_page(fixtures.page(cards=card), 1)[0]["title"], "样本&书名")

    def test_nested_templates_close_their_own_children_and_resume_visible_siblings(self):
        cards = ("<template><div>前<template></ul>" + fixtures.card("900") +
                 "</template></div>" + fixtures.card("901") + "</template>" + fixtures.card("100"))
        entries = sources.parse_qimao_page(fixtures.page(cards=cards), 1)
        self.assertEqual([item["book_id"] for item in entries], ["100"])
        # A hidden element is not a template parsing scope: a legitimate outer
        # end tag still closes it, so the following visible book remains usable.
        html = fixtures.page(cards="<div><span hidden>旧版</div>" + fixtures.card("100"))
        self.assertEqual([item["book_id"] for item in sources.parse_qimao_page(html, 1)], ["100"])

    def test_only_visible_template_sibling_reaches_research_without_changing_raw_source(self):
        import test_rank as ranks
        import test_research as research
        html = fixtures.page(cards="<template></ul>" + fixtures.card("900") + "</template>" + fixtures.card("100"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.html"
            path.write_text(html, encoding="utf-8")
            snapshot, raw = sources.collect_qimao(1, [path], [sources.QIMAO_URL.format(page=1)])
        decision = research.research.initialize(ranks.rank.build_sample([snapshot]))
        self.assertEqual([item["identity"]["value"] for item in decision["sample"]["books"]], ["100"])
        self.assertEqual(raw[1], html)


if __name__ == "__main__":
    unittest.main()

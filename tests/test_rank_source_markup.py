"""Inline markup must not alter the text used as ranking evidence."""
from pathlib import Path
import tempfile
import unittest

import test_rank_sources as fixtures


sources = fixtures.sources


class RankSourceMarkupTests(unittest.TestCase):
    def test_inline_formatting_preserves_titles_authors_and_categories(self):
        card = fixtures.card(author="陈<span>小<em>明</em></span>", category="东方<span>玄幻</span>")
        card = card.replace("样本&amp;书名", "<strong>样本</strong><span>&amp;</span>书名")
        entry = sources.parse_qimao_page(fixtures.page(cards=card), 1)[0]
        self.assertEqual(entry["title"], "样本&书名")
        self.assertEqual(entry["author"], "陈小明")
        self.assertEqual(entry["category"], "东方玄幻")

    def test_inline_formatting_preserves_page_controls_and_card_status(self):
        html = fixtures.page(cards=fixtures.card(status="连<span>载</span>中", words="12.<b>34</b>万字"),
                             sort="按<strong>点击</strong>量")
        entry = sources.parse_qimao_page(html, 1)[0]
        self.assertEqual(entry["status"], "连载中")
        self.assertEqual(entry["word_count"], 123400)
        with self.assertRaisesRegex(ValueError, "排序"):
            sources.parse_qimao_page(html.replace("点击", "收藏"), 1)
        with self.assertRaisesRegex(ValueError, "筛选矛盾"):
            sources.parse_qimao_page(html.replace("连<span>载</span>中", "已<span>完</span>结"), 1)

    def test_real_whitespace_is_preserved_and_inline_edges_do_not_add_it(self):
        for markup, expected in (("The <span>Last</span> Door", "The Last Door"),
                                 ("The<span> Last </span>Door", "The Last Door"),
                                 ("左<span>门</span>右", "左门右"),
                                 ("左 <span>门</span> 右", "左 门 右"),
                                 ("左<span>门</span><script>bad</script><style>bad</style>右", "左门右")):
            with self.subTest(markup=markup):
                card = fixtures.card().replace("样本&amp;书名", markup)
                self.assertEqual(sources.parse_qimao_page(fixtures.page(cards=card), 1)[0]["title"], expected)

    def test_explicit_line_and_block_boundaries_do_not_merge_words(self):
        for markup in ("左<br>右", "左<br/>右", "<div>左</div><div>右</div>", "左<p>右</p>"):
            with self.subTest(markup=markup):
                card = fixtures.card().replace("样本&amp;书名", markup)
                self.assertEqual(sources.parse_qimao_page(fixtures.page(cards=card), 1)[0]["title"], "左 右")

    def test_collected_inline_title_remains_selectable_in_research_handoff(self):
        import test_rank as rank_fixtures
        import test_research as research_fixtures

        card = fixtures.card().replace("样本&amp;书名", "样本<span>&amp;</span>书名")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.html"
            path.write_text(fixtures.page(cards=card), encoding="utf-8")
            snapshot, raw = sources.collect_qimao(1, [path], [sources.QIMAO_URL.format(page=1)])
        sample = rank_fixtures.rank.build_sample([snapshot], title_contains="样本&书名")
        decision = research_fixtures.research.initialize(sample)
        self.assertEqual(decision["sample"]["books"][0]["observation"]["title"], "样本&书名")
        self.assertEqual(raw[1], fixtures.page(cards=card))


if __name__ == "__main__":
    unittest.main()

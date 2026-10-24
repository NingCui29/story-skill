"""Static rank evidence must not include explicitly inert HTML subtrees."""
from pathlib import Path
import tempfile
import unittest

import test_rank_sources as fixtures

sources = fixtures.sources


class RankSourceVisibilityTests(unittest.TestCase):
    def test_template_and_hidden_cards_do_not_become_ranked_books(self):
        for wrapper in ('<template>{}</template>', '<div hidden>{}</div>',
                        '<div hidden="false">{}</div>', '<div hidden="until-found">{}</div>'):
            with self.subTest(wrapper=wrapper):
                html = fixtures.page(cards=wrapper.format(fixtures.card('900')) + fixtures.card('100'))
                entries = sources.parse_qimao_page(html, 1)
                self.assertEqual([(entry['book_id'], entry['rank']) for entry in entries], [('100', 1)])

    def test_inert_only_cards_are_not_an_empty_success(self):
        for wrapper in ('<template>{}</template>', '<div hidden>{}</div>'):
            with self.subTest(wrapper=wrapper), self.assertRaisesRegex(ValueError, '空榜成功'):
                sources.parse_qimao_page(fixtures.page(cards=wrapper.format(fixtures.card())), 1)

    def test_inert_sort_or_page_controls_cannot_supply_page_evidence(self):
        for wrapper in ('<template>{}</template>', '<span hidden>{}</span>'):
            for control, message in (('<a class="tab-inner active">按点击量</a>', '排序'),
                                     ('<a class="page-btn num active">1</a>', '页码')):
                with self.subTest(wrapper=wrapper, message=message), self.assertRaisesRegex(ValueError, message):
                    sources.parse_qimao_page(fixtures.page().replace(control, wrapper.format(control)), 1)

    def test_inert_duplicates_do_not_mask_visible_controls_or_card_fields(self):
        sort = '<a class="tab-inner active">按点击量</a>'
        page = '<a class="page-btn num active">1</a>'
        card = fixtures.card().replace('<span class="s-tit">',
            '<template><span class="s-tit"><a href="/shuku/900/">旧书名</a></span></template><span class="s-tit">')
        card = card.replace('<span class="s-author">', '<span class="s-author" hidden>旧作者</span><span class="s-author">')
        html = fixtures.page(cards=card).replace(sort, '<template>' + sort + '</template>' + sort)
        html = html.replace(page, '<div hidden>' + page + '</div>' + page)
        entry = sources.parse_qimao_page(html, 1)[0]
        self.assertEqual((entry['book_id'], entry['title'], entry['author']), ('100', '样本&书名', '样本作者'))

    def test_inert_text_preserves_inline_boundaries_and_visible_contradictions(self):
        card = fixtures.card(author='陈<span hidden>旧名</span><span>小明</span>',
                             category='东方<template>言情</template>玄幻',
                             status='连<template>已完结</template><span>载</span>中')
        card = card.replace('样本&amp;书名', '样本<template><div>旧版</div></template>&amp;<span hidden>广告</span>书名')
        entry = sources.parse_qimao_page(fixtures.page(cards=card), 1)[0]
        self.assertEqual((entry['title'], entry['author'], entry['category'], entry['status']),
                         ('样本&书名', '陈小明', '东方玄幻', '连载中'))
        with self.assertRaisesRegex(ValueError, '连载中筛选矛盾'):
            sources.parse_qimao_page(fixtures.page(cards=fixtures.card(status='<span hidden>连载中</span>已完结')), 1)
        with self.assertRaisesRegex(ValueError, '排序'):
            sources.parse_qimao_page(fixtures.page(sort='<template>按点击量</template>按收藏数'), 1)

    def test_aria_hidden_is_not_a_visual_visibility_signal(self):
        card = fixtures.card().replace('样本&amp;书名', '<span aria-hidden="true">样本</span>&amp;书名')
        self.assertEqual(sources.parse_qimao_page(fixtures.page(cards=card), 1)[0]['title'], '样本&书名')

    def test_collection_preserves_raw_html_and_only_visible_books_reach_research(self):
        import test_rank as ranks
        import test_research as research
        source = fixtures.page(cards='<template>' + fixtures.card('900') + '</template>' + fixtures.card('100'))
        for newline in ('\n', '\r\n'):
            with self.subTest(newline=repr(newline)):
                html = source.replace('\n', newline)
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'source.html'
                    path.write_bytes(html.encode('utf-8'))
                    snapshot, raw = sources.collect_qimao(1, [path], [sources.QIMAO_URL.format(page=1)])
                decision = research.research.initialize(ranks.rank.build_sample([snapshot]))
                self.assertEqual([book['identity']['value'] for book in decision['sample']['books']], ['100'])
                self.assertEqual(raw[1], html)
                self.assertIn('未执行 JavaScript 或计算 CSS', ' '.join(snapshot['source']['limitations']))


if __name__ == '__main__':
    unittest.main()

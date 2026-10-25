"""Offline Fanqie pagination completeness. No browser or platform access."""
import unittest
from unittest.mock import patch

from tests import test_platform_browser as fixtures
from tests.test_platform_browser_preview import configuration


class FanqiePaginationTests(unittest.TestCase):
    def setUp(self):
        self.old_api = fixtures.browser.api
        fixtures.browser.api = None
        self.addCleanup(setattr, fixtures.browser, 'api', self.old_api)

    def opened(self, size=31, change=None):
        config = configuration()
        drafts, published = config['chapter_lists']
        drafts['count'] = fixtures.field('#count', format='draft_count')
        for listing in config['chapter_lists']:
            listing['pagination'] = {
                'mode': 'fanqie_pages_v1', 'validated': True,
                'container': fixtures.field('#scroll'), 'previous': fixtures.field('#previous'),
                'current': fixtures.field('#page'), 'next': fixtures.field('#next'),
                'disabled_class': 'arco-pagination-item-disabled', 'reject_selectors': ['.forbidden']}
        rows = [('published-' + str(number), number, '已发布') for number in range(1, size + 1)]
        pages = [rows[index:index + 15] for index in range(0, len(rows), 15)] or [[]]
        adapter = fixtures.BrowserTests().opened(config, lists={drafts['url']: [[]], published['url']: pages})
        page = adapter._list_page
        lookup = page._lookup
        page.pagination_changed = False

        def controls(selector):
            if selector in ('#previous', '#page', '#next'):
                if page.url != published['url'] or len(pages) <= 1:
                    return []
                changed = page.pagination_changed
                if (change == 'appears' and not changed) or (change == 'disappears' and changed):
                    return []
                if selector == '#page':
                    return [fixtures.Node(str(2 if change == 'page' and changed else page.list_index + 1))]
                disabled = page.list_index == 0 if selector == '#previous' else page.list_index == len(pages) - 1
                if selector == '#next' and change in ('next_enabled', 'next_disabled'):
                    disabled = not changed if change == 'next_enabled' else changed
                def move():
                    page.list_index += -1 if selector == '#previous' else 1
                return [fixtures.Node(attrs={'class': 'arco-pagination-item-disabled' if disabled else ''}, click=move)]
            return lookup(selector)

        page._lookup = controls
        adapter.verify_target('fanqie', 'author-1', '100')
        return adapter

    def test_stable_pages_are_exhausted_and_a_later_published_chapter_blocks_creation(self):
        for size in (1, 15, 16, 31):
            with self.subTest(size=size):
                adapter = self.opened(size)
                self.assertEqual(adapter.find_drafts(size + 1), [])
                with self.assertRaises(fixtures.browser.BrowserAdapterError) as caught:
                    adapter.find_drafts(size)
                self.assertEqual(caught.exception.code, 'platform_browser_chapter_exists_not_draft')
                self.assertEqual(adapter._context.created_blank, 0)
                self.assertFalse(any(event[0] == 'fill' or event == ('click', '#save') for event in adapter._context.events))

    def test_pagination_changes_while_scrolling_cannot_hide_later_published_chapters(self):
        evaluate = fixtures.Locator.evaluate
        for change in ('appears', 'disappears', 'next_enabled', 'next_disabled', 'page'):
            with self.subTest(change=change):
                adapter = self.opened(change=change)
                def scroll(locator, expression, *args):
                    result = evaluate(locator, expression, *args)
                    if 'el.scrollTop=bottom' in expression and locator.page.url.endswith('/submitted'):
                        locator.page.pagination_changed = True
                    return result
                with patch.object(fixtures.Locator, 'evaluate', scroll):
                    with self.assertRaises(fixtures.browser.BrowserAdapterError) as caught:
                        adapter.find_drafts(31)
                self.assertEqual(caught.exception.code, 'platform_browser_incomplete_list')
                self.assertTrue(adapter._list_page.pagination_changed)
                self.assertEqual(adapter._context.created_blank, 0)
                self.assertFalse(any(event[0] == 'fill' or event == ('click', '#save') for event in adapter._context.events))


if __name__ == '__main__':
    unittest.main()

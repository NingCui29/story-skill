"""Draft links retain their effective target when document base URI changes."""
import unittest
from unittest.mock import patch
from tests import test_platform_browser as fixture
from tests import test_platform_browser_editor_readback as reader


class DraftLinkBaseTests(unittest.TestCase):
    def setUp(self):
        self.old_api = fixture.browser.api
        fixture.browser.api = None
        self.addCleanup(setattr, fixture.browser, 'api', self.old_api)

    def scenario(self, platform, operation, href, changed_base):
        adapter = reader.EditorReadbackTests().opened(platform, listing=operation != 'save', sync_drafts=operation != 'save')
        self.addCleanup(adapter.close)
        if operation == 'same_page':
            adapter.profile['create_action']['opens'] = 'same_page'
            adapter._context.options['creation_opens'] = 'same_page'
        selector = '#save' if operation == 'save' else '#create'
        origin = adapter.profile['origin']
        adapter._context.options['base_url'] = origin + '/draft/'
        attribute, checked = fixture.Locator.get_attribute, adapter._checked_editor_action
        changed, destinations = [], []
        click = fixture.Locator.click
        def read(locator, name):
            if locator.selector == selector and href is not None:
                locator.node().attrs['href'] = href
                locator.node().tag = 'A'
            return attribute(locator, name)
        def action(locator, name, before_write, *args, **kwargs):
            def before():
                before_write()
                if locator.selector == selector and changed_base and not changed:
                    changed.append(True)
                    adapter._context.options['base_url'] = origin + '/publish/'
            return checked(locator, name, before, *args, **kwargs)
        def clicked(locator):
            if locator.selector == selector:
                destinations.append(fixture.action_link(locator.node(), locator.page)['resolvedHref'])
            return click(locator)
        error, remote = None, None
        with patch.object(fixture.Locator, 'get_attribute', read), patch.object(adapter, '_checked_editor_action', action), patch.object(fixture.Locator, 'click', clicked):
            try:
                remote = adapter.create_draft(2, '新标题', '新正文')
            except fixture.browser.BrowserAdapterError as caught:
                error = caught.code
        return error, remote, changed, destinations

    def test_relative_link_base_change_stops_before_actual_click(self):
        for platform in ('qimao', 'qidian'):
            for operation in ('save', 'same_page', 'popup'):
                with self.subTest(platform=platform, operation=operation):
                    error, remote, changed, destinations = self.scenario(platform, operation, 'next', True)
                    self.assertEqual(changed, [True])
                    self.assertEqual(destinations, [])
                    self.assertIsNone(remote)
                    self.assertEqual(error, 'platform_browser_unsafe_' + ('save' if operation == 'save' else 'create'))

    def test_unchanged_effective_destination_keeps_normal_creation_usable(self):
        for platform in ('qimao', 'qidian'):
            for operation in ('save', 'same_page', 'popup'):
                origin = fixture.profile(platform)['origin']
                for href, changed_base in (('next', False), (None, True), ('/draft/next', True), (origin + '/draft/next', True)):
                    with self.subTest(platform=platform, operation=operation, href=href, changed_base=changed_base):
                        error, remote, _, destinations = self.scenario(platform, operation, href, changed_base)
                        self.assertIsNone(error)
                        self.assertEqual(remote, 'new-1' if operation == 'save' else 'empty-1')
                        self.assertEqual(len(destinations), 1)

    def test_same_draft_url_alias_keeps_self_links_but_rejects_base_override(self):
        for platform in ('qimao', 'qidian'):
            for href in ('', '#save', '\t#save\r\n', ' \t', '\x01#save\x01', '\u00a0#save', '\u3000#save', '\u00a0'):
                for override in (False, True):
                    with self.subTest(platform=platform, href=href, override=override):
                        config = fixture.BrowserTests().url_guard_profile(platform)
                        alias = config['origin'] + '/fixture/{book_id}/modify/{remote_id}?from=edit'
                        config['editor']['remote_id']['templates'].append(alias)
                        config['identity']['book']['templates'].append(alias)
                        adapter = fixture.BrowserTests().opened(config)
                        self.addCleanup(adapter.close)
                        adapter.verify_target(platform, 'author-1', '100')
                        fill, attr, checked = fixture.Locator.fill, fixture.Locator.get_attribute, adapter._checked_editor_action
                        changed = []
                        def filled(locator, value):
                            result = fill(locator, value)
                            if locator.selector == '#chapter':
                                locator.page.context.persist(locator.page)
                                locator.page.url = config['urls']['edit'].replace('{remote_id}', 'new-1')
                            return result
                        def attribute(locator, name):
                            if locator.selector == '#save':
                                locator.node().attrs['href'] = href
                                locator.node().tag = 'A'
                            return attr(locator, name)
                        def action(locator, name, before_write, *args, **kwargs):
                            def before():
                                before_write()
                                if locator.selector == '#save' and not changed:
                                    locator.page.url = alias.replace('{book_id}', '100').replace('{remote_id}', 'new-1')
                                    if override:
                                        locator.page.context.options['base_url'] = config['origin'] + '/publish/'
                                    changed.append(True)
                            return checked(locator, name, before, *args, **kwargs)
                        error, remote = None, None
                        with patch.object(fixture.Locator, 'fill', filled), patch.object(fixture.Locator, 'get_attribute', attribute), patch.object(adapter, '_checked_editor_action', action):
                            try:
                                remote = adapter.create_draft(2, '新标题', '新正文')
                            except fixture.browser.BrowserAdapterError as caught:
                                error = caught.code
                        self.assertEqual(changed, [True])
                        rejected = override or href not in ('', '#save', '\t#save\r\n', ' \t', '\x01#save\x01')
                        self.assertEqual(adapter._context.events.count(('click', '#save')), 0 if rejected else 1)
                        self.assertEqual(remote, None if rejected else 'new-1')
                        self.assertEqual(error, 'platform_browser_unsafe_save' if rejected else None)


if __name__ == '__main__':
    unittest.main()

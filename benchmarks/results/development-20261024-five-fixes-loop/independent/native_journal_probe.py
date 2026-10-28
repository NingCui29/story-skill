"""Exercise the real native adapter and durable journal together on fake DOMs."""
from pathlib import Path
import hashlib
import json
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from tests import test_platform_browser as dom
from tests import test_platform_drafts as ledger

def scenario(platform, mode):
    fixture = ledger.fixtures.PublishTests()
    fixture.setUp()
    adapter = None
    try:
        fixture.commit()
        plan = fixture.prepare(platform=platform)
        config = dom.profile(platform)
        adapter = dom.BrowserTests().opened(config, account='author-one', book='book-one', sync_drafts=True)
        before = fixture.writing_snapshot()
        state = fixture.root.parent / 'target-state'
        state.mkdir(mode=0o700)
        original = dom.Locator.fill
        triggered = []
        def fill(locator, value):
            result = original(locator, value)
            if locator.selector == '#chapter' and mode != 'normal' and not triggered:
                triggered.append(True)
                if mode == 'published':
                    locator.page.nodes['#state'][0].text = '已发布'
                elif mode == 'missing_state':
                    locator.page.nodes['#state'] = []
                elif mode == 'duplicate_state':
                    locator.page.nodes['#state'].append(dom.Node('草稿'))
                elif mode == 'unbound_url_change':
                    locator.page.url = config['origin'] + '/fixture/changed-without-id'
            return result
        with patch.object(dom.Locator, 'fill', fill), patch.object(ledger.drafts, '_lock_directory', return_value=state):
            first = ledger.drafts.run_plan(fixture.book, plan['id'], adapter, execute=True)
            initial_events = list(adapter._context.events)
            if mode == 'normal':
                # The shared fake only auto-syncs a drafts-only list; this profile
                # uses an all-chapters list. Materialize its saved remote record.
                remote_id = first['results'][0]['remote_id']
                saved = adapter._context.editors[remote_id]
                adapter._context.lists[config['urls']['drafts']] = [[(remote_id, saved['chapter'], '草稿')]]
            second = ledger.drafts.run_plan(fixture.book, plan['id'], adapter, execute=True)
        events = adapter._context.events
        assert fixture.writing_snapshot() == before
        assert events.count(('goto', config['urls']['create'])) == 1, events
        if mode == 'normal':
            assert first['ok'] and second['ok'], (first, second)
            assert first['results'][0]['state'] == 'saved'
            assert second['results'][0]['state'] == 'reconciled'
            assert events.count(('click', '#save')) == 1
        else:
            assert not first['ok'] and not second['ok'], (first, second)
            assert first['results'][0]['state'] == second['results'][0]['state'] == 'unknown'
            assert [row for row in events if row[0] == 'fill'] == [('fill', '#chapter')], events
            assert not any(row[0] == 'click' for row in events)
            assert [row for row in events[len(initial_events):] if row[0] in ('fill', 'click')] == []
        return {'platform': platform, 'case': mode, 'first': first['results'][0]['state'],
                'second': second['results'][0]['state'], 'create_entries': 1,
                'field_writes': len([row for row in events if row[0] == 'fill']),
                'save_clicks': events.count(('click', '#save')), 'creative_state_unchanged': True, 'ok': True}
    finally:
        if adapter is not None:
            adapter.close()
        fixture.tearDown()

rows = [scenario(platform, mode) for platform in ('qimao', 'qidian')
        for mode in ('normal', 'published', 'missing_state', 'duplicate_state', 'unbound_url_change')]
paths = ['skills/story-skill/scripts/' + name for name in
         ('story_platform_browser.py', 'story_platform_drafts.py', 'story_publish.py')]
report = {'ok': True, 'cases': len(rows), 'results': rows,
          'source_sha256': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths},
          'scope': 'Production native adapter plus durable journal on isolated synthetic books and fake DOM transport; no platform/account or native Playwright session.'}
(OUT / 'native-journal.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'ok': True, 'cases': len(rows)}))

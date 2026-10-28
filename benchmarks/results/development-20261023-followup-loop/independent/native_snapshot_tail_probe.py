"""Check whether final selector-validation RPCs can hide a later body change."""
from pathlib import Path
import hashlib
import json
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT))
import test_platform_browser_editor_readback as helpers

fixture, browser = helpers.fixture, helpers.browser
results = []
for platform in ('qimao', 'qidian'):
    for mutate in (False, True):
        helper = helpers.EditorReadbackTests()
        helper.setUp()
        adapter = helper.opened(platform)
        changed = []
        original = fixture.Locator.evaluate_all
        def evaluate_all(locator, expression, argument=None):
            value = original(locator, expression, argument)
            if (mutate and not changed and locator.selector == '#book'
                    and 'elements[0] === expected' in expression and locator.page.kind == 'edit'):
                locator.page.nodes['#body'][0].value = '最终选择器核验期间改变的正文'
                changed.append(locator.page)
            return value
        accepted = None
        error = None
        try:
            with patch.object(fixture.Locator, 'evaluate_all', evaluate_all):
                accepted = adapter.read_draft('900')
        except browser.BrowserAdapterError as exc:
            error = exc.code
        finally:
            helper.tearDown()
        results.append({'platform': platform, 'mutate': mutate, 'mutation_observed': bool(changed),
                        'accepted': accepted is not None, 'body_returned': accepted and accepted['body'],
                        'current_body': changed[0].nodes['#body'][0].value if changed else None,
                        'error': error, 'expected_acceptance': not mutate,
                        'writes': [row for row in adapter._context.events if row[0] in ('fill','click')]})
data = {'ok': all(row['accepted'] == row['expected_acceptance'] for row in results), 'results': results,
        'source_sha256': hashlib.sha256((ROOT/'skills/story-skill/scripts/story_platform_browser.py').read_bytes()).hexdigest(),
        'scope': 'Production adapter read_draft with offline fixture and mutation during later selector-check callback; no real platform.'}
(OUT/'native-snapshot-tail-result.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(data,ensure_ascii=False,indent=2))
raise SystemExit(0 if data['ok'] else 1)

"""Compare static rank extraction with rendered text in isolated local Chrome."""
from pathlib import Path
import hashlib
import html
import json
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT)]
import test_rank_sources as fixtures

BROWSER = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
wrappers = ('<template>{}</template>', '<span hidden>{}</span>',
            '<span hidden="false">{}</span>', '<span hidden="until-found">{}</span>',
            '<div><template><span>{}</span></template></div>', '<div hidden="until-found">{}</div>',
            '<span hidden style="display:inline">{}</span>')
cases = []
for wrapper in wrappers:
    visible = fixtures.card('100').replace('样本&amp;书名', '可见书名')
    inert = fixtures.card('900').replace('样本&amp;书名', '隐藏整卡标记')
    markup = fixtures.page(cards=wrapper.format(inert) + visible)
    rendered_visible = wrapper in ('<span hidden="until-found">{}</span>', '<span hidden style="display:inline">{}</span>')
    cases.append((markup, '隐藏整卡标记', rendered_visible, ['可见书名']))
    inline = fixtures.card('100').replace('样本&amp;书名', '可见' + wrapper.format('隐藏行内标记') + '书名')
    title = '可见 书名' if wrapper == '<div><template><span>{}</span></template></div>' else '可见书名'
    cases.append((fixtures.page(cards=inline), '隐藏行内标记', rendered_visible, [title]))
for attribute in ('aria-hidden="true"', 'aria-hidden="false"'):
    inline = fixtures.card('100').replace('样本&amp;书名', '<span ' + attribute + '>仍然可见</span>书名')
    cases.append((fixtures.page(cards=inline), '仍然可见', True, ['仍然可见书名']))

results = []
with tempfile.TemporaryDirectory(prefix='story-rank-local-dom-') as temp:
    folder = Path(temp)
    for index, (markup, marker, visible, expected) in enumerate(cases):
        file = folder / 'probe.html'
        # Record before inserting the result so assertions do not observe themselves.
        script = '''<script>const t=document.body.innerText;const p=document.createElement('pre');p.id='rank-result';p.textContent=JSON.stringify({text:t,engine:navigator.userAgent});document.body.append(p);</script>'''
        file.write_text('<!doctype html><meta charset="utf-8"><body>' + markup + script, encoding='utf-8')
        run = subprocess.run([BROWSER, '--headless=new', '--disable-gpu', '--disable-background-networking',
            '--disable-component-update', '--disable-sync', '--no-first-run', '--no-default-browser-check',
            '--disable-extensions', '--metrics-recording-only', '--host-resolver-rules=MAP * 0.0.0.0',
            '--user-data-dir=' + str(folder / 'profile'), '--dump-dom', '--virtual-time-budget=1000', file.as_uri()],
            capture_output=True, text=True, timeout=30, check=True)
        match = re.search(r'<pre id="rank-result">(.*?)</pre>', run.stdout, re.S)
        assert match, run.stderr[-1000:]
        rendered = json.loads(html.unescape(match.group(1)))
        actual = [entry['title'] for entry in fixtures.sources.parse_qimao_page(markup, 1)]
        ok = (marker in rendered['text']) == visible and actual == expected
        results.append({'case': index + 1, 'ok': ok, 'marker_visible': marker in rendered['text'],
                        'expected_visible': visible, 'titles': actual, 'expected_titles': expected})
source = 'skills/story-skill/scripts/story_rank_sources.py'
report = {'ok': all(row['ok'] for row in results), 'cases': len(results), 'results': results,
          'engine': rendered['engine'], 'source_sha256': {source: hashlib.sha256((ROOT / source).read_bytes()).hexdigest()},
          'scope': 'Local synthetic HTML rendered in an isolated Chrome profile; no network or live rank page.',
          'limit_observed': 'Production deliberately excludes hidden attributes without CSS computation. Chrome may render inline hidden=until-found content and explicit display overrides; extraction is conservative static evidence, not exact rendered visibility.',
          'initial_oracle_correction': 'Initial probe incorrectly treated inline until-found as visually hidden and omitted a real block boundary around an empty template; initial JSON is preserved. No production changes were made to match the oracle.'}
(OUT / 'rank-visibility-dom.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'ok': report['ok'], 'cases': len(results)}))
assert report['ok'], report

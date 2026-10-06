#!/usr/bin/env python3
"""Serve only the two synthetic acceptance books, with a memory-only probe.

This developer fixture never dispatches input, recovery or save actions. Normal
service records and leases are still owned by the existing workbench lifecycle.
Importing this file does not start a server.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[2]
FIXTURE = HERE / 'fixture.json'
PROBE_MARKER = '【合成单窗口缓存验收：仅内存未保存探针，不属于真实小说，不写文件】'
PROBE_SCRIPT = r'''
;
(() => {
  if (window.__singleWindowFixtureProbe?.ready) return;
  const probe = window.__singleWindowFixtureProbe = {ready:false,synthetic:true};
  let attempts = 0;
  const prepare = () => {
    const d = typeof docs !== 'undefined' && typeof active !== 'undefined' ? docs.get(active) : null;
    if (!d || d.kind !== 'formal' || !d.editable || typeof d.text !== 'string') {
      if (++attempts < 300) { setTimeout(prepare, 50); return; }
      probe.error = '合成正式稿未在限定时间内就绪';
      return;
    }
    d.value = d.text + '\n\n' + __MARKER__;
    d.editing = true;
    d.saving = true;
    show(d);
    badge();
    const editor = $('text');
    editor.setSelectionRange(5, 13);
    editor.focus({preventScroll:true});
    Object.assign(probe, {ready:true,document:d.id,saving:true,
                         selectionStart:5,selectionEnd:13,marker:__MARKER__});
  };
  prepare();
})();
'''.replace('__MARKER__', json.dumps(PROBE_MARKER, ensure_ascii=False))


def load_fixture():
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    if fixture.get('synthetic') is not True or len(fixture.get('books', [])) != 2:
        raise ValueError('Only the two synthetic fixture books are allowed')
    for entry in fixture['books']:
        root = entry['book_root']
        if not Path(root).is_absolute() or str(Path(root).resolve()) != root or not entry.get('book_id'):
            raise ValueError('Fixture roots must be exact canonical absolute paths')
    if len({entry['book_root'] for entry in fixture['books']}) != 2:
        raise ValueError('Fixture book roots must be distinct')
    return fixture


def load_story():
    script = REPOSITORY / 'skills/story-skill/scripts/story.py'
    spec = importlib.util.spec_from_file_location('single_window_synthetic_story', script)
    story = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(story)
    return story


def probe_page(original, packet, reading, token, first_root, allowed_roots):
    root = packet.get('book', {}).get('root')
    if root not in allowed_roots:
        raise ValueError('Refusing to render a non-fixture book')
    page = original(packet, reading, token)
    if root != first_root:
        return page
    html = page.decode('utf-8')
    scripts = list(re.finditer(r'<script>(.*?)</script>', html, re.DOTALL))
    if len(scripts) != 1:
        raise ValueError('Fixture requires one existing inline editor script')
    match = scripts[0]
    original_script = match.group(1)
    original_digest = base64.b64encode(hashlib.sha256(original_script.encode()).digest()).decode()
    old_policy = "script-src 'sha256-" + original_digest + "'"
    if html.count(old_policy) != 1:
        raise ValueError('Original editor script does not match its CSP')
    script = original_script + PROBE_SCRIPT
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    html = html[:match.start(1)] + script + html[match.end(1):]
    html = html.replace(old_policy, "script-src 'sha256-" + digest + "'", 1)
    return html.encode('utf-8')


def install_probe(story, fixture):
    roots = {entry['book_root'] for entry in fixture['books']}
    first = fixture['books'][0]['book_root']
    original = story.workbench._editor_page

    def wrapped(packet, reading, token):
        return probe_page(original, packet, reading, token, first, roots)

    story.workbench._editor_page = wrapped
    return original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--book', required=True)
    parser.add_argument('--library-book', action='append', default=[])
    parser.add_argument('--limit', type=int, default=10)
    args = parser.parse_args()
    fixture = load_fixture()
    entries = {entry['book_root']: entry for entry in fixture['books']}
    if args.book not in entries or any(root not in entries for root in args.library_book):
        parser.error('Only exact roots from this private fixture.json are allowed')
    if not 1 <= args.limit <= 100 or len(args.library_book) > 2:
        parser.error('Fixture limit must be 1..100, with at most two library books')
    story = load_story()
    for root in {args.book, *args.library_book}:
        story.workbench._library_entry(root, entries[root])
    install_probe(story, fixture)
    sys.argv = [str(REPOSITORY / 'skills/story-skill/scripts/story.py'),
                'workbench-serve', '--book', args.book, '--limit', str(args.limit),
                '--expected-book-id', entries[args.book]['book_id']]
    for root in args.library_book:
        sys.argv += ['--library-book', root]
    return story.main()


if __name__ == '__main__':
    raise SystemExit(main())

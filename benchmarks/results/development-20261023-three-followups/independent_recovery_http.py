"""Exercise both real local HTTP recovery endpoints against a synthetic book."""
import hashlib
import http.client
import json
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import test_workbench as base
import test_workbench_library as library


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    fixture = base.WorkbenchTests('runTest')
    fixture.setUp()
    shelf_fixture = library.WorkbenchLibraryTests('runTest')
    shelf_fixture.setUp()
    w = base.story.workbench
    old_cursors = set(w._material_sessions)
    server = thread = None
    try:
        root = fixture.root
        (root / '素材.md').write_text('HTTP 验收的原始材料。', encoding='utf-8')
        history = root / '.story/drafts/000-无关历史'
        history.mkdir()
        for index in range(5001):
            (history / f'{index:05}.txt').write_text('历史候选原文。', encoding='utf-8')
        source = w._editor_document(root, 'file:素材.md')
        saved = []
        for index in range(103):
            saved.append(w._editor_save(root, {**source, 'text': f'恢复材料 {index}，保留原文与来源。',
                'recovery_key': f'independent-http-{index}'}, recovery=True))
        first_path = root / saved[0]['path']
        meta = json.loads(Path(str(first_path) + '.meta.json').read_text(encoding='utf-8'))
        pending_name, _ = w._start_pending_save(root, '.story/drafts/workbench/中断材料.txt',
            '保存中断时已经落盘的材料。', '保存中断时已经落盘的材料。', meta)
        expected = {row['id'] for row in saved} | {'pending:' + pending_name}
        protected = {path: digest(path) for path in root.rglob('*')
                     if path.is_file() and (path.suffix in ('.txt', '.md') or
                                           path.name.endswith(('.meta.json', '.pending.json')))}
        authoritative = fixture.authoritative_state()

        server = w.editor_server(root)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        thread.start()
        route = '/' + server.editor_url.split('/')[-2] + '/'
        headers = {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{server.server_port}',
                   'X-Story-Token': route.strip('/')}

        def editor_post(action, payload, extra=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            try:
                connection.request('POST', route + 'api/' + action, json.dumps(payload), headers | (extra or {}))
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        shelf = shelf_fixture.start(books=[str(root)])
        status, groups = shelf_fixture.post(shelf, 'recoveries')
        assert status == 200, groups
        assert len(groups['books']) == 1 and groups['books'][0]['deferred'] is True, groups
        key = groups['books'][0]['key']

        def shelf_post(payload):
            status, packet = shelf_fixture.post(shelf, 'recoveries', {'key': key, **payload})
            assert status == 200, packet
            assert len(packet['books']) == 1, packet
            return packet['books'][0]

        def editor_page(payload):
            status, packet = editor_post('recoveries', payload)
            assert status == 200, packet
            return packet

        def collect(call):
            cursor, offset, ids, pages = None, 0, [], []
            for _ in range(20):
                packet = call({'cursor': cursor, 'offset': offset})
                current = [row['id'] for row in packet['recoveries']]
                assert len(current) <= 100
                assert not set(current).intersection(ids), current
                ids.extend(current)
                pages.append({key: packet[key] for key in ('offset', 'next_offset', 'has_more', 'complete', 'count', 'scan')})
                pages[-1]['returned'] = len(current)
                if not packet['has_more']:
                    assert packet['complete'] and not packet['scan']['hard_limit'], packet
                    break
                cursor, offset = packet['cursor'], packet['next_offset']
            else:
                raise AssertionError('Recovery pagination did not terminate')
            assert set(ids) == expected, {'missing': sorted(expected - set(ids)), 'extra': sorted(set(ids) - expected)}
            return pages

        editor_pages = collect(editor_page)
        shelf_pages = collect(shelf_post)
        status, opened = editor_post('open', {'id': saved[-1]['id']})
        assert status == 200 and opened['text'] == '恢复材料 102，保留原文与来源。', opened
        assert opened['source'] == 'file:素材.md' and opened['content_kind'] == 'material', opened
        status, pending = editor_post('open', {'id': 'pending:' + pending_name})
        assert status == 200 and pending['needs_recovery'] is True, pending
        assert pending['text'] == '保存中断时已经落盘的材料。', pending
        refused, _ = editor_post('recoveries', {'cursor': None, 'offset': 0}, {'X-Story-Token': 'foreign-token'})
        assert refused == 403
        assert fixture.authoritative_state() == authoritative
        assert {path: digest(path) for path in protected} == protected
        report = {'successful': True, 'scope': 'Synthetic book through actual local HTTP servers; no real author books or platform',
                  'unrelated_history_files': 5001, 'recovery_files_saved_with_production_entry': 103,
                  'pending_records': 1, 'expected_recovery_count': len(expected),
                  'shelf_initial_request_deferred': True, 'editor_pages': editor_pages, 'shelf_pages': shelf_pages,
                  'open_recovery_preserves_material_source': True, 'open_pending_preserves_text': True,
                  'foreign_token_status': refused, 'formal_state_and_all_protected_files_unchanged': True}
        (OUTPUT / 'independent-recovery-http.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({key: report[key] for key in ('successful', 'expected_recovery_count', 'foreign_token_status',
                                                       'formal_state_and_all_protected_files_unchanged')}, ensure_ascii=False))
    finally:
        if server is not None:
            if thread is not None and thread.is_alive():
                server.shutdown()
                thread.join(timeout=5)
            server.server_close()
        shelf_fixture.doCleanups()
        for cursor in set(w._material_sessions) - old_cursors:
            w._material_discard(cursor)
        fixture.doCleanups()


if __name__ == '__main__':
    main()

"""Large material histories must not hide formal progress or weaken file access."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

import test_workbench as base


class WorkbenchMaterialScanTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.w = base.story.workbench
        existing = set(self.w._material_sessions)
        self.addCleanup(lambda: [self.w._material_discard(cursor)
                                 for cursor in set(self.w._material_sessions) - existing])

    def file(self, relative, text='材料原文。'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return 'file:' + relative

    def file_state(self):
        # Preserve every historical file as well as the authoritative databases;
        # a logical DB comparison alone would miss a rewrite or renamed backup.
        return {
            path.relative_to(self.root).as_posix(): (
                path.stat().st_mtime_ns, path.stat().st_size,
                hashlib.sha256(path.read_bytes()).hexdigest())
            for path in self.root.rglob('*') if path.is_file() and not path.is_symlink()
        }

    def assert_formal_progress(self, catalog):
        self.assertEqual({row['id'] for row in catalog['chapters']}, {'formal:1', 'formal:2'})
        workspace = catalog['workspace']
        self.assertEqual(workspace['formal_total'], 2)
        self.assertEqual(workspace['planned_total'], 2)
        self.assertEqual(workspace['next_chapter'], 3)
        self.assertTrue(workspace['kind_verified'])

    def pending(self):
        source = self.w._editor_document(self.root, 'formal:1')
        name = '.story/drafts/workbench/第1章 保存中断.md'
        text = '中断时已经写入恢复记录的文字。'
        meta = {
            'source': source['id'], 'chapter': 1, 'content_kind': 'prose',
            'base_sha256': source['sha256'], 'source_sha256': source['sha256'],
            'prefix': source['prefix'], 'adopted': False,
        }
        pending_name, _ = self.w._start_pending_save(
            self.root, name, text, source['prefix'] + text, meta)
        return 'pending:' + pending_name, text

    def ui(self, source, assertions):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required for material pagination UI checks')
        result = subprocess.run(
            [node, '-'], input="const assert=require('node:assert/strict');\n" + source + '\n' + assertions,
            capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_loading_more_materials_preserves_current_unsaved_editor(self):
        page = self.w._editor_page(self.w.snapshot(self.fixture.book), {}, 'material-ui-test').decode()
        script = page.split('<script>', 1)[1].split('</script>', 1)[0]
        source = script[script.index('async function catalog('):script.index('async function recover(')]
        self.ui(r"""
const elements={},$=id=>elements[id]||(elements[id]={value:'',hidden:false,disabled:false,textContent:'',
 parentElement:{scrollTop:19},querySelectorAll:()=>[],replaceChildren(){}});
let active='formal:1',loading=false,reloadPending=false,pendingFocus=false,currentCatalog=null;
let viewMode='chapters',offset=0,queryTimer=null;
const original={id:active,text:'正式原文。',value:'尚未保存的新文字。',editing:true,positions:{edit:37}};
const docs=new Map([[active,original]]),before=JSON.stringify([...docs]);$('text').value=original.value;
const requests=[],notes=[],chapterNumber=()=>1,locateActive=()=>{},badge=()=>{};
const drawCatalog=c=>currentCatalog=c,note=message=>notes.push(message);
const openDoc=()=>{throw Error('Pagination cannot reopen the current document');};
async function call(action,payload){
 assert.equal(action,'catalog');requests.push(payload);
 const complete=requests.length>1;
 return {materials:{cursor:'a'.repeat(64),loaded:complete?7:3,complete,has_more:!complete,hard_limit:false},
  warnings:complete?[]:['材料尚未全部加载'],chapters:[],files:[],workspace:{groups:[]}};
}
""" + source, r"""
(async()=>{
 await catalog();assert.equal(requests[0].material_cursor,null);assert.equal(requests[0].material_more,false);
 assert.equal($('materials-more').hidden,false);assert.ok($('materials-status').textContent.includes('尚有材料未加载'));
 await catalog(false,false,true);
 assert.equal(requests[1].material_cursor,'a'.repeat(64));assert.equal(requests[1].material_more,true);
 assert.equal($('materials-more').hidden,true);assert.ok($('materials-status').textContent.includes('扫描已完成'));
 assert.equal(JSON.stringify([...docs]),before);assert.equal(active,'formal:1');
 assert.equal($('text').value,'尚未保存的新文字。');assert.deepEqual(notes,[]);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")

    def test_successful_candidate_save_refreshes_material_inventory_for_new_file(self):
        page = self.w._editor_page(self.w.snapshot(self.fixture.book), {}, 'material-ui-save').decode()
        script = page.split('<script>', 1)[1].split('</script>', 1)[0]
        source = script[script.index('async function saveDocument('):script.index("$('save').onclick=")]
        self.ui(r"""
let active='formal:1',reloading=false;
const original={id:active,text:'正式原文。',value:'本次保存的候选文字。',editable:true,editing:true,positions:{edit:37}};
const docs=new Map([[active,original]]),requests=[],catalogRequests=[];
const dirty=d=>d?.editable&&d.value!==d.text,badge=()=>{},recover=async()=>{},rememberView=()=>{},note=()=>{};
const show=d=>active=d.id,location={},crypto={randomUUID:()=> 'new-recovery-key'};
async function call(action,payload){
 requests.push({action,payload});
 if(action==='save')return {id:'file:.story/drafts/workbench/新候选.md'};
 if(action==='open')return {id:payload.id,text:'本次保存的候选文字。',editable:true};
 throw Error('Unexpected action '+action);
}
async function catalog(){catalogRequests.push(catalog.materialCursor);}
catalog.materialCursor='old-complete-material-inventory';
""" + source, r"""
(async()=>{
 assert.equal(await saveDocument(original),true);
 assert.deepEqual(catalogRequests,[null]);
 assert.equal(requests[0].payload.text,'本次保存的候选文字。');
 assert.equal(active,'file:.story/drafts/workbench/新候选.md');
 assert.equal(docs.get(active).value,'本次保存的候选文字。');
 assert.equal(docs.get(active).editing,true);assert.deepEqual(docs.get(active).positions,{edit:37});
 assert.equal(original.text,'正式原文。');
})().catch(error=>{console.error(error);process.exitCode=1;});
""")

    def test_history_larger_than_old_limit_pages_without_losing_formal_progress_or_files(self):
        history = self.root / '.story/drafts/20261019_rule_check/before'
        history.mkdir(parents=True)
        paths = set()
        for index in range(5010):
            path = history / f'{index:05d}.txt'
            path.write_text(f'第{index}份历史备份，保留原文。', encoding='utf-8')
            paths.add(path.relative_to(self.root).as_posix())
        pending_id, pending_text = self.pending()
        before = self.file_state(), self.fixture.authoritative_state()

        catalog = self.w._editor_catalog(self.root)
        self.assert_formal_progress(catalog)
        scan = catalog['materials']
        self.assertFalse(scan['complete'])
        self.assertTrue(scan['has_more'])
        self.assertFalse(scan['hard_limit'])
        self.assertLessEqual(scan['inspected'], self.w.AUTHOR_LIMIT)
        self.assertTrue(catalog['warnings'])
        cursor = scan['cursor']

        cached = self.w._editor_catalog(self.root, material_cursor=cursor)
        self.assertEqual(cached['materials'], scan)
        self.assertEqual(cached['related_files'], catalog['related_files'])
        previous_ids = {row['id'] for row in catalog['related_files']}
        previous_inspected = scan['inspected']
        for _ in range(10):
            if catalog['materials']['complete']:
                break
            catalog = self.w._editor_catalog(self.root, material_cursor=cursor, material_more=True)
            self.assert_formal_progress(catalog)
            scan = catalog['materials']
            self.assertEqual(scan['cursor'], cursor)
            self.assertLessEqual(scan['inspected'] - previous_inspected, self.w.AUTHOR_LIMIT)
            self.assertGreaterEqual(scan['inspected'], previous_inspected)
            ids = [row['id'] for row in catalog['related_files']]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertTrue(previous_ids <= set(ids))
            previous_ids, previous_inspected = set(ids), scan['inspected']
        else:
            self.fail('A finite history did not finish within bounded material pages')

        self.assertTrue(scan['complete'])
        self.assertFalse(scan['has_more'])
        self.assertFalse(scan['hard_limit'])
        self.assertEqual(scan['loaded'], len(catalog['related_files']))
        self.assertTrue(paths <= {row['path'] for row in catalog['related_files']})
        self.assertIn(pending_id, previous_ids)
        for index in (0, 5009):
            document = self.w._editor_document(self.root, 'file:' + (history / f'{index:05d}.txt').relative_to(self.root).as_posix())
            self.assertEqual(document['text'], f'第{index}份历史备份，保留原文。')
        self.assertEqual(self.w._editor_document(self.root, pending_id)['text'], pending_text)

        # A bounded full-text request must retain formal hits and disclose its
        # incomplete material coverage, even after the UI inventory was loaded.
        search = self.w._editor_search(self.root, '唯一的钥匙')
        self.assertFalse(search['complete'])
        self.assertTrue(search['warnings'])
        self.assertTrue({'formal:1', 'formal:2'} <= {row['id'] for row in search['results']})
        self.assertEqual((self.file_state(), self.fixture.authoritative_state()), before)

    def test_catalog_hard_budget_keeps_partial_inventory_and_does_not_block_direct_open(self):
        ids = [self.file(f'.story/drafts/历史备份/{index:03d}.txt', f'历史原文{index}。')
               for index in range(40)]
        before = self.file_state(), self.fixture.authoritative_state()
        with patch.object(self.w, 'AUTHOR_LIMIT', 7), patch.object(self.w, 'MAX_MATERIAL_NODES', 13):
            catalog = self.w._editor_catalog(self.root)
            self.assertLessEqual(catalog['materials']['inspected'], 7)
            previous = catalog['materials']['inspected']
            while catalog['materials']['has_more']:
                catalog = self.w._editor_catalog(
                    self.root, material_cursor=catalog['materials']['cursor'], material_more=True)
                self.assertLessEqual(catalog['materials']['inspected'] - previous, 7)
                previous = catalog['materials']['inspected']
            self.assert_formal_progress(catalog)
            scan = catalog['materials']
            self.assertEqual(scan['inspected'], 13)
            self.assertTrue(scan['hard_limit'])
            self.assertFalse(scan['complete'])
            self.assertFalse(scan['has_more'])
            self.assertTrue(catalog['warnings'])
            self.assertLess(scan['loaded'], len(ids))
            repeated = self.w._editor_catalog(self.root, material_cursor=scan['cursor'], material_more=True)
            self.assertEqual(repeated['materials'], scan)
            self.assertEqual(repeated['related_files'], catalog['related_files'])
            missing = set(ids) - {row['id'] for row in catalog['related_files']}
            self.assertTrue(missing)
            selected = sorted(missing)[-1]
            document = self.w._editor_document(self.root, selected)
            self.assertEqual(document['text'], '历史原文' + str(int(Path(selected).stem)) + '。')
            search = self.w._editor_search(self.root, '唯一的钥匙')
            self.assertFalse(search['complete'])
            self.assertTrue(search['warnings'])
            self.assertTrue({'formal:1', 'formal:2'} <= {row['id'] for row in search['results']})
        self.assertEqual((self.file_state(), self.fixture.authoritative_state()), before)

    def test_cursor_validation_and_expiry_require_explicit_fresh_scan(self):
        self.file('.story/drafts/历史备份/旧稿.txt')
        for cursor in ('invalid', '0' * 63, 'G' * 64, 7, []):
            with self.subTest(cursor=cursor):
                self.fixture.assert_story_error('invalid_input', self.w._editor_catalog,
                                                self.root, material_cursor=cursor)
        self.fixture.assert_story_error('workbench_material_cursor_expired', self.w._editor_catalog,
                                        self.root, material_cursor='0' * 64)
        catalog = self.w._editor_catalog(self.root)
        cursor = catalog['materials']['cursor']
        with patch.object(self.w, 'MATERIAL_SCAN_TTL', -1):
            self.fixture.assert_story_error('workbench_material_cursor_expired', self.w._editor_catalog,
                                            self.root, material_cursor=cursor)
        refreshed = self.w._editor_catalog(self.root)
        self.assertNotEqual(refreshed['materials']['cursor'], cursor)
        self.assert_formal_progress(refreshed)

    def test_cursor_does_not_attach_replaced_linked_root_to_old_materials(self):
        outer = Path(self.fixture.temp.name) / '明确关联的材料'
        outer.mkdir()
        (outer / '旧材料.md').write_text('原来明确关联的材料。', encoding='utf-8')
        roots = self.w._material_roots([outer])
        catalog = self.w._editor_catalog(self.root, material_roots=roots)
        cursor = catalog['materials']['cursor']
        outer.rename(outer.with_name('原材料备份'))
        outer.mkdir()
        (outer / '新秘密.md').write_text('不得使用旧cursor读取。', encoding='utf-8')
        self.fixture.assert_story_error('workbench_material_cursor_changed', self.w._editor_catalog,
                                        self.root, material_roots=roots, material_cursor=cursor,
                                        material_more=True)
        # A deliberate fresh scan can load the newly selected pathname, while
        # the previous material cursor is permanently retired.
        self.fixture.assert_story_error('workbench_material_cursor_expired', self.w._editor_catalog,
                                        self.root, material_roots=roots, material_cursor=cursor)

    def test_cursor_rejects_replaced_book_root_even_with_identical_book_id(self):
        catalog = self.w._editor_catalog(self.root)
        cursor = catalog['materials']['cursor']
        old_root = self.root.with_name('旧书根备份')
        # The fixture's writer must release SQLite handles before Windows can
        # rename its parent; retain the cursor and all production identity checks.
        self.fixture.book.close()
        self.root.rename(old_root)
        shutil.copytree(old_root, self.root)
        # The copied state is byte-identical and has the same book ID, but it is
        # a different directory and database identity. Old rows cannot follow it.
        self.fixture.assert_story_error('workbench_material_cursor_changed', self.w._editor_catalog,
                                        self.root, material_cursor=cursor, material_more=True)
        self.assert_formal_progress(self.w._editor_catalog(self.root))

    @unittest.skipIf(os.name == 'nt', 'Symbolic links require Windows privileges')
    def test_material_pagination_never_lists_linked_secret_or_hidden_backup(self):
        outside = Path(self.fixture.temp.name) / '外部秘密'
        outside.mkdir()
        (outside / '秘密.md').write_text('不得列入目录。', encoding='utf-8')
        self.file('.story/drafts/可见.md', '可见材料。')
        self.file('.story/drafts/.backups/原文.md', '隐藏备份必须保留。')
        (self.root / '.story/drafts/越界').symlink_to(outside, target_is_directory=True)
        before = self.file_state(), self.fixture.authoritative_state()
        with patch.object(self.w, 'AUTHOR_LIMIT', 2):
            catalog = self.w._editor_catalog(self.root)
            for _ in range(100):
                if not catalog['materials']['has_more']:
                    break
                catalog = self.w._editor_catalog(self.root, material_cursor=catalog['materials']['cursor'],
                                                 material_more=True)
            else:
                self.fail('Small fixture material pages did not finish')
        self.assertTrue(catalog['materials']['complete'])
        paths = {row['path'] for row in catalog['related_files']}
        self.assertIn('.story/drafts/可见.md', paths)
        self.assertFalse(any('秘密' in path or '.backups' in path for path in paths))
        self.assertTrue(catalog['warnings'])
        self.assertEqual((self.file_state(), self.fixture.authoritative_state()), before)

    def test_windows_guard_mtime_changes_allow_pages_but_not_replaced_directory(self):
        expected = {self.file(f'.story/drafts/历史备份/{index:02d}.txt', f'历史原文{index}。')
                    for index in range(24)}
        outer = Path(self.fixture.temp.name) / 'Windows关联材料'
        outer.mkdir()
        (outer / '提纲.md').write_text('关联原文。', encoding='utf-8')
        roots = self.w._material_roots([outer])
        before = self.file_state(), self.fixture.authoritative_state()
        directory_key = self.w._material_directory_key
        pin = self.w.api._pinned_directory
        guard_changes = []

        def touch_directory(path):
            facts = Path(path).stat()
            os.utime(path, ns=(facts.st_atime_ns, facts.st_mtime_ns + 10_000_000))
            after = Path(path).stat()
            guard_changes.append((str(path), facts.st_dev, facts.st_ino,
                                  facts.st_mtime_ns, after.st_mtime_ns))

        @contextmanager
        def windows_guard(path, *args, **kwargs):
            # Native Windows pins create and remove a temporary guard in the
            # target directory. Reproduce both observable mtime changes while
            # retaining this host's real safe directory descriptor and Path.
            with pin(path, *args, **kwargs) as directory:
                touch_directory(path)
                try:
                    yield directory
                finally:
                    touch_directory(path)

        with patch.object(self.w, '_material_directory_key',
                          side_effect=lambda facts: directory_key(facts, platform='nt')), \
                patch.object(self.w.api, '_pinned_directory', side_effect=windows_guard), \
                patch.object(self.w, 'AUTHOR_LIMIT', 3):
            catalog = self.w._editor_catalog(self.root, material_roots=roots)
            cursor = catalog['materials']['cursor']
            pages = 1
            while catalog['materials']['has_more'] and pages < 100:
                catalog = self.w._editor_catalog(self.root, material_roots=roots,
                                                 material_cursor=cursor, material_more=True)
                pages += 1
            self.assertGreater(pages, 1)
            self.assertTrue(catalog['materials']['complete'])
            self.assert_formal_progress(catalog)
            self.assertTrue(expected <= {row['id'] for row in catalog['related_files']})
            self.assertTrue(any(row.get('external') for row in catalog['related_files']))
            self.assertIsNone(self.w._material_directory_identity(self.root)[2])
            self.assertTrue(guard_changes)
            self.assertTrue(all(before_mtime != after_mtime for _, _, _, before_mtime, after_mtime in guard_changes))
            for path in {row[0] for row in guard_changes}:
                self.assertEqual(len({(dev, inode) for item, dev, inode, _, _ in guard_changes if item == path}), 1)
            self.assertEqual((self.file_state(), self.fixture.authoritative_state()), before)

            # Ignoring the guard's timestamp must not ignore an actual inode
            # replacement, including one whose pathname and contents are equal.
            old = outer.with_name('Windows关联材料原备份')
            outer.rename(old)
            shutil.copytree(old, outer)
            self.fixture.assert_story_error('workbench_material_cursor_changed', self.w._editor_catalog,
                                            self.root, material_roots=roots, material_cursor=cursor)

    @unittest.skipIf(os.name == 'nt', 'POSIX directory mtimes have no Windows pin guard')
    def test_posix_real_directory_mutation_invalidates_active_material_cursor(self):
        for index in range(8):
            self.file(f'.story/drafts/历史备份/{index}.txt')
        with patch.object(self.w, 'AUTHOR_LIMIT', 1):
            catalog = self.w._editor_catalog(self.root)
            self.assertFalse(catalog['materials']['complete'])
            cursor = catalog['materials']['cursor']
            path = self.root / '.story/drafts'
            before = path.stat()
            self.file('.story/drafts/新加入的材料.txt', '真实新增材料。')
            # Force distinct timestamps even on coarse or unusually fast test
            # filesystems, while keeping the same directory inode.
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
            self.assertEqual(path.stat().st_ino, before.st_ino)
            self.fixture.assert_story_error('workbench_material_cursor_changed', self.w._editor_catalog,
                                            self.root, material_cursor=cursor, material_more=True)

    def test_candidate_recovery_and_pending_open_without_inventory_rescan(self):
        formal = self.w._editor_document(self.root, 'formal:1')
        candidate = self.w._editor_save(self.root, {**formal, 'text': '普通候选保留文字。'})
        recovery = self.w._editor_save(self.root, {
            **formal, 'text': '自动恢复保留文字。', 'recovery_key': 'material-scan-recovery-1',
        }, recovery=True)
        pending_id, pending_text = self.pending()
        before = self.file_state(), self.fixture.authoritative_state()

        with patch.object(self.w, '_author_files', side_effect=AssertionError('must not scan inventory')):
            reopened = self.w._editor_document(self.root, candidate['id'])
            self.assertEqual(reopened['text'], '普通候选保留文字。')
            self.assertEqual(reopened['source'], 'formal:1')
            self.assertEqual(reopened['base_sha256'], formal['sha256'])
            rescued = self.w._editor_document(self.root, recovery['id'])
            self.assertEqual(rescued['text'], '自动恢复保留文字。')
            self.assertEqual(rescued['category'], '自动恢复')
            pending = self.w._editor_document(self.root, pending_id)
            self.assertEqual(pending['text'], pending_text)
            self.assertTrue(pending['needs_recovery'])
            self.assertEqual(pending['source'], 'formal:1')

        self.assertEqual((self.file_state(), self.fixture.authoritative_state()), before)

    def test_material_candidate_resolves_markdown_origin_without_inventory_rescan(self):
        source_id = self.file('01_大纲细纲/素材/设定.md', '# 设定\n\n![人物](人物.png)')
        self.file('01_大纲细纲/素材/人物.png', 'image fixture')
        source = self.w._editor_document(self.root, source_id)
        candidate = self.w._editor_save(self.root, {**source, 'text': '# 新设定\n\n![人物](人物.png)'})
        first = self.w._editor_document(self.root, candidate['id'])
        second = self.w._editor_save(self.root, {**first, 'text': '# 二次候选\n\n![人物](人物.png)'})
        before = self.file_state(), self.fixture.authoritative_state()

        with patch.object(self.w, '_author_files', side_effect=AssertionError('must not scan inventory')):
            reopened = self.w._editor_document(self.root, second['id'])
        self.assertTrue(reopened['render_markdown'])
        self.assertEqual(reopened['content_kind'], 'material')
        self.assertEqual(reopened['markdown_path'], source['path'])
        self.assertEqual(reopened['source'], first['id'])
        self.assertEqual((self.file_state(), self.fixture.authoritative_state()), before)

    def test_direct_access_keeps_original_root_and_hidden_path_restrictions(self):
        admitted = [
            '.story/drafts/旧候选/第3章 旧稿.md', '.story/analysis/source/拆书.txt',
            '00_项目策划/规划.md', '作者有话说/附言.txt', '创作约定.md',
        ]
        denied = [
            '.story/state.sqlite3', '.story/drafts/.private/秘密.md',
            '.story/drafts/旧候选/.秘密.md', '.story/analysis/.private/秘密.md',
            '00_项目策划/.private/秘密.md', '.private.md', '私人目录/秘密.md',
            'chapters/私自添加.md', '00_项目策划/记录.json',
        ]
        for relative in admitted + [p for p in denied if not p.endswith('state.sqlite3')]:
            self.file(relative, '不可越界。')
        before = self.file_state(), self.fixture.authoritative_state()
        with patch.object(self.w, '_author_files', side_effect=AssertionError('must not scan inventory')):
            for relative in admitted:
                with self.subTest(admitted=relative):
                    self.assertEqual(self.w._editor_document(self.root, 'file:' + relative)['path'], relative)
            for relative in denied + ['../outside.md', '/etc/passwd', '00_项目策划/../创作约定.md']:
                with self.subTest(denied=relative), self.assertRaises(base.story.StoryError):
                    self.w._editor_document(self.root, 'file:' + relative)
        self.assertEqual((self.file_state(), self.fixture.authoritative_state()), before)

    @unittest.skipIf(os.name == 'nt', 'Symbolic links require Windows privileges')
    def test_direct_access_never_follows_file_or_directory_links(self):
        outside = Path(self.fixture.temp.name) / '未授权目录'
        outside.mkdir()
        secret = outside / '秘密.md'
        secret.write_text('不应读取的外部文字。', encoding='utf-8')
        material = self.root / '01_大纲细纲'
        material.mkdir(exist_ok=True)
        (material / '文件链接.md').symlink_to(secret)
        (material / '目录链接').symlink_to(outside, target_is_directory=True)
        (self.root / '02_链接材料').symlink_to(outside, target_is_directory=True)
        for relative in ['01_大纲细纲/文件链接.md', '01_大纲细纲/目录链接/秘密.md', '02_链接材料/秘密.md']:
            with self.subTest(path=relative), self.assertRaises(base.story.StoryError):
                self.w._editor_document(self.root, 'file:' + relative)
        self.assertEqual(secret.read_text(encoding='utf-8'), '不应读取的外部文字。')

    def test_explicit_linked_document_opens_without_full_material_inventory(self):
        outer = Path(self.fixture.temp.name) / '已关联的材料'
        outer.mkdir()
        (outer / '提纲.md').write_text('明确关联的原始提纲。', encoding='utf-8')
        (outer / '.隐藏.md').write_text('隐藏材料。', encoding='utf-8')
        hidden = outer / '.private'
        hidden.mkdir()
        (hidden / '内容.md').write_text('隐藏材料。', encoding='utf-8')
        roots = self.w._material_roots([outer])
        key = next(iter(roots))
        document_id = 'linked:' + key + ':提纲.md'
        before = self.file_state(), self.fixture.authoritative_state()
        with patch.object(self.w, '_linked_material_files', side_effect=AssertionError('must not scan linked inventory')):
            document = self.w._linked_material_document(roots, document_id)
            self.assertEqual(document['text'], '明确关联的原始提纲。')
            self.assertFalse(document['editable'])
            self.assertIsNone(document['context'])
            for relative in ['../秘密.md', '/etc/passwd', '.隐藏.md', '.private/内容.md', '提纲.md/../提纲.md']:
                with self.subTest(path=relative), self.assertRaises(base.story.StoryError):
                    self.w._linked_material_document(roots, 'linked:' + key + ':' + relative)
            with self.assertRaises(base.story.StoryError):
                self.w._linked_material_document({}, document_id)
        self.assertEqual((self.file_state(), self.fixture.authoritative_state()), before)

    @unittest.skipIf(os.name == 'nt', 'Symbolic links require Windows privileges')
    def test_linked_document_rejects_internal_links_and_replaced_root(self):
        outer = Path(self.fixture.temp.name) / '关联根'
        outer.mkdir()
        (outer / '提纲.md').write_text('旧提纲。', encoding='utf-8')
        outside = outer.with_name('私有目录')
        outside.mkdir()
        (outside / '秘密.md').write_text('外部秘密。', encoding='utf-8')
        (outer / '链接.md').symlink_to(outside / '秘密.md')
        (outer / '目录链接').symlink_to(outside, target_is_directory=True)
        roots = self.w._material_roots([outer])
        key = next(iter(roots))
        for relative in ['链接.md', '目录链接/秘密.md']:
            with self.subTest(path=relative), self.assertRaises(base.story.StoryError):
                self.w._linked_material_document(roots, 'linked:' + key + ':' + relative)
        outer.rename(outer.with_name('原始关联材料'))
        outer.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(base.story.StoryError):
            self.w._linked_material_document(roots, 'linked:' + key + ':秘密.md')
        self.assertEqual((outside / '秘密.md').read_text(encoding='utf-8'), '外部秘密。')


if __name__ == '__main__':
    unittest.main()

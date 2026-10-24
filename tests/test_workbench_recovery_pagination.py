"""Recovery discovery must remain reachable beyond both scan and result pages."""
from contextlib import closing
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_workbench as base


class RecoveryPaginationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='story-recovery-pagination-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'book'
        base.story.Book.create(self.root, '恢复分页测试', 'long')
        self.w = base.story.workbench
        self.archives = base.story.workbench_archives
        existing = set(self.w._material_sessions)
        self.addCleanup(lambda: [self.w._material_discard(token)
                                for token in set(self.w._material_sessions) - existing])
        (self.root / '素材.md').write_text('来源材料。', encoding='utf-8')
        self.source = self.w._editor_document(self.root, 'file:素材.md')
        with closing(self.w._ReadOnlyBook(self.root)) as book:
            self.book_id = book.meta('id')

    def recovery(self):
        return self.w._editor_save(self.root, {**self.source, 'text': '尚未采用的恢复文字。',
                                   'recovery_key': 'pagination-recovery-key'}, recovery=True)

    def page(self, previous=None):
        options = {} if previous is None else {'cursor': previous['cursor'], 'offset': previous['next_offset']}
        return self.archives.recovery_catalog(self.root, self.book_id, **options)

    def error(self, code, operation):
        with self.assertRaises(base.story.StoryError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def test_real_recovery_after_5001_unrelated_materials_remains_reachable(self):
        history = self.root / '.story/drafts/000-历史材料'
        history.mkdir(parents=True)
        for index in range(5001):
            (history / f'{index:05}.md').write_text('历史候选。', encoding='utf-8')
        saved = self.recovery()
        original = (self.root / saved['path']).read_bytes()
        page = self.page()
        self.assertFalse(page['complete'])
        self.assertTrue(page['has_more'])
        self.assertEqual(page['scan']['inspected'], 5000)
        found = list(page['recoveries'])
        for _ in range(10):
            if not page['has_more']:
                break
            page = self.page(page)
            found.extend(page['recoveries'])
        self.assertTrue(page['complete'])
        self.assertEqual([row['id'] for row in found], [saved['id']])
        self.assertEqual(found[0]['content_kind'], 'material')
        opened = self.w._editor_document(self.root, saved['id'])
        self.assertEqual(opened['source'], self.source['id'])
        self.assertEqual(opened['content_kind'], 'material')
        self.assertEqual(opened['text'], '尚未采用的恢复文字。')
        self.assertEqual((self.root / saved['path']).read_bytes(), original)
        self.assertEqual((self.root / '素材.md').read_text(encoding='utf-8'), '来源材料。')

    def test_over_100_recoveries_across_scan_batches_with_reverse_mtimes(self):
        saved = self.recovery()
        original = self.root / saved['path']
        metadata = Path(str(original) + '.meta.json').read_bytes()
        expected = {saved['id']}
        for index in range(204):
            target = original.parent / f'{204 - index:04}.txt'
            target.write_bytes(original.read_bytes())
            Path(str(target) + '.meta.json').write_bytes(metadata)
            os.utime(target, (100000 + index, 100000 + index))
            expected.add('file:' + target.relative_to(self.root).as_posix())
        with patch.object(self.w, 'AUTHOR_LIMIT', 250):
            page, found = self.page(), []
            for _ in range(20):
                self.assertLessEqual(len(page['recoveries']), 100)
                self.assertEqual(page['offset'], len(found))
                found.extend(row['id'] for row in page['recoveries'])
                if not page['has_more']:
                    break
                page = self.page(page)
        self.assertTrue(page['complete'])
        self.assertFalse(page['has_more'])
        self.assertEqual(len(found), 205)
        self.assertEqual(set(found), expected)
        self.assertEqual(len(set(found)), len(found))

    def test_pending_metadata_and_private_link_boundaries_are_preserved(self):
        saved = self.recovery()
        name = '.story/drafts/workbench/保存中断.txt'
        text = '中断前的材料文字。'
        meta = {'source': self.source['id'], 'source_sha256': self.source['sha256'],
                'content_kind': 'material', 'prefix': '', 'chapter': None,
                'base_sha256': None, 'adopted': False}
        pending, _ = self.w._start_pending_save(self.root, name, text, text, meta)
        private = self.root / '.story/drafts/workbench/.backups/自动恢复/秘密.txt'
        private.parent.mkdir(parents=True)
        private.write_text('不能列出。', encoding='utf-8')
        outside = self.root.parent / '外部.txt'
        outside.write_text('不能跟随链接。', encoding='utf-8')
        linked = Path(self.root / saved['path']).parent / '链接.txt'
        try:
            linked.symlink_to(outside)
        except OSError:
            linked = None
        page = self.page()
        self.assertEqual({row['id'] for row in page['recoveries']}, {saved['id'], 'pending:' + pending})
        pending_row = next(row for row in page['recoveries'] if row['id'].startswith('pending:'))
        self.assertEqual(pending_row['status'], '保存待核对')
        opened = self.w._editor_document(self.root, pending_row['id'])
        self.assertEqual(opened['text'], text)
        self.assertEqual(opened['source'], self.source['id'])
        self.assertTrue(opened['needs_recovery'])
        self.assertFalse(any('adopted' in row for row in page['recoveries']))
        if linked is not None:
            self.assertTrue(page['warnings'])

    def test_expiration_refresh_book_identity_and_mode_binding(self):
        self.recovery()
        first = self.page()
        self.w._material_discard(first['cursor'])
        self.error('workbench_material_cursor_expired', lambda: self.page(first))
        refreshed = self.page()
        self.assertEqual(refreshed['recoveries'], first['recoveries'])
        self.assertNotEqual(refreshed['cursor'], first['cursor'])
        self.error('archive_identity', lambda: self.archives.recovery_catalog(self.root, 'different-book'))
        _, material, _ = self.w._material_catalog(self.root)
        self.error('invalid_input', lambda: self.archives.recovery_catalog(self.root, self.book_id,
                                                                          cursor=material['cursor']))
        self.error('invalid_input', lambda: self.w._material_catalog(self.root, cursor=refreshed['cursor']))

    def test_invalid_offsets_and_directory_change_require_refresh(self):
        self.recovery()
        for offset in (True, -1, '0', 1):
            self.error('invalid_input', lambda: self.archives.recovery_catalog(self.root, offset=offset))
        with patch.object(self.w, 'AUTHOR_LIMIT', 1):
            first = self.page()
            self.error('invalid_input', lambda: self.archives.recovery_catalog(
                self.root, cursor=first['cursor'], offset=100))
            before = self.root.stat()
            (self.root / '新材料.md').write_text('新增材料。', encoding='utf-8')
            if os.name == 'nt':
                # Windows directory pins themselves change mtimes. Ordinary
                # child additions keep the bound directory's identity valid.
                continued = self.page(first)
                self.assertEqual(continued['cursor'], first['cursor'])
                self.assertEqual(self.w._material_directory_key(before),
                                 self.w._material_directory_identity(self.root))
            else:
                # Keep the genuine POSIX directory-mutation rejection even
                # on a filesystem with coarse timestamps.
                os.utime(self.root, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
                self.error('workbench_material_cursor_changed', lambda: self.page(first))
        self.w._material_discard(first['cursor'])
        complete = self.page()
        self.assertTrue(complete['complete'])
        # A finished cursor has released its scandir handles, so the real
        # directory can be replaced on Windows too. Equal copied book contents
        # and the same book ID must never make this physical replacement safe.
        old_root = self.root.with_name('old-book')
        before = self.w._material_directory_identity(self.root)
        state = (self.root / '.story/state.sqlite3').read_bytes()
        self.root.rename(old_root)
        shutil.copytree(old_root, self.root)
        self.assertNotEqual(before[:2], self.w._material_directory_identity(self.root)[:2])
        self.assertEqual((self.root / '.story/state.sqlite3').read_bytes(), state)
        with closing(self.w._ReadOnlyBook(self.root)) as book:
            self.assertEqual(book.meta('id'), self.book_id)
        self.error('workbench_material_cursor_changed', lambda: self.page(complete))
        self.assertTrue(self.page()['complete'])

    def test_scan_failure_and_hard_limit_never_claim_complete(self):
        self.recovery()
        with patch.object(self.w, '_material_inventory_walk', side_effect=OSError('synthetic scan failure')):
            with self.assertRaisesRegex(OSError, 'synthetic scan failure'):
                self.page()
        with patch.object(self.w, 'MAX_MATERIAL_NODES', 1):
            page = self.page()
        self.assertFalse(page['complete'])
        self.assertFalse(page['has_more'])
        self.assertTrue(page['scan']['hard_limit'])
        self.assertTrue(page['warnings'])


class RecoveryPaginationUITests(unittest.TestCase):
    def ui(self, assertions, extra=''):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js is required for recovery UI behavior')
        source = r"""
const assert=require('node:assert/strict');
class Element {
 constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.hidden=false;this.disabled=false;this.open=true;}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;}
 setAttribute(){} close(){this.open=false;} showModal(){this.open=true;} addEventListener(){} remove(){}
}
const document={createElement:tag=>new Element(tag),body:new Element()},elements={};
const $=id=>elements[id]||(elements[id]=new Element());
const texts=node=>[node.textContent,...node.children.flatMap(texts)].join('\n');
const buttons=node=>[...(node.tag==='button'?[node]:[]),...node.children.flatMap(buttons)];
const row=id=>({id,path:id,title:id,status:'自动恢复稿'});
const packet=(rows,options={})=>({recoveries:rows,offset:0,next_offset:rows.length,cursor:'a'.repeat(64),
 complete:true,has_more:false,scan:{complete:true},warnings:[],...options});
""" + base.story.workbench._recovery_list_script() + extra
        result = subprocess.run([node, '-'], input=source + '\n(async()=>{' + assertions +
                                '})().catch(error=>{console.error(error);process.exitCode=1;});',
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_partial_empty_more_error_refresh_and_reopen(self):
        self.ui(r"""
const box=new Element(),requests=[],opened=[];
let step=0;
await recoveryList(box,async data=>{requests.push(data);step++;
 if(step===1)return packet([],{complete:false,has_more:true});
 if(step===2)return packet([row('later')],{complete:false,has_more:true,next_offset:1});
 if(step===3)throw Error('分页已过期');
 return packet([row('fresh')]);},async value=>opened.push(value.id),()=>true);
assert.ok(!texts(box).includes('没有可列出的'));assert.ok(texts(box).includes('未读取'));
let more=buttons(box).find(value=>value.textContent==='继续加载恢复记录');
await more.onclick();assert.equal(requests[1].cursor,'a'.repeat(64));assert.equal(requests[1].offset,0);
await more.onclick();assert.ok(texts(box).includes('分页已过期'));assert.ok(texts(box).includes('later'));
await buttons(box).find(value=>value.textContent.includes('later')).onclick();assert.deepEqual(opened,['later']);
await buttons(box).find(value=>value.textContent==='重新读取恢复记录').onclick();
assert.equal(requests[3].cursor,null);assert.equal(requests[3].offset,0);
assert.ok(!texts(box).includes('later'));assert.ok(texts(box).includes('fresh'));
const reopened=new Element();await recoveryList(reopened,async data=>{assert.equal(data.cursor,null);return packet([]);},()=>{},()=>true);
assert.ok(texts(reopened).includes('没有可列出的磁盘恢复记录'));
""");

    def test_completed_warnings_hard_limit_and_late_response_are_not_empty(self):
        self.ui(r"""
for(const value of [packet([],{warnings:['目录无法安全读取']}),packet([],{complete:false,scan:{hard_limit:true}})]){
 const box=new Element();await recoveryList(box,async()=>value,()=>{},()=>true);
 assert.ok(!texts(box).includes('没有可列出的'));assert.ok(texts(box).includes('未完成'));
}
let resolve,current=true;const box=new Element();const pending=recoveryList(box,()=>new Promise(done=>resolve=done),()=>{},()=>current);
current=false;resolve(packet([row('old-dialog')]));await pending;assert.ok(!texts(box).includes('old-dialog'));
""");

    def test_editor_entry_uses_shared_recovery_api_and_does_not_touch_buffers(self):
        script = base.story.workbench._editor_tools_script()
        handler = script[script.index("$('recoveries-open')"):script.index("$('writing-task')")]
        self.ui(r"""
await $('recoveries-open').onclick();assert.deepEqual(requests,[{action:'recoveries',payload:{cursor:null,offset:0}}]);
assert.equal(buffer,'未保存文字');assert.ok(texts(box).includes('saved'));
""", r"""
const box=new Element(),dialog=new Element(),editorDialog=()=>({dialog,content:box});
const requests=[],buffer='未保存文字';const openDoc=()=>{};
const call=async(action,payload)=>{requests.push({action,payload});return packet([row('saved')]);};
""" + handler)

    def test_shelf_selects_one_book_before_scanning_and_keeps_error_visible(self):
        script = base.story.workbench._library_management_script()
        handler = script[script.index('async function showRecoveries('):script.index('function showCreateBook(')]
        self.ui(r"""
await showRecoveries();assert.equal(requests.length,1);assert.deepEqual(requests[0],{});
assert.equal(buttons(managerBody).length,30);await buttons(managerBody)[12].onclick();
assert.deepEqual(requests[1],{key:'book12',cursor:null,offset:0});assert.ok(texts(managerBody).includes('选中书的分页已过期'));
assert.ok(!texts(managerBody).includes('没有可列出的'));
await buttons(managerBody).find(value=>value.textContent==='重新读取恢复记录').onclick();
assert.deepEqual(requests[2],{key:'book12',cursor:null,offset:0});assert.ok(texts(managerBody).includes('chosen'));
""", r"""
const manager=new Element(),managerBody=new Element(),requests=[];let managerGeneration=0;
const bookName=book=>book.title,bookDisplayRows=books=>books.map(book=>({...book,label:book.title}));
const openManager=()=>{managerBody.replaceChildren();return ++managerGeneration;};
const managementText=value=>{const p=new Element('p');p.textContent=value;managerBody.append(p);};
const openRecoveryBook=async()=>{};
const call=async(action,data)=>{assert.equal(action,'recoveries');requests.push(data);
 if(!data.key)return {books:Array.from({length:30},(_,index)=>({key:'book'+index,title:'书'+index,deferred:true}))};
 if(requests.length===2)return {books:[{key:data.key,error:'选中书的分页已过期'}]};
 return {books:[{key:data.key,...packet([row('chosen')])}]};};
""" + handler)


class RecoveryPaginationHTTPTests(unittest.TestCase):
    def test_shelf_book_selection_never_starts_thirty_scans_and_forwards_page(self):
        import test_workbench_management as management
        fixture = management.WorkbenchManagementHTTPTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        books = [fixture.create(title='同名作品', name='书' + str(index))[1] for index in range(10)]
        archives = base.story.workbench_archives
        with patch.object(archives, 'recovery_catalog', side_effect=AssertionError('must be lazy')) as scan:
            status, result = fixture.post('recoveries')
            self.assertEqual(status, 200, result)
            self.assertEqual(len(result['books']), 10)
            self.assertTrue(all(group['deferred'] and group['root'] for group in result['books']))
            scan.assert_not_called()
        selected = books[0]
        response = {'ok': True, 'recoveries': [], 'cursor': 'a' * 64, 'offset': 100,
                    'next_offset': 100, 'has_more': True, 'complete': False}
        with patch.object(archives, 'recovery_catalog', return_value=response) as scan:
            status, result = fixture.post('recoveries', {'key': selected['key'], 'cursor': 'a' * 64, 'offset': 100})
            self.assertEqual(status, 200, result)
            self.assertEqual(result['books'][0]['next_offset'], 100)
            scan.assert_called_once_with(selected['root'], selected['book_id'], 'a' * 64, 100)


if __name__ == '__main__':
    unittest.main()

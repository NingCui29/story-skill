"""Bookshelf update ordering uses verified times and preserves the reader's view."""
import json
import os
import shutil
import subprocess
import unittest

import test_workbench as base


class WorkbenchTimeDisplayTests(unittest.TestCase):
    def setUp(self):
        self.page = base.story.workbench._library_page('test').decode()
        script = self.page.split('<script>', 1)[1].split('</script>', 1)[0]
        self.source = script[script.index('function bookName'):script.index("$('refresh').onclick")]

    def js(self, test, saved=None, storage_error=False):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        mock = r"""
const assert=require('assert');
class E {
 constructor(tag='div'){this.tag=tag;this.children=[];this._text='';this.attributes={};this.value='';}
 set textContent(value){this._text=String(value);this.children=[];}
 get textContent(){return this._text;}
 set innerHTML(value){throw Error('Bookshelf must use text nodes');}
 replaceChildren(...children){this.children=children;this._text='';}
 append(...children){this.children.push(...children);}
 setAttribute(key,value){this.attributes[key]=value;}
 click(){this.clicked=true;}
}
const nodes={},$=id=>nodes[id]||(nodes[id]=new E());
const document={createElement:tag=>new E(tag),createTextNode:text=>({textContent:text})};
let response={books:[]};const call=async()=>response;
const localStorage={getItem(){if(storageError)throw Error('Storage blocked');return storageValue;},setItem(key,value){if(storageError)throw Error('Storage blocked');storageValue=value;}};
const book=(key,at,extras={})=>({title:key,key,root:'/books/'+key,kind:'long',kind_verified:true,available:true,updated_at:at,updated_at_verified:true,...extras});
const keys=items=>items.map(item=>item.key);
const cardKeys=()=>$('books').children.map(row=>row.children[0].textContent.split(' · ')[0]);
const changeSort=value=>{$('shelf-order').value=value;$('shelf-order').onchange();};
"""
        setup = 'let storageValue=' + json.dumps(saved) + ',storageError=' + json.dumps(storage_error) + ';\n'
        result = subprocess.run(
            [node, '-e', setup + mock + self.source + '\n(async()=>{' + test + '\n})().catch(error=>{console.error(error);process.exitCode=1;});'],
            capture_output=True, text=True, timeout=15, env={**os.environ, 'TZ': 'Asia/Shanghai'},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_sort_directions_keep_unknown_times_last_without_mutating_records(self):
        self.js(r"""
const records=[book('missing',null),book('older','2026-09-01T12:00:00Z'),book('unverified','2099-01-01T00:00:00Z',{updated_at_verified:false}),book('newer','2026-10-05T12:00:00Z'),book('invalid','bad'),book('unavailable','2100-01-01T00:00:00Z',{available:false})];
const original=records.slice();
assert.deepEqual(keys(sortShelfBooks(records)),['newer','older','missing','unverified','invalid','unavailable']);
assert.deepEqual(keys(sortShelfBooks(records,'oldest')),['older','newer','missing','unverified','invalid','unavailable']);
assert.deepEqual(records,original);
""")

    def test_equal_times_stay_stable_and_fractional_seconds_keep_their_order(self):
        self.js(r"""
const records=[book('one','2026-10-05T00:00:00.100000Z'),book('two','2026-10-05T00:00:00.1+00:00'),book('three','2026-10-05T00:00:00.100000000Z'),book('later','2026-10-05T00:00:00.100001Z'),book('earlier','2026-10-05T00:00:00.099999Z')];
assert.deepEqual(keys(sortShelfBooks(records)),['later','one','two','three','earlier']);
assert.deepEqual(keys(sortShelfBooks(records,'oldest')),['earlier','one','two','three','later']);
const same=[book('first','2026-10-05T00:00:00Z'),book('second','2026-10-05T00:00:00.000000+00:00')];
assert.deepEqual(keys(sortShelfBooks(same)),['first','second']);assert.deepEqual(keys(sortShelfBooks(same,'oldest')),['first','second']);
""")

    def test_only_verified_legal_utc_timestamps_participate_in_ordering(self):
        self.js(r"""
for(const at of [null,123,'','2026-02-30T00:00:00Z','2026-13-01T00:00:00Z','2026-10-05','2026-10-05T00:00:00','2026-10-05T00:00:00+08:00','2026-10-05T24:00:00Z','2026-10-05T00:60:00Z','2026-10-05T00:00:00.1234567890Z','<img onerror=bad>'])assert.equal(shelfUpdatedTime(book('invalid',at)),null,String(at));
for(const verification of [undefined,null,false,1,'true'])assert.equal(shelfUpdatedTime(book('invalid','2026-10-05T00:00:00Z',{updated_at_verified:verification})),null);
assert.ok(shelfUpdatedTime(book('leap','2024-02-29T12:34:56.123456Z')));assert.ok(shelfUpdatedTime(book('utc','2026-10-05T00:00:00+00:00')));
""")

    def test_default_order_local_time_and_uncertain_time_labels(self):
        self.js(r"""
render({books:[book('older','2026-09-01T00:00:00Z'),book('unknown',null,{updated_at_note:'<script>待核对</script>'}),book('newer','2026-10-05T00:00:00Z',{updated_at_note:'当前作者文件与正式状态事件'})]});
assert.equal($('shelf-order').value,'newest');assert.deepEqual(cardKeys(),['newer','older','unknown']);
const time=$('books').children[0].children.find(node=>node.className==='book-updated');assert.equal(time.tag,'time');assert.equal(time.dateTime,'2026-10-05T00:00:00Z');assert.ok(time.textContent.includes('2026/10/05'));assert.ok(time.textContent.includes('08:00:00'));assert.equal(time.title,'当前作者文件与正式状态事件');
const unknown=$('books').children[2].children.find(node=>node.className==='book-updated');assert.equal(unknown.tag,'p');assert.equal(unknown.textContent,'更新时间未确认');assert.equal(unknown.dateTime,undefined);assert.equal(unknown.title,'<script>待核对</script>');
assert.equal($('filter-all').textContent,'全部 3');assert.equal($('filter-long').textContent,'长篇 3');
""")

    def test_sort_type_filter_and_actual_refresh_preserve_selected_view(self):
        self.js(r"""
const older=book('older','2026-09-01T00:00:00Z'),newer=book('newer','2026-10-05T00:00:00Z'),short=book('short','2026-10-04T00:00:00Z',{kind:'short'});
render({books:[newer,short,older]});changeSort('oldest');$('filter-long').onclick();assert.deepEqual(cardKeys(),['older','newer']);
response={books:[book('middle','2026-10-01T00:00:00Z'),newer,short,older,book('unknown',null)]};await refresh();
assert.equal(shelfFilter,'long');assert.equal(shelfSort,'oldest');assert.equal($('shelf-order').value,'oldest');assert.equal($('filter-long').attributes['aria-pressed'],'true');assert.equal($('filter-all').textContent,'全部 5');assert.deepEqual(cardKeys(),['older','middle','newer','unknown']);
$('filter-short').onclick();assert.deepEqual(cardKeys(),['short']);assert.equal(shelfSort,'oldest');$('filter-all').onclick();assert.deepEqual(cardKeys(),['older','middle','short','newer','unknown']);changeSort('newest');assert.deepEqual(cardKeys(),['newer','short','middle','older','unknown']);
assert.deepEqual(JSON.parse(storageValue),{filter:'all',sort:'newest',status:'all'});
""")

    def test_preferences_reload_and_invalid_settings_fall_back_to_defaults(self):
        self.js(r"""
assert.equal(shelfFilter,'short');assert.equal(shelfSort,'oldest');
render({books:[book('short','2026-10-05T00:00:00Z',{kind:'short'}),book('long','2026-10-04T00:00:00Z')]});assert.deepEqual(cardKeys(),['short']);assert.equal($('shelf-order').value,'oldest');
$('filter-long').onclick();changeSort('newest');shelfFilter='all';shelfSort='oldest';restoreShelfView();assert.equal(shelfFilter,'long');assert.equal(shelfSort,'newest');
shelfFilter='all';shelfSort='newest';storageValue=JSON.stringify({filter:'constructor',sort:'random'});restoreShelfView();assert.equal(shelfFilter,'all');assert.equal(shelfSort,'newest');
storageValue='broken json';restoreShelfView();assert.equal(shelfFilter,'all');assert.equal(shelfSort,'newest');
""", saved=json.dumps({'filter': 'short', 'sort': 'oldest'}))

    def test_disabled_storage_keeps_filter_and_order_in_memory_during_refresh(self):
        self.js(r"""
assert.equal(shelfFilter,'all');assert.equal(shelfSort,'newest');
render({books:[book('newer','2026-10-05T00:00:00Z'),book('older','2026-09-01T00:00:00Z')]});$('filter-long').onclick();changeSort('oldest');response={books:[book('newer','2026-10-05T00:00:00Z'),book('older','2026-09-01T00:00:00Z'),book('short',null,{kind:'short'})]};await refresh();
assert.equal(shelfFilter,'long');assert.equal(shelfSort,'oldest');assert.deepEqual(cardKeys(),['older','newer']);assert.equal($('filter-all').textContent,'全部 3');
""", storage_error=True)

    def test_page_has_compact_accessible_sort_control(self):
        self.assertIn('class="shelf-controls"', self.page)
        self.assertIn('for="shelf-order"', self.page)
        self.assertIn('id="shelf-order" aria-label="按更新时间排序"', self.page)
        self.assertIn('<option value="newest">最新在前</option>', self.page)
        self.assertIn('<option value="oldest">最早在前</option>', self.page)


if __name__ == '__main__':
    unittest.main()

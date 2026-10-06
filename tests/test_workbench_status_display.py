"""Local writing state display, intersecting filters, and confirmed mutation."""
import json
import shutil
import subprocess
import unittest

import test_workbench as base


class WorkbenchStatusDisplayTests(unittest.TestCase):
    def setUp(self):
        self.page = base.story.workbench._library_page('test').decode()
        script = self.page.split('<script>', 1)[1].split('</script>', 1)[0]
        self.source = script[script.index('function bookName'):script.index("$('refresh').onclick")]

    def js(self, test, saved=None):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        mock = r"""
const assert=require('assert');
class E {
 constructor(tag='div'){this.tag=tag;this.children=[];this._text='';this.attributes={};this.value='';}
 set textContent(value){this._text=String(value);this.children=[];}
 get textContent(){return this._text;}
 set innerHTML(value){throw Error('Status must use safe text nodes');}
 replaceChildren(...children){this.children=children;this._text='';}
 append(...children){this.children.push(...children);}
 setAttribute(key,value){this.attributes[key]=value;}
 click(){this.clicked=true;}
 focus(){this.focused=true;}
}
const nodes={},$=id=>nodes[id]||(nodes[id]=new E());
const document={createElement:tag=>new E(tag),createTextNode:text=>({textContent:text})};
const localStorage={getItem:()=>storageValue,setItem:(key,value)=>{storageValue=value;}};
let response={books:[]},failure=null,pending=null,calls=[];
const call=async(action,data)=>{calls.push({action,data});if(failure)throw Error(failure);if(pending)return pending;return response;};
const state=(value,verified=true,source='author_mark',extras={})=>({value,verified,source,note:'已核对本地来源',...extras});
const book=(title,kind,writing_status,extras={})=>({title,key:title,root:'/书库/'+title,kind,kind_verified:true,available:true,writing_status,...extras});
const books=[
 book('连载书','long',state('serializing'),{updated_at:'2026-10-01T00:00:00Z',updated_at_verified:true}),
 book('记录完本','long',state('completed',true,'recorded',{sources:[{path:'README.md',line:8,field:'当前状态',value:'已完本'}]})),
 book('手动完本','short',state('completed')),
 book('未核对','long',state('completed',false)),
 book('分析书','analysis',state('not_applicable',true,'recorded')),
 book('不可用','short',state('serializing'),{available:false}),
];
const cards=()=>$('books').children;
const card=title=>cards().find(row=>row.children[0].textContent.startsWith(title+' · '));
const badge=row=>row.children.find(child=>child.className==='book-status');
const editor=row=>row.children.find(child=>child.className==='book-status-editor');
const select=details=>details.children.find(child=>child.tag==='label').children.find(child=>child.tag==='select');
const save=details=>details.children.find(child=>child.tag==='button');
const filter=value=>{$('shelf-status').value=value;$('shelf-status').onchange();};
const search=value=>{$('shelf-search').value=value;$('shelf-search').oninput();};
const sort=value=>{$('shelf-order').value=value;$('shelf-order').onchange();};
const counts=()=>Object.fromEntries($('shelf-status').children.map(option=>[option.value,option.textContent]));
const text=node=>[node.textContent,...(node.children||[]).map(text)].join(' ');
const descendants=node=>[node,...(node.children||[]).flatMap(descendants)];
"""
        setup = 'let storageValue=' + json.dumps(saved) + ';\n'
        result = subprocess.run([node, '-e', setup + mock + self.source + '\n(async()=>{' + test + '\n})().catch(error=>{console.error(error);process.exitCode=1;});'], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_only_verified_valid_writing_values_show_serializing_or_completed(self):
        self.js(r"""
assert.equal(shelfWritingState(books[0]),'serializing');assert.equal(shelfWritingState(books[1]),'completed');
for(const writing_status of [undefined,state('completed',false),state('completed','true'),state('complete'),state('constructor'),state('not_applicable'),state('unknown')]){const b={...books[0],writing_status};assert.equal(shelfWritingState(b),'unknown');assert.equal(shelfStatusNode(b).textContent,'状态待确认');}
assert.equal(shelfWritingState(books[5]),'unknown');assert.equal(shelfStatusNode(books[0]).textContent,'连载中');assert.equal(shelfStatusNode(books[1]).textContent,'已完本');assert.ok(shelfStatusNode(books[1]).title.includes('本地作品状态'));
""")

    def test_analysis_is_a_project_without_a_writing_status_mutation_control(self):
        self.js(r"""
for(const writing_status of [undefined,state('completed'),state('serializing'),state('not_applicable')]){const b={...books[4],writing_status};assert.equal(shelfWritingState(b),'not_applicable');assert.equal(shelfStatusNode(b).textContent,'分析项目');assert.equal(shelfStatusEditor(b),null);}
assert.equal(shelfStatusEditor(books[5]),null);
render({books});assert.equal(editor(card('分析书')),undefined);assert.equal(badge(card('分析书')).textContent,'分析项目');filter('unknown');assert.equal(cards().length,2);assert.equal(card('分析书'),undefined);assert.equal(calls.length,0);
""")

    def test_status_filter_counts_follow_type_and_search_does_not_change_counts(self):
        self.js(r"""
render({books});assert.deepEqual(counts(),{all:'全部状态 6',serializing:'连载中 1',completed:'已完本 2',unknown:'待确认 2'});assert.equal($('total-count').textContent,'6');
filter('completed');assert.equal(cards().length,2);$('filter-long').onclick();assert.equal(cards().length,1);assert.equal(badge(cards()[0]).textContent,'已完本');assert.deepEqual(counts(),{all:'全部状态 3',serializing:'连载中 1',completed:'已完本 1',unknown:'待确认 1'});
search('没有匹配');assert.equal(cards().length,0);assert.equal(counts().completed,'已完本 1');assert.equal($('filter-all').textContent,'全部 6');$('clear-search').onclick();assert.equal(cards().length,1);assert.equal(shelfStatusFilter,'completed');assert.equal(shelfFilter,'long');
""")

    def test_refresh_and_persisted_preferences_keep_all_selected_filters(self):
        self.js(r"""
render({books});$('filter-long').onclick();sort('oldest');filter('completed');search('记录');
response={books:[...books,book('记录新完本','long',state('completed'))]};await refresh();assert.equal(shelfFilter,'long');assert.equal(shelfSort,'oldest');assert.equal(shelfStatusFilter,'completed');assert.equal(shelfQuery,'记录');assert.equal(cards().length,2);assert.equal($('shelf-status').value,'completed');assert.deepEqual(JSON.parse(storageValue),{filter:'long',sort:'oldest',status:'completed'});
shelfFilter='all';shelfSort='newest';shelfStatusFilter='all';restoreShelfView();assert.equal(shelfFilter,'long');assert.equal(shelfSort,'oldest');assert.equal(shelfStatusFilter,'completed');storageValue=JSON.stringify({status:'constructor'});shelfStatusFilter='all';restoreShelfView();assert.equal(shelfStatusFilter,'all');
""")

    def test_old_saved_preferences_load_with_all_statuses_and_valid_saved_status_restores(self):
        self.js(r"""
assert.equal(shelfFilter,'short');assert.equal(shelfSort,'oldest');assert.equal(shelfStatusFilter,'all');storageValue=JSON.stringify({filter:'long',sort:'newest',status:'unknown'});restoreShelfView();render({books});assert.equal(shelfStatusFilter,'unknown');assert.equal(cards().length,1);assert.equal(badge(cards()[0]).textContent,'状态待确认');
""", saved=json.dumps({'filter': 'short', 'sort': 'oldest'}))

    def test_editor_distinguishes_manual_mark_from_recorded_state_and_shows_safe_evidence(self):
        self.js(r"""
assert.equal(select(shelfStatusEditor(books[0])).value,'serializing');assert.equal(select(shelfStatusEditor(books[2])).value,'completed');assert.equal(select(shelfStatusEditor(books[1])).value,'unknown');
const row=shelfStatusEditor({...books[1],writing_status:state('completed',true,'recorded',{note:'<script>已完本依据</script>',sources:[{path:'<img>.md',line:12,field:'状态',value:'**已完本**'}]})});assert.ok(text(row).includes('按作品记录显示'));assert.ok(text(row).includes('清除手动标记后，显示作品记录；无明确记录则待确认'));assert.ok(text(row).includes('不代表平台已发布或完本'));assert.ok(text(row).includes('作品记录'));assert.ok(text(row).includes('<img>.md · 第12行'));assert.ok(text(row).includes('状态：**已完本**'));assert.ok(text(row).includes('<script>已完本依据</script>'));assert.ok(!descendants(row).some(node=>['img','script','a'].includes(node.tag)));
assert.ok(text(shelfStatusEditor(books[0])).includes('书架手动标记'));
""")

    def test_successful_mutation_is_confirmed_by_catalog_and_keeps_view_when_card_leaves_filter(self):
        self.js(r"""
render({books});$('filter-long').onclick();sort('oldest');filter('serializing');search('连载');const details=editor(card('连载书'));select(details).value='completed';const updated={...books[0],writing_status:state('completed')};response={books:[updated,...books.slice(1)]};await save(details).onclick();
assert.deepEqual(calls[0],{action:'set-book-status',data:{key:'连载书',value:'completed'}});assert.equal(shelfFilter,'long');assert.equal(shelfStatusFilter,'serializing');assert.equal(shelfSort,'oldest');assert.equal(shelfQuery,'连载');assert.equal(cards().length,0);assert.equal(shelfBooks.find(book=>book.key==='连载书').writing_status.value,'completed');assert.ok($('message').textContent.includes('作品状态已更新（连载书）：已完本'));assert.equal(statusSaving.size,0);
""")

    def test_clearing_manual_mark_can_restore_recorded_completed_state(self):
        self.js(r"""
render({books});const details=editor(card('连载书'));select(details).value='unknown';response={books:[{...books[0],writing_status:state('completed',true,'recorded')},...books.slice(1)]};await save(details).onclick();assert.equal(calls[0].data.value,'unknown');assert.equal(badge(card('连载书')).textContent,'已完本');assert.equal(select(editor(card('连载书'))).value,'unknown');assert.ok($('message').textContent.includes('已完本'));assert.ok(!$('message').textContent.includes('状态待确认'));
""")

    def test_failed_and_pending_mutation_never_optimistically_change_marker_or_filters(self):
        self.js(r"""
render({books});$('filter-long').onclick();sort('oldest');filter('serializing');search('连载');let details=editor(card('连载书'));select(details).value='completed';let reject;pending=new Promise((resolve,deny)=>{reject=deny;});const saving=save(details).onclick();assert.equal(badge(card('连载书')).textContent,'连载中');assert.ok(select(details).disabled);assert.ok(save(details).disabled);assert.ok(!$('message').textContent.includes('已更新'));
await save(details).onclick();assert.equal(calls.length,1);renderShelf();assert.ok(save(editor(card('连载书'))).disabled);reject(Error('作品目录身份已变化'));await saving;
assert.equal(badge(card('连载书')).textContent,'连载中');assert.equal(select(editor(card('连载书'))).value,'serializing');assert.equal(save(editor(card('连载书'))).disabled,false);assert.equal(shelfFilter,'long');assert.equal(shelfStatusFilter,'serializing');assert.equal(shelfSort,'oldest');assert.equal(shelfQuery,'连载');assert.ok($('message').textContent.includes('更新未确认（连载书）'));assert.ok(!$('message').textContent.includes('已更新'));assert.equal(statusSaving.size,0);assert.equal(books[0].writing_status.value,'serializing');
""")

    def test_controls_are_accessible_and_open_remains_first_top_level_button(self):
        self.assertIn('id="shelf-status" aria-label="按本地作品状态筛选"', self.page)
        self.js(r"""
render({books});const row=card('记录完本');assert.equal(row.children[0].tag,'strong');assert.equal(row.children.find(node=>node.tag==='button').textContent,'打开作品');assert.equal(row.children.find(node=>node.tag==='details').children[0].textContent,'目录位置');assert.ok(select(editor(row)).attributes['aria-label'].includes('记录完本'));assert.equal(editor(row).children[0].textContent,'作品状态');select(editor(row)).value='illegal';await save(editor(row)).onclick();assert.equal(calls.length,0);
""")


if __name__ == '__main__':
    unittest.main()

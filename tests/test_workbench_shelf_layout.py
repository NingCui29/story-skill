"""Card bookshelf search keeps verified metadata and existing open behavior."""
import shutil
import subprocess
import unittest

import test_workbench as base


class WorkbenchShelfLayoutTests(unittest.TestCase):
    def setUp(self):
        self.page = base.story.workbench._library_page('test').decode()
        script = self.page.split('<script>', 1)[1].split('</script>', 1)[0]
        self.source = script[script.index('function bookName'):script.index("$('refresh').onclick")]

    def js(self, test):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        mock = r"""
const assert=require('assert');
class E {
 constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this._text='';this.attributes={};this.value='';}
 set textContent(value){this._text=String(value);this.children=[];}
 get textContent(){return this._text;}
 set innerHTML(value){throw Error('Bookshelf must use text nodes');}
 replaceChildren(...children){this.children=children;this._text='';}
 append(...children){this.children.push(...children);}
 setAttribute(key,value){this.attributes[key]=value;}
 click(){this.clicked=true;}
 focus(){this.focused=true;}
}
const nodes={},$=id=>nodes[id]||(nodes[id]=new E());
const document={createElement:tag=>new E(tag),createTextNode:text=>({textContent:text})};
// Lifecycle/session hooks sit outside the extracted renderer under test.
let managementDecorations=0;const rememberedShelfBooks=[];
function decorateBookManagement(){managementDecorations++;}
function rememberShelfBook(key){rememberedShelfBooks.push(key);}
const localStorage={getItem:()=>null,setItem:()=>{}};
let response={books:[]},failOpen=false;
const call=async action=>{if(action==='open-book'){if(failOpen)throw Error('此作品的服务无法确认已退出');return {url:'http://127.0.0.1:1234/confirmed-editor/'};}return response;};
const books=[
 {title:'灯下的都市',kind:'long',kind_verified:true,root:'/甲/同名',key:'long',available:true,updated_at:'2026-10-01T00:00:00Z',updated_at_verified:true,classification:{status:'recorded',tags:['都市','悬疑'],sources:[{path:'01_策划/分类.md',field:'题材',value:'都市、悬疑'}]}},
 {title:'灯下的都市',kind:'short',kind_verified:true,root:'/乙/同名',key:'short',available:true,updated_at:'2026-10-05T00:00:00Z',updated_at_verified:true,classification:{status:'recorded',tags:['修仙'],sources:[]}},
 {title:'CASE Lab',kind:'analysis',kind_verified:true,root:'/分析',key:'analysis',available:true,classification:{status:'recorded',tags:['都市'],sources:[]}},
 {title:'旧作品',kind:'long',kind_verified:false,root:'/旧作品',key:'old',available:false,classification:{status:'unavailable',tags:['未核对标签'],sources:[]}},
];
const query=value=>{$('shelf-search').value=value;$('shelf-search').oninput();};
const changeSort=value=>{$('shelf-order').value=value;$('shelf-order').onchange();};
const labels=()=>$('books').children.map(row=>row.children[0].textContent);
"""
        result = subprocess.run([node, '-'], input=mock + self.source + '\n(async()=>{' + test + '\n})().catch(error=>{console.error(error);process.exitCode=1;});', encoding='utf-8', capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_search_applies_inside_current_type_without_changing_counts_or_sort(self):
        self.js(r"""
render({books});changeSort('oldest');$('filter-long').onclick();query('修仙');
assert.equal($('books').children.length,0);assert.ok($('books').textContent.includes('当前分类中没有'));assert.equal($('search-empty').hidden,false);assert.equal($('filter-all').textContent,'全部 4');assert.equal($('filter-long').textContent,'长篇 1');assert.equal(shelfSort,'oldest');
$('filter-short').onclick();assert.equal(shelfQuery,'修仙');assert.equal($('books').children.length,1);assert.ok(labels()[0].includes('短篇'));assert.equal($('search-empty').hidden,true);
$('empty-clear').onclick();assert.equal(shelfQuery,'');assert.equal(shelfFilter,'short');assert.equal(shelfSort,'oldest');assert.equal($('shelf-search').focused,true);assert.equal($('clear-search').hidden,true);assert.equal($('books').children.length,1);
""")

    def test_refresh_keeps_query_and_selection_while_results_and_totals_update(self):
        self.js(r"""
render({books});$('filter-long').onclick();changeSort('oldest');query('灯下');
response={books:[...books,{title:'灯下第二部',kind:'long',root:'/新增',available:true,key:'new',updated_at:'2026-10-06T00:00:00Z',updated_at_verified:true}]};await refresh();
assert.equal(shelfQuery,'灯下');assert.equal($('shelf-search').value,'灯下');assert.equal(shelfFilter,'long');assert.equal(shelfSort,'oldest');assert.equal($('books').children.length,2);assert.ok(labels()[0].includes('灯下的都市'));assert.ok(labels()[1].includes('灯下第二部'));assert.equal($('filter-all').textContent,'全部 5');assert.ok($('filter-summary').textContent.includes('长篇 · 2 部'));assert.ok($('filter-summary').textContent.includes('搜索“灯下”'));
""")

    def test_search_normalizes_case_width_and_matches_all_terms_from_evidenced_tags(self):
        self.js(r"""
assert.ok(shelfSearchMatch(books[2],'ｃａｓｅ　都市'));assert.ok(shelfSearchMatch(books[0],' 灯下 \n悬疑 '));assert.ok(shelfSearchMatch(books[0],'\t\n'));
assert.equal(shelfSearchMatch(books[0],'灯下 修仙'),false);assert.ok(shelfSearchMatch(books[0],'甲/同名'));assert.equal(shelfSearchMatch({...books[0],key:'TECHNICAL-ID',book_id:'TECHNICAL-ID'},'TECHNICAL-ID'),false);assert.equal(shelfSearchMatch(books[3],'未核对标签'),false);
assert.equal(shelfSearchMatch({title:'空白',platform_category:'都市',classification:{status:'missing',tags:['都市'],sources:[{value:'都市'}]}},'都市'),false);
render({books});query('ｃａｓｅ　都市');assert.equal($('books').children.length,1);assert.ok(labels()[0].includes('CASE Lab'));$('clear-search').onclick();assert.equal($('books').children.length,4);
""")

    def test_literal_titles_queries_and_source_details_remain_plain_text(self):
        self.js(r"""
const title='<img onerror=bad> **灯下**';render({books:[{...books[0],title}]});query('<img');
assert.equal($('books').children.length,1);const row=$('books').children[0];assert.ok(row.children[0].textContent.includes(title));assert.equal(row.children[0].title,row.children[0].textContent);assert.ok($('filter-summary').textContent.includes('<img'));
const classification=row.children.find(node=>node.className==='classification'),sources=classification.children.find(node=>node.tag==='details');assert.equal(sources.children[0].textContent,'题材依据');assert.ok(sources.children[1].textContent.includes('01_策划/分类.md'));assert.ok(sources.children[1].textContent.includes('题材：都市、悬疑'));
""")

    def test_progress_appears_only_for_verified_real_chapter_counts_without_finish_claims(self):
        self.js(r"""
for(const progress of [undefined,{verified:false,formal_chapters:55},{verified:true,formal_chapters:-1},{verified:true,formal_chapters:'55'},{verified:true,formal_chapters:1.5}])assert.equal(shelfProgressNode({...books[0],progress}),null);
assert.equal(shelfProgressNode({...books[0],available:false,progress:{verified:true,formal_chapters:55}}),null);assert.equal(shelfProgressNode({...books[2],progress:{verified:true,formal_chapters:0}}),null);
assert.equal(shelfProgressNode({...books[0],progress:{verified:true,formal_chapters:0}}).textContent,'已登记正式 0 章');
render({books:[{...books[0],progress:{verified:true,formal_chapters:131,next_chapter:132}}]});const progress=$('books').children[0].children.find(node=>node.className==='book-progress');assert.equal(progress.textContent,'已登记正式 131 章');assert.ok(!progress.textContent.includes('下一章'));assert.ok(!progress.textContent.includes('完结'));
""")

    def test_card_semantics_directory_disambiguation_and_open_failure_name_are_preserved(self):
        self.js(r"""
render({books});assert.notEqual(labels()[0],labels()[1]);
for(const row of $('books').children){assert.equal(row.children[0].tag,'strong');assert.equal(row.children[0].title,row.children[0].textContent);assert.ok(row.children.find(node=>node.className==='book-updated'));assert.ok(row.children.find(node=>node.tag==='details'));assert.ok(row.children.find(node=>node.tag==='button'));}
const unavailable=$('books').children.find(row=>row.children[0].textContent.includes('旧作品'));assert.ok(unavailable.children.find(node=>node.tag==='button').disabled);
const open=$('books').children[0].children.find(node=>node.tag==='button');assert.ok(open.attributes['aria-label'].includes('灯下的都市'));failOpen=true;await open.onclick();assert.ok($('message').textContent.includes('打开失败（灯下的都市）'));assert.equal(open.disabled,false);assert.deepEqual(rememberedShelfBooks,[],'an unconfirmed open must not become a restored session');
""")

    def test_layout_exposes_native_compatible_accessible_responsive_controls(self):
        for marker in ('id="shelf-intro"', 'id="shelf-footer"', 'id="add-book-panel"', 'id="book-path"', 'id="add"', 'id="refresh"', 'id="shelf-search" type="search"', 'for="shelf-search"', 'id="filter-summary" role="status"'):
            self.assertIn(marker, self.page)
        for identifier in ('new-book', 'add-book-panel', 'book-path', 'add',
                           'restore-backup', 'recovery-center', 'last-books',
                           'book-manager', 'workbench-theme'):
            self.assertEqual(self.page.count('id="' + identifier + '"'), 1)
        self.assertLess(self.page.index('id="new-book"'), self.page.index('id="add-book-panel"'))
        self.assertIn('role="search" aria-label="查找书架作品"', self.page)
        self.assertIn('grid-template-columns:repeat(3,minmax(0,1fr))', self.page)
        self.assertIn('grid-template-columns:repeat(2,minmax(0,1fr))', self.page)
        self.assertIn('@media(max-width:680px)', self.page)
        self.assertIn('@media(prefers-reduced-motion:reduce)', self.page)
        self.assertIn('-webkit-line-clamp:3', self.page)
        self.assertNotIn('<details id="add-book-panel" class="shelf-add" open', self.page)

    def test_management_menu_moves_original_evidence_location_and_status_handlers(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        source = base.story.workbench._library_management_script()
        decorator = source[source.index('function decorateBookManagement(){'):
                           source.index("$('new-book').onclick")]
        fixture = r"""
const assert=require('assert');
class E {
 constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.className='';this.parentElement=null;this.textContent='';}
 get classList(){return {contains:value=>this.className.split(/\s+/).includes(value)};}
 append(...nodes){for(const node of nodes){if(node.parentElement){const siblings=node.parentElement.children;siblings.splice(siblings.indexOf(node),1);}node.parentElement=this;this.children.push(node);}}
 querySelectorAll(selector){const result=[];for(const child of this.children){if(child.classList.contains(selector.slice(1)))result.push(child);result.push(...child.querySelectorAll(selector));}return result;}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
}
const document={createElement:tag=>new E(tag)},books=new E(),error=new E(),$=id=>id==='books'?books:error;
let managerGeneration=0,manager={open:false},saved=0;
const calls=[];for(const name of ['showBookBackup','showRecoveries','showBookService','showRelinkBook','showRemoveBook','showTrashBook'])globalThis[name]=book=>calls.push([name,book.key]);
const shelfBooks=[{key:'a',available:true},{key:'b',available:true},{key:'missing',available:false}];
function card(book,root){
 const row=new E();row.className='book';row.dataset.bookKey=book.key;
 const title=new E('strong');title.textContent='同名作品';const genres=new E();genres.className='classification';
 const updated=new E('time');updated.className='book-updated';updated.textContent=book.key==='missing'?'更新时间未确认':'更新时间：2026/10/19 17:00:00';updated.dateTime='2026-10-19T09:00:00Z';updated.title='原始时间来源';
 const evidence=new E('details');evidence.className='classification-sources';const proof=new E('p');proof.textContent=root+'/01_策划/分类.md：都市';evidence.append(proof);genres.append(evidence);
 const location=new E('details');location.textContent=root;
 const status=new E('details');status.className='book-status-editor';const save=new E('button');save.onclick=()=>saved++;status.append(save);
 const open=new E('button');open.onclick=()=>calls.push(['open',book.key]);row.append(title,updated,genres,location,open,status);books.append(row);
 return {row,genres,evidence,proof,location,status,save,open,updated};
}
const fixtures=shelfBooks.map((book,index)=>card(book,'/合成目录/'+index));
"""
        checks = r"""
fixtures[2].genres.dataset.classificationEmpty='true';fixtures[2].genres.hidden=true;
decorateBookManagement();
for(const fixture of fixtures){
 const {row,genres,evidence,proof,location,status,save,open,updated}=fixture,menu=row.querySelector('.book-management'),panel=menu.querySelector('.book-management-panel');
 assert.ok(menu);assert.equal(menu.open,undefined,'management must start collapsed');
 assert.equal(genres.children.length,0);assert.strictEqual(evidence.parentElement,panel);assert.strictEqual(location.parentElement,panel);assert.strictEqual(status.parentElement,panel);
 assert.strictEqual(updated.parentElement,panel);assert.equal(updated.title,'原始时间来源');assert.equal(updated.dateTime,'2026-10-19T09:00:00Z');assert.ok(updated.textContent.includes('更新时间'));
 assert.strictEqual(evidence.children[0],proof);assert.ok(proof.textContent.includes(location.textContent));assert.equal(row.children.filter(child=>child.tagName==='DETAILS').length,1);
 assert.strictEqual(row.children.find(child=>child.tagName==='BUTTON'),open);save.onclick();
 const actions=panel.children.filter(child=>child.tagName==='BUTTON');assert.deepEqual(actions.map(action=>action.textContent),['备份','找回文字','保存与停止','重新定位','移出书架','删除至回收站']);
 for(const action of actions)assert.equal(action.disabled,!shelfBooks.find(book=>book.key===row.dataset.bookKey).available&&!['重新定位','移出书架'].includes(action.textContent));
}
assert.equal(saved,3);assert.notEqual(fixtures[0].location.textContent,fixtures[1].location.textContent);
assert.equal(fixtures[2].genres.hidden,true);assert.equal(fixtures[2].evidence.parentElement.className,'book-management-panel','folded empty metadata must keep its reading evidence in management');
decorateBookManagement();for(const {row}of fixtures)assert.equal(row.querySelectorAll('.book-management').length,1);
fixtures[0].open.onclick();assert.deepEqual(calls,[['open','a']]);
"""
        result = subprocess.run([node, '-'], input=fixture + decorator + checks, encoding='utf-8',
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()

"""Shelf filters, refresh ownership and deferred return refresh behavior."""
import shutil
import subprocess
import unittest

import test_workbench as base


class WorkbenchShelfFeedbackTests(unittest.TestCase):
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
 constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attributes={};this.value='';this._text='';this.parentNode=null;}
 set textContent(value){this.replaceChildren();this._text=String(value);}
 get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
 replaceChildren(...children){for(const child of this.children)child.parentNode=null;this.children=[];this._text='';this.append(...children);}
 append(...children){for(const child of children){child.remove?.();child.parentNode=this;this.children.push(child);}}
 remove(){if(this.parentNode){const siblings=this.parentNode.children;siblings.splice(siblings.indexOf(this),1);this.parentNode=null;}}
 setAttribute(key,value){this.attributes[key]=value;}
 focus(){this.focused=true;}
 click(){}
}
const nodes={},$=id=>nodes[id]||(nodes[id]=new E()),events={window:{},document:{}};
const allChildren=node=>[node,...node.children.flatMap(allChildren)];
let opened=null,now=10000,nextTimer=0,timers=new Map(),busy=false,managerBusy=false;
const setTimeout=(callback,delay)=>{const id=++nextTimer;timers.set(id,{callback,at:now+delay});return id;};
Date.now=()=>now;
async function advance(milliseconds){now+=milliseconds;for(let i=0;i<20;i++){const entry=[...timers].find(([,timer])=>timer.at<=now);if(!entry)break;timers.delete(entry[0]);entry[1].callback();await settle();}}
async function settle(){for(let i=0;i<8;i++)await Promise.resolve();}
const window={addEventListener:(type,handler)=>{(events.window[type]??=[]).push(handler);}};
const document={visibilityState:'visible',activeElement:null,createElement:tag=>new E(tag),createTextNode:text=>{const node=new E();node.textContent=text;return node;},querySelector:()=>opened,querySelectorAll:()=>allChildren($('books')).filter(node=>node.tagName==='SELECT'&&node.dataset.bookStatusOriginal!==undefined),addEventListener:(type,handler)=>{(events.document[type]??=[]).push(handler);}};
const emit=(target,type,event={})=>{for(const handler of events[target][type]||[])handler(event);};
let stored=null;const localStorage={getItem:()=>stored,setItem:(key,value)=>{stored=value;}};
let response={books:[]},failure=null,pending=null,calls=0,decorations=0;
const call=async()=>{calls++;if(failure)throw Error(failure);if(pending)return pending;return response;};
function decorateBookManagement(){decorations++;}
function rememberShelfBook(){}
const book=(key,kind='long',status='serializing',extra={})=>({title:key,key,root:'/合成/'+key,kind,kind_verified:true,available:true,writing_status:{value:status,verified:true,source:'author_mark'},...extra});
const books=[book('连载稿'),book('完本稿','short','completed'),book('分析甲','analysis')];
const filter=value=>{$('shelf-status').value=value;$('shelf-status').onchange();};
const query=value=>{$('shelf-search').value=value;$('shelf-search').oninput();};
"""
        result = subprocess.run(
            [node, '-e', mock + self.source + '\n(async()=>{' + test +
             '\n})().catch(error=>{console.error(error);process.exitCode=1;});'],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_analysis_drops_inapplicable_status_and_preserves_query_and_sort(self):
        self.js(r"""
render({books});filter('completed');shelfSort='oldest';query('分析');$('filter-analysis').onclick();
assert.equal(shelfStatusFilter,'all');assert.equal($('shelf-status').disabled,true);assert.equal(shelfQuery,'分析');assert.equal(shelfSort,'oldest');assert.equal($('books').children.length,1);assert.ok($('books').textContent.includes('分析甲'));assert.ok(!$('books').textContent.includes('暂无作品'));assert.equal(JSON.parse(stored).status,'all');
$('filter-all').onclick();assert.equal($('shelf-status').disabled,false);
stored=JSON.stringify({filter:'analysis',status:'completed',sort:'oldest'});restoreShelfView();render({books});assert.equal(shelfStatusFilter,'all');assert.equal($('books').children.length,1);
""")

    def test_empty_state_explains_status_filter_and_clear_filters_keeps_sort(self):
        self.js(r"""
render({books});$('filter-long').onclick();filter('completed');shelfSort='oldest';
assert.ok($('books').textContent.includes('当前分类有 1 部作品'));assert.ok($('books').textContent.includes('已完本'));assert.ok(!$('books').textContent.includes('当前分类暂无作品'));assert.equal($('clear-filters').hidden,false);
query('连载');assert.ok($('books').textContent.includes('当前搜索与本地状态筛选没有匹配作品'));$('clear-search').onclick();assert.equal(shelfFilter,'long');assert.equal(shelfStatusFilter,'completed');
$('clear-filters').onclick();assert.equal(shelfFilter,'all');assert.equal(shelfStatusFilter,'all');assert.equal(shelfQuery,'');assert.equal(shelfSort,'oldest');assert.equal($('books').children.length,3);assert.equal($('clear-filters').hidden,true);
""")

    def test_successful_refresh_removes_only_the_owned_error_node(self):
        self.js(r"""
failure='临时失败';await refresh();const owned=$('message').children[0];assert.ok(owned.textContent.includes('刷新书架失败：临时失败'));
failure=null;response={books};await refresh();assert.equal($('message').textContent,'');assert.equal(owned.parentNode,null);
failure='又失败';await refresh();const other=document.createElement('span');other.textContent='刷新书架失败：又失败';$('message').replaceChildren(other);failure=null;await refresh();assert.strictEqual($('message').children[0],other,'same text from a different operation must survive');
$('message').textContent='作品状态已更新';failure='网络暂不可达';await refresh({automatic:true});assert.equal($('message').textContent,'作品状态已更新','background failure must preserve operation receipt');
""")

    def test_return_events_coalesce_and_refresh_keeps_the_current_view(self):
        self.js(r"""
render({books});$('filter-long').onclick();filter('serializing');shelfSort='oldest';query('连载');document.activeElement=$('shelf-search');
response={books:[book('连载稿','long','serializing',{progress:{verified:true,formal_chapters:12}}),...books.slice(1)]};installShelfRefreshHooks();
emit('window','pageshow',{persisted:false});assert.equal(timers.size,0,'initial pageshow must not duplicate initial load');
emit('window','focus');emit('window','story-shelf-return');emit('window','pageshow',{persisted:true});emit('document','visibilitychange');assert.equal(timers.size,1);await advance(120);
assert.equal(calls,1);assert.equal(shelfFilter,'long');assert.equal(shelfStatusFilter,'serializing');assert.equal(shelfSort,'oldest');assert.equal(shelfQuery,'连载');assert.ok($('books').textContent.includes('已登记正式 12 章'));
emit('window','focus');emit('document','visibilitychange');await advance(120);assert.equal(calls,1);await advance(1000);assert.equal(calls,2,'bursts are throttled into one later refresh');
document.activeElement=$('shelf-status');emit('window','story-shelf-return');await advance(1000);assert.equal(calls,3,'a persisted filter focus must not prevent returning to updated metadata');
""")

    def test_return_refresh_waits_for_busy_menus_dialogs_path_and_unsaved_status(self):
        self.js(r"""
render({books});response={books};installShelfRefreshHooks();busy=true;emit('window','focus');await advance(120);assert.equal(calls,0);assert.equal(shelfRefreshPending,true);
busy=false;managerBusy=true;await advance(350);assert.equal(calls,0);managerBusy=false;opened=new E('details');await advance(350);assert.equal(calls,0);opened=new E('dialog');await advance(350);assert.equal(calls,0);
opened=null;$('book-path').value='/尚未登记的目录';await advance(350);assert.equal(calls,0);$('book-path').value='';document.activeElement=new E('select');await advance(350);assert.equal(calls,0);document.activeElement=null;
const select=document.querySelectorAll()[0],original=select.value;select.value='completed';await advance(350);assert.equal(calls,0);assert.equal(select.value,'completed','unsaved mark must not be reset');select.value=original;emit('document','change');await advance(350);assert.equal(calls,1);assert.equal(shelfRefreshPending,false);
""")

    def test_hidden_page_waits_and_in_flight_results_cannot_replace_opened_ui(self):
        self.js(r"""
render({books});response={books};installShelfRefreshHooks();document.visibilityState='hidden';emit('window','focus');await advance(120);assert.equal(calls,0);assert.equal(timers.size,0);assert.equal(shelfRefreshPending,true);
document.visibilityState='visible';emit('document','visibilitychange');let resolve;pending=new Promise(done=>{resolve=done;});await advance(120);assert.equal(calls,1);const before=decorations;opened=new E('details');resolve({books:[book('新目录内容')]});await settle();assert.equal(decorations,before,'a menu opened during fetch must survive');assert.equal(shelfRefreshPending,true);
opened=null;pending=null;response={books:[book('新目录内容')]};await advance(1000);assert.equal(calls,2);assert.ok($('books').textContent.includes('新目录内容'));
""")

    def test_an_operation_rendered_during_fetch_wins_until_a_new_refresh(self):
        self.js(r"""
render({books});let resolve;pending=new Promise(done=>{resolve=done;});const refreshing=refresh({automatic:true});render({books:[book('刚保存的状态','long','completed')]});resolve({books});await refreshing;assert.ok($('books').textContent.includes('刚保存的状态'));assert.equal(shelfRefreshPending,true);
pending=null;response={books:[book('刚保存的状态','long','completed')]};await advance(1000);assert.equal(calls,2);assert.ok($('books').textContent.includes('刚保存的状态'));
""")


if __name__ == '__main__':
    unittest.main()

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

    def js(self, test, call_source=None):
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
let opened=null,now=10000,nextTimer=0,timers=new Map(),busy=false,managerBusy=false,lastBooksBusy=false;
const setTimeout=(callback,delay)=>{const id=++nextTimer;timers.set(id,{callback,at:now+delay});return id;};
Date.now=()=>now;
async function advance(milliseconds){now+=milliseconds;for(let i=0;i<20;i++){const entry=[...timers].find(([,timer])=>timer.at<=now);if(!entry)break;timers.delete(entry[0]);entry[1].callback();await settle();}}
async function settle(){for(let i=0;i<8;i++)await Promise.resolve();}
const window={addEventListener:(type,handler)=>{(events.window[type]??=[]).push(handler);}};
const document={visibilityState:'visible',activeElement:null,createElement:tag=>new E(tag),createTextNode:text=>{const node=new E();node.textContent=text;return node;},querySelector:()=>opened,querySelectorAll:()=>allChildren($('books')).filter(node=>node.tagName==='SELECT'&&node.dataset.bookStatusOriginal!==undefined),addEventListener:(type,handler)=>{(events.document[type]??=[]).push(handler);}};
const emit=(target,type,event={})=>{for(const handler of events[target][type]||[])handler(event);};
let stored=null;const localStorage={getItem:()=>stored,setItem:(key,value)=>{stored=value;}};
let response={books:[]},failure=null,pending=null,calls=0,decorations=0;const requests=[];
const call=async(action,data={})=>{requests.push({action,data});calls++;if(failure)throw Error(failure);if(pending)return pending;return response;};
function decorateBookManagement(){decorations++;}
function rememberShelfBook(){}
const book=(key,kind='long',status='serializing',extra={})=>({title:key,key,root:'/合成/'+key,kind,kind_verified:true,available:true,writing_status:{value:status,verified:true,source:'author_mark'},...extra});
const books=[book('连载稿'),book('完本稿','short','completed'),book('分析甲','analysis')];
const filter=value=>{$('shelf-status').value=value;$('shelf-status').onchange();};
const query=value=>{$('shelf-search').value=value;$('shelf-search').oninput();};
const statusSelect=key=>document.querySelectorAll().find(select=>select.dataset.bookStatusKey===key);
const statusEditor=key=>allChildren($('books')).find(node=>node.className==='book-status-editor'&&allChildren(node).some(child=>child.dataset.bookStatusKey===key));
const statusSave=key=>statusEditor(key).children.find(node=>node.tagName==='BUTTON');
const chooseStatus=(key,value)=>{const select=statusSelect(key);select.value=value;select.onchange();};
"""
        if call_source is not None:
            original = "const call=async(action,data={})=>{requests.push({action,data});calls++;if(failure)throw Error(failure);if(pending)return pending;return response;};"
            mock = mock.replace(original, call_source)
        result = subprocess.run(
            [node, '-'], input=mock + self.source + '\n(async()=>{' + test +
             '\n})().catch(error=>{console.error(error);process.exitCode=1;});', encoding='utf-8',
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_browser_close_warns_for_running_operations_and_hidden_status_drafts(self):
        self.js(r"""
render({books});installShelfRefreshHooks();
const closing=()=>{let prevented=false;const event={preventDefault:()=>{prevented=true;}};emit('window','beforeunload',event);return {prevented,event};};
assert.equal(closing().prevented,false);busy=true;assert.equal(closing().prevented,true);busy=false;
managerBusy=true;assert.equal(closing().prevented,true);managerBusy=false;
lastBooksBusy=true;assert.equal(closing().prevented,true);lastBooksBusy=false;
statusSaving.add('连载稿');assert.equal(closing().prevented,true);statusSaving.clear();
chooseStatus('连载稿','completed');query('分析');const close=closing();assert.equal(close.prevented,true);assert.equal(close.event.returnValue,'');
$('clear-filters').onclick();chooseStatus('连载稿','serializing');assert.equal(closing().prevented,false);
""")

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

    def test_unsaved_marks_survive_search_type_sort_and_clearing_filters(self):
        self.js(r"""
render({books});chooseStatus('连载稿','completed');const draft=shelfStatusDrafts.get('连载稿');assert.ok(statusEditor('连载稿').textContent.includes('未保存'));
query('分析');assert.equal(statusSelect('连载稿'),undefined);assert.equal(shelfRefreshBlocked(),true,'hidden drafts must still block automatic refresh');
$('shelf-order').value='oldest';$('shelf-order').onchange();$('filter-analysis').onclick();$('clear-filters').onclick();
assert.equal(statusSelect('连载稿').value,'completed');assert.strictEqual(shelfStatusDrafts.get('连载稿'),draft);assert.equal(shelfSort,'oldest');
filter('completed');assert.equal(statusSelect('连载稿'),undefined,'unsaved marks must not change saved-state filtering');$('clear-filters').onclick();assert.equal(statusSelect('连载稿').value,'completed');
chooseStatus('连载稿','serializing');assert.equal(shelfStatusDrafts.size,0);assert.ok(!statusEditor('连载稿').textContent.includes('当前选择尚未保存'));
""")

    def test_slow_manual_refresh_keeps_new_selection_and_rejects_an_older_catalog(self):
        self.js(r"""
render({books});let resolve;pending=new Promise(done=>{resolve=done;});const refreshing=refresh();chooseStatus('连载稿','completed');resolve({books});await refreshing;
assert.equal(statusSelect('连载稿').value,'completed');assert.equal(shelfStatusDrafts.get('连载稿').value,'completed');assert.ok(statusEditor('连载稿').textContent.includes('尚未保存'));
chooseStatus('连载稿','serializing');let resolveOlder;pending=new Promise(done=>{resolveOlder=done;});const older=refresh();render({books:[book('已完成的新目录','long','completed')]});resolveOlder({books});await older;
assert.ok($('books').textContent.includes('已完成的新目录'));assert.equal(statusSelect('连载稿'),undefined);assert.equal(shelfRefreshPending,true);
""")

    def test_confirmed_save_clears_only_its_draft_and_keeps_other_hidden_drafts(self):
        self.js(r"""
render({books});chooseStatus('连载稿','completed');chooseStatus('完本稿','serializing');const other=shelfStatusDrafts.get('完本稿');
let resolve;pending=new Promise(done=>{resolve=done;});const saving=statusSave('连载稿').onclick();assert.equal(statusSelect('连载稿').disabled,true);assert.ok($('books').textContent.includes('连载中'));
query('分析');resolve({books:[book('连载稿','long','completed'),...books.slice(1)]});await saving;assert.equal(shelfStatusDrafts.has('连载稿'),false);assert.strictEqual(shelfStatusDrafts.get('完本稿'),other);
$('clear-search').onclick();assert.equal(statusSelect('连载稿').value,'completed');assert.equal(statusSelect('完本稿').value,'serializing');assert.ok(statusEditor('完本稿').textContent.includes('未保存'));assert.equal(statusSaving.size,0);
""")

    def test_failed_save_retains_selection_and_error_through_hiding_and_retry(self):
        self.js(r"""
render({books});chooseStatus('连载稿','completed');let reject;pending=new Promise((done,deny)=>{reject=deny;});const saving=statusSave('连载稿').onclick();query('分析');reject(Error('目录暂不可达'));await saving;
assert.equal(shelfStatusDrafts.get('连载稿').value,'completed');$('clear-search').onclick();assert.equal(statusSelect('连载稿').value,'completed');assert.ok(statusEditor('连载稿').textContent.includes('保存未确认，当前选择已保留：目录暂不可达'));assert.ok($('message').textContent.includes('尚未确认保存'));assert.equal(statusSelect('连载稿').disabled,false);
pending=null;response={books:[book('连载稿','long','completed'),...books.slice(1)]};await statusSave('连载稿').onclick();assert.equal(shelfStatusDrafts.size,0);assert.ok($('message').textContent.includes('已更新'));
""")

    def test_out_of_order_saves_do_not_revert_another_confirmed_book(self):
        self.js(r"""
render({books});chooseStatus('连载稿','completed');chooseStatus('完本稿','serializing');let resolveFirst,resolveSecond;
pending=new Promise(done=>{resolveFirst=done;});const first=statusSave('连载稿').onclick();pending=new Promise(done=>{resolveSecond=done;});const second=statusSave('完本稿').onclick();
resolveSecond({books:[book('连载稿','long','completed'),book('完本稿','short','serializing'),books[2]]});await second;assert.equal(statusSaving.has('连载稿'),true);
resolveFirst({books:[book('连载稿','long','completed'),books[1],books[2]]});await first;
assert.equal(shelfBooks.find(book=>book.key==='连载稿').writing_status.value,'completed');assert.equal(shelfBooks.find(book=>book.key==='完本稿').writing_status.value,'serializing');assert.equal(statusSelect('完本稿').value,'serializing');assert.equal(shelfStatusDrafts.size,0);assert.equal(statusSaving.size,0);
""")

    def test_unconfirmed_success_response_keeps_the_unsaved_choice(self):
        self.js(r"""
render({books});chooseStatus('连载稿','completed');response={books};await statusSave('连载稿').onclick();
assert.equal(shelfBooks[0].writing_status.value,'serializing');assert.equal(statusSelect('连载稿').value,'completed');assert.equal(shelfStatusDrafts.size,1);assert.ok(statusEditor('连载稿').textContent.includes('未确认所选作品及状态'));assert.ok(!$('message').textContent.includes('已更新'));
""")

    def test_discard_notice_counts_hidden_drafts_and_does_not_recapture_them(self):
        self.js(r"""
render({books});assert.equal($('shelf-draft-feedback').hidden,true);chooseStatus('连载稿','completed');chooseStatus('完本稿','serializing');
assert.equal($('shelf-draft-feedback').hidden,false);assert.ok($('shelf-draft-summary').textContent.includes('2 部作品'));assert.ok($('shelf-draft-summary').textContent.includes('本地状态选择'));assert.ok($('shelf-draft-summary').textContent.includes('不修改稿件'));
query('分析');assert.equal(document.querySelectorAll().length,0);assert.equal($('shelf-draft-feedback').hidden,false);$('discard-status-drafts').onclick();assert.equal(shelfStatusDrafts.size,0);assert.equal($('shelf-draft-feedback').hidden,true);$('clear-search').onclick();assert.equal(statusSelect('连载稿').value,'serializing');assert.equal(statusSelect('完本稿').value,'completed');assert.equal(calls,0,'discard must not write to the service');
chooseStatus('连载稿','completed');$('discard-status-drafts').onclick();assert.equal(shelfStatusDrafts.size,0,'a visible stale select must not repopulate the cleared map');assert.equal(statusSelect('连载稿').value,'serializing');
""")

    def test_removed_and_unavailable_books_have_a_way_to_discard_orphaned_drafts(self):
        self.js(r"""
for(const catalog of [{books:books.slice(1)},{books:[{...books[0],available:false},...books.slice(1)]}]){
 render({books});chooseStatus('连载稿','completed');response=catalog;await refresh();assert.equal(statusSelect('连载稿'),undefined);assert.equal(shelfStatusDrafts.size,1);assert.equal($('shelf-draft-feedback').hidden,false);assert.equal(shelfRefreshBlocked(),true);
 const before=calls;$('discard-status-drafts').onclick();assert.equal(calls,before);assert.equal(shelfStatusDrafts.size,0);assert.equal($('shelf-draft-feedback').hidden,true);assert.equal(shelfRefreshBlocked(),false);
}
""")

    def test_discard_waits_for_submitted_save_then_updates_after_failure_and_success(self):
        self.js(r"""
render({books});chooseStatus('连载稿','completed');let reject;pending=new Promise((done,deny)=>{reject=deny;});const saving=statusSave('连载稿').onclick();assert.equal($('discard-status-drafts').disabled,true);$('discard-status-drafts').onclick();assert.equal(shelfStatusDrafts.size,1);
reject(Error('暂不可达'));await saving;assert.equal($('discard-status-drafts').disabled,false);assert.equal($('shelf-draft-feedback').hidden,false);assert.ok($('shelf-draft-summary').textContent.includes('1 部作品'));$('discard-status-drafts').onclick();assert.equal(shelfStatusDrafts.size,0);
chooseStatus('连载稿','completed');pending=null;response={books:[book('连载稿','long','completed'),...books.slice(1)]};await statusSave('连载稿').onclick();assert.equal($('shelf-draft-feedback').hidden,true);assert.equal($('shelf-draft-summary').textContent,'');
""")


if __name__ == '__main__':
    unittest.main()

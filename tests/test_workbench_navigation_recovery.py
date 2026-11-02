"""Navigation recovery keeps author's buffers and exposes complete context on demand."""
import shutil
import subprocess
import unittest

import test_workbench as base


class WorkbenchNavigationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        w = base.story.workbench
        page = w._editor_page(w.snapshot(self.fixture.book), {}, 'navigation-test').decode()
        self.script = page.split('<script>', 1)[1].split('</script>', 1)[0]

    def js(self, source, assertions):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        mock = r"""
const assert=require('node:assert/strict');
class E {
 constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.value='';this.textContent='';this.open=false;this.hidden=false;this.disabled=false;this.parentElement={scrollTop:25};}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
 get firstElementChild(){return this.children[0];}
 querySelectorAll(selector){const all=this.children.flatMap(child=>[child,...child.querySelectorAll(selector)]);return selector==='details[open]'?all.filter(child=>child.tag==='details'&&child.open):all.filter(child=>child.tag==='details');}
}
const elements={},$=id=>elements[id]||(elements[id]=new E());
const document={createElement:tag=>new E(tag)};
"""
        result = subprocess.run([node, '-'], input=mock + source + '\n' + assertions,
                                text=True, encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_long_context_is_complete_and_expansion_belongs_to_selected_document(self):
        source = self.script[self.script.index('function showChapterInfo'):self.script.index('function readableLength')]
        self.js("let currentCatalog={files:[]};\n" + source, r"""
const goal='目标原文。'.repeat(35)+'全文末尾✅';
const stop='停笔第一行\n第二行\n第三行\n最后一行';
const d={id:'formal:1',is_prose:true,context:{chapter:1,plan:{goal,stop},revision:1}};
const sections=()=>$('chapter-info').children.filter(node=>node.className==='context-summary');
showChapterInfo(d);assert.equal(sections().length,2);
assert.ok(sections().every(node=>!node.open));
assert.equal(sections()[0].children[1].textContent,goal,'full text must never be truncated');
assert.equal(sections()[1].children[1].textContent,stop,'line breaks remain exact');
assert.ok(sections()[0].firstElementChild.children[0].textContent.endsWith('…'));
sections()[0].open=true;showChapterInfo(d);assert.equal(sections()[0].open,true);
showChapterInfo({...d,id:'formal:2',context:{...d.context,chapter:2}});
assert.ok(sections().every(node=>!node.open),'another document starts collapsed');
showChapterInfo({...d,context:{...d.context,plan:{goal:'短目标',stop:'短停笔'}}});
assert.equal(sections().length,0,'short guidance stays directly visible');
assert.equal($('chapter-info').children[1].textContent,'短目标');
""")

    def catalog_source(self):
        source = self.script[self.script.index('async function catalog('):self.script.index('async function recover(')]
        source += self.script[self.script.index(" $('materials-more').onclick="):self.script.index(' const narrow=matchMedia')]
        return r"""
let active='formal:1',loading=false,reloadPending=false,pendingFocus=false,queryTimer=null;
let offset=10,viewMode='chapters',currentCatalog={materials:{has_more:false},files:[]};
const draft={id:active,text:'原文',value:'仍未保存的文字',editing:true,positions:{edit:37}};
const docs=new Map([[active,draft]]),original=JSON.stringify([...docs]),requests=[],notes=[];
$('text').value=draft.value;$('search').value='章名';
const chapterNumber=()=>1,locateActive=()=>{},badge=()=>{},note=message=>notes.push(message);
const drawCatalog=c=>{currentCatalog=c;},openDoc=()=>{throw Error('Cannot replace buffer');};
let failure=null,reply=null;
async function call(action,payload){requests.push(payload);if(failure)throw failure;return reply;}
const packet=()=>({materials:{cursor:'new-cursor',loaded:3,has_more:false,complete:true},warnings:[],chapters:[],files:[]});
""" + source

    def test_expired_or_changed_catalog_offers_safe_explicit_restart(self):
        self.js(self.catalog_source(), r"""
(async()=>{
 for(const code of ['workbench_material_cursor_expired','workbench_material_cursor_changed']){
  catalog.materialCursor='expired-cursor';failure=Object.assign(Error('材料目录已过期'),{code});
  await catalog(false,false,true);
  assert.equal($('materials-more').textContent,'重新加载材料目录');
  assert.equal($('materials-more').hidden,false);assert.equal($('materials-more').disabled,false);
  assert.equal($('materials-status').dataset.state,'error');assert.equal(catalog.materialsNeedReload,true);
  const before=requests.length;failure=null;reply=packet();await $('materials-more').onclick();
  assert.equal(requests.length,before+1,'one explicit click issues one read');
  assert.equal(requests.at(-1).material_cursor,null);assert.equal(requests.at(-1).material_more,false);
  assert.equal(requests.at(-1).query,'章名');assert.equal(requests.at(-1).offset,10);
  assert.equal(catalog.materialsNeedReload,false);assert.equal($('materials-more').hidden,true);
  assert.equal($('materials-status').dataset.state,'');assert.ok(notes.at(-1).includes('已重新加载'));
  assert.equal(JSON.stringify([...docs]),original);assert.equal($('text').value,draft.value);
  assert.equal(active,'formal:1');assert.equal($('list').parentElement.scrollTop,25);
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
""")

    def test_failed_restart_remains_retryable_without_claiming_success(self):
        self.js(self.catalog_source(), r"""
(async()=>{
 failure=Object.assign(Error('材料过期'),{code:'workbench_material_cursor_expired'});await catalog();
 failure=Error('网络暂不可用');await $('materials-more').onclick();
 assert.equal(catalog.materialsNeedReload,true);assert.equal($('materials-more').disabled,false);
 assert.equal($('materials-more').textContent,'重新加载材料目录');assert.equal(notes.at(-1),'网络暂不可用');
 assert.equal(JSON.stringify([...docs]),original);
 failure=null;reply=packet();await $('materials-more').onclick();
 assert.equal(catalog.materialsNeedReload,false);assert.equal(requests.at(-1).material_cursor,null);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")

    def test_obsolete_failure_does_not_replace_current_navigation_feedback(self):
        self.js(self.catalog_source() + r"""
call=async()=>{await Promise.resolve();$('search').value='新的查询';throw Object.assign(Error('旧材料过期'),{code:'workbench_material_cursor_expired'});};
""", r"""
(async()=>{
 $('materials-status').textContent='当前目录';await catalog();
 assert.equal(catalog.materialsNeedReload,undefined);assert.deepEqual(notes,[]);
 assert.equal($('materials-status').textContent,'当前目录');assert.equal(JSON.stringify([...docs]),original);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")


if __name__ == '__main__':
    unittest.main()

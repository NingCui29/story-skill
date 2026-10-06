"""Exercise context visibility, persisted reading choices, and drawer focus together."""
import shutil
import subprocess
import unittest

import test_workbench as base


class WorkbenchFocusRegressions(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        page = base.story.workbench._editor_page(
            base.story.workbench.snapshot(self.fixture.book), {}, 'test'
        ).decode()
        script = page.split('<script>', 1)[1].split('</script>', 1)[0]
        self.source = '\n'.join((
            script[script.index('function panelFocusables'):script.index('let compareSequence')],
            script[script.index('function applyReading'):script.index('async function loadBooks')],
            script[script.index('function fitColumns'):script.index('function dirty(d)')],
        ))

    def js(self, test):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        mock = r"""
const assert=require('assert');
let nodes,classes,media,innerWidth=1200,styles,active=null;
const storage=new Map(),writes=[];
const localStorage={getItem:k=>storage.get(k)||null,setItem(k,v){storage.set(k,v);writes.push(k);}};
class Element {
 constructor(id){this.id=id;this.value='';this.textContent='';this.checked=false;this.disabled=false;this.inert=false;this.tabIndex=0;this.parentElement=null;this.tagName='BUTTON';this.children=[];this.attributes={};this.listeners={};}
 setAttribute(k,v){this.attributes[k]=v;}
 addEventListener(k,fn){this.listeners[k]=fn;}
 focus(){document.activeElement=this;}
 getClientRects(){return [1];}
 querySelectorAll(){return this.children;}
}
const $=id=>nodes[id]||(nodes[id]=new Element(id));
const document={activeElement:null,listeners:{},addEventListener(k,fn){this.listeners[k]=fn;},querySelector:selector=>$(selector)};
const window={addEventListener(){}},matchMedia=()=>media;
const docs=new Map(),catalog=()=>{},openDoc=()=>{},leaveComparison=()=>{},stepDiff=()=>{},stepSearch=()=>{},show=()=>{},scheduleMetrics=()=>{},dirty=()=>false,fullSearch=()=>{},loadBooks=()=>{},note=()=>{};
let closePanels=()=>{},viewMode='chapters',offset=0,fullSearchOffset=0;
function freshPage(width=1200){
 innerWidth=width;nodes={};classes=new Set();styles=new Map();document.activeElement=null;document.listeners={};
 document.body={classList:{contains:k=>classes.has(k),add(...keys){keys.forEach(k=>classes.add(k));},remove(...keys){keys.forEach(k=>classes.delete(k));},toggle(k,force){const on=force===undefined?!classes.has(k):force;on?classes.add(k):classes.delete(k);return on;}}};
 document.documentElement={style:{setProperty:(k,v)=>styles.set(k,v)}};
 media={matches:width<=820,listeners:{},addEventListener(k,fn){this.listeners[k]=fn;}};
 $('font-size').value='19';$('line-height').value='1.8';$('font-family').value='serif';
 for(const [panelId,closeId] of [['book-context','context-close'],['book-nav','nav-close']]){
  const panel=$(panelId),close=$(closeId);panel.tabIndex=-1;close.parentElement=panel;panel.children=[close];
 }
 setupWorkspace();
}
const contextVisible=()=>!classes.has('context-closed')&&!classes.has('focus-reading');
const reading=()=>JSON.parse(storage.get('story-reading'));
const columns=()=>JSON.parse(storage.get('story-columns'));
"""
        result = subprocess.run(
            [node, '-e', mock + self.source + '\n' + test],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_focus_hidden_context_opens_once_then_closes_and_persists_both(self):
        self.js(r"""
freshPage();
$('focus-reading').checked=true;$('focus-reading').onchange();
assert.ok(!contextVisible());assert.equal($('context-toggle').textContent,'辅助信息');
const start=writes.length;$('context-toggle').onclick();
assert.ok(contextVisible());assert.ok(!$('focus-reading').checked);
assert.equal($('context-toggle').textContent,'收起辅助栏');assert.equal($('context-toggle').attributes['aria-expanded'],'true');
assert.equal(reading().focus,false);assert.equal(columns().contextClosed,false);
assert.deepEqual(writes.slice(start),['story-reading','story-columns']);
$('context-toggle').onclick();
assert.ok(!contextVisible());assert.equal($('context-toggle').attributes['aria-expanded'],'false');
assert.equal(reading().focus,false);assert.equal(columns().contextClosed,true);
""")

    def test_already_closed_context_opens_once_while_focused(self):
        self.js(r"""
storage.set('story-columns',JSON.stringify({left:236,right:248,contextClosed:true}));
storage.set('story-reading',JSON.stringify({size:'19',line:'1.8',font:'serif',focus:true}));
freshPage();
assert.ok(classes.has('context-closed'));assert.ok(classes.has('focus-reading'));
$('context-toggle').onclick();
assert.ok(contextVisible());assert.equal(columns().contextClosed,false);assert.equal(reading().focus,false);
$('context-toggle').onclick();assert.ok(!contextVisible());
""")

    def test_same_address_reload_restores_last_context_and_reading_choices(self):
        self.js(r"""
storage.set('story-columns',JSON.stringify({left:300,right:280,navClosed:true,contextClosed:false}));
storage.set('story-reading',JSON.stringify({size:'22',line:'2.2',font:'sans',focus:true}));
freshPage();$('context-toggle').onclick();
freshPage();
assert.ok(contextVisible());assert.ok(!$('focus-reading').checked);assert.ok(classes.has('nav-closed'));
assert.equal($('font-size').value,'22');assert.equal($('line-height').value,'2.2');assert.equal($('font-family').value,'sans');
assert.equal(styles.get('--reader-size'),'22px');assert.equal(styles.get('--reader-line'),'2.2');assert.equal(styles.get('--reader-font'),'system-ui');
assert.equal(styles.get('--nav-width'),'300px');assert.equal(styles.get('--context-width'),'280px');
$('context-toggle').onclick();freshPage();
assert.ok(classes.has('context-closed'));assert.ok(!classes.has('focus-reading'));assert.equal($('context-toggle').attributes['aria-expanded'],'false');
""")

    def test_narrow_drawer_preserves_focus_preferences_and_restores_keyboard_focus(self):
        self.js(r"""
storage.set('story-columns',JSON.stringify({left:236,right:248,contextClosed:true}));
storage.set('story-reading',JSON.stringify({size:'22',line:'2.2',font:'sans',focus:true}));
freshPage(760);const saved=new Map(storage),start=writes.length;
$('context-toggle').onclick();
assert.ok(classes.has('context-open'));assert.ok(classes.has('focus-reading'));
assert.equal($('context-toggle').attributes['aria-expanded'],'true');
assert.ok($('main').inert);assert.ok($('header').inert);assert.ok($('book-nav').inert);assert.ok(!$('book-context').inert);
assert.equal(document.activeElement,$('context-close'));assert.deepEqual(storage,saved);assert.equal(writes.length,start);
document.listeners.keydown({key:'Escape'});
assert.ok(!classes.has('context-open'));assert.ok(!$('main').inert);assert.ok(!$('header').inert);assert.ok(!$('book-nav').inert);
assert.equal(document.activeElement,$('context-toggle'));assert.equal(reading().focus,true);assert.equal(columns().contextClosed,true);
$('context-toggle').onclick();media.matches=false;media.listeners.change();
assert.ok(!classes.has('context-open'));assert.ok(!$('main').inert);assert.equal(document.activeElement,$('context-toggle'));
assert.ok(classes.has('focus-reading'));assert.deepEqual(storage,saved);
""")


if __name__ == '__main__':
    unittest.main()

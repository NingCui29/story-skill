"""Execute the client appearance asset against node-preserving DOM fixtures."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / 'desktop/macos/editor-appearance.js'


HARNESS = r"""
const assert=require('assert'),vm=require('vm');
class Element {
 constructor(tag,attributes={},text='') {
  this.tagName=tag.toUpperCase();this.attributes={...attributes};this.children=[];
  this.parentElement=null;this._text=text;this.hidden=false;this.open=false;
  this.dataset={};this.scrollTop=0;this.onclick=null;this.listeners={};
  this.style={setProperty(k,v){this[k]=v;},removeProperty(k){delete this[k];}};
  this.classList={add:(...names)=>{this.className=[...new Set([...this.className.split(/\s+/).filter(Boolean),...names])].join(' ');},
   remove:(...names)=>{this.className=this.className.split(/\s+/).filter(n=>!names.includes(n)).join(' ');},
   contains:name=>this.className.split(/\s+/).includes(name),
   toggle:(name,force)=>{const enabled=force===undefined?!this.classList.contains(name):force;enabled?this.classList.add(name):this.classList.remove(name);return enabled;}};
 }
 get id(){return this.attributes.id||'';} set id(value){this.attributes.id=value;}
 get className(){return this.attributes.class||'';} set className(value){this.attributes.class=value;}
 get parentNode(){return this.parentElement;} get childNodes(){return this.children;}
 get firstChild(){return this.children[0]||null;} get firstElementChild(){return this.firstChild;}
 get nextSibling(){if(!this.parentElement)return null;return this.parentElement.children[this.parentElement.children.indexOf(this)+1]||null;}
 get textContent(){return this._text+this.children.map(n=>n.textContent).join('');}
 set textContent(value){this._text=String(value);for(const n of this.children)n.parentElement=null;this.children=[];}
 set innerHTML(_){throw Error('Appearance must not replace HTML or recreate controls');}
 setAttribute(k,v){this.attributes[k]=String(v);} getAttribute(k){return this.attributes[k]??null;}
 hasAttribute(k){return k in this.attributes;} removeAttribute(k){delete this.attributes[k];}
 appendChild(node){if(node.parentElement)node.parentElement.removeChild(node);this.children.push(node);node.parentElement=this;return node;}
 append(...nodes){nodes.forEach(n=>this.appendChild(n));} prepend(node){this.insertBefore(node,this.firstChild);}
 insertBefore(node,next){if(node.parentElement)node.parentElement.removeChild(node);const index=next?this.children.indexOf(next):-1;this.children.splice(index<0?this.children.length:index,0,node);node.parentElement=this;return node;}
 removeChild(node){const index=this.children.indexOf(node);if(index<0)throw Error('not a child');this.children.splice(index,1);node.parentElement=null;return node;}
 remove(){if(this.parentElement)this.parentElement.removeChild(this);}
 contains(node){return node===this||this.children.some(n=>n.contains(node));}
 addEventListener(k,fn){(this.listeners[k]??=[]).push(fn);}
 focus(){this.ownerDocument.activeElement=this;}
 click(){if(this.onclick)this.onclick({target:this});for(const fn of this.listeners.click||[])fn({target:this});}
 matches(selector){
  selector=selector.trim().replace(/^:scope\s*/, '');
  const tag=selector.match(/^[A-Za-z][\w-]*/);if(tag&&this.tagName!==tag[0].toUpperCase())return false;
  for(const match of selector.matchAll(/#([\w-]+)/g))if(this.id!==match[1])return false;
  for(const match of selector.matchAll(/\.([\w-]+)/g))if(!this.classList.contains(match[1]))return false;
  for(const match of selector.matchAll(/\[([\w-]+)(?:=["']?([^"'\]]+)["']?)?\]/g))if(!this.hasAttribute(match[1])||(match[2]!==undefined&&this.getAttribute(match[1])!==match[2]))return false;
  return !!selector;
 }
 querySelectorAll(selector){
  const result=[];
  const matchChain=(node,parts)=>{if(!node.matches(parts[parts.length-1]))return false;if(parts.length===1)return true;
   for(let parent=node.parentElement;parent;parent=parent.parentElement)if(matchChain(parent,parts.slice(0,-1)))return true;return false;};
  const alternatives=selector.split(',').map(s=>s.trim().split(/\s*>\s*|\s+/).filter(s=>s&&s!==':scope'));
  const visit=node=>{for(const child of node.children){if(alternatives.some(parts=>matchChain(child,parts)))result.push(child);visit(child);}};
  visit(this);return result;
 }
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
 closest(selector){for(let node=this;node;node=node.parentElement)if(node.matches(selector))return node;return null;}
}
function page({address='http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/',iframe=false,known=true,modern=false}={}){
 const html=new Element('html'),head=new Element('head'),body=new Element('body');html.append(head,body);
 head.append(new Element('style',{id:'original-style'},'body{color:black}'));
 const header=new Element('header'),heading=new Element('div',{class:'book-heading'}),actions=new Element('div',{class:'header-actions'});
 const title=new Element('strong',{id:'book-title'},'这是很长的合成验收作品名称 · 布局核验');
 const identity=new Element('div',{class:'book-identity'}),kind=new Element('span',{id:'book-kind',class:'book-kind'},'长篇');
 const locationPanel=new Element('details',{id:'current-book-location'}),locationSummary=new Element('summary',{},'作品位置');
 const root=new Element('p',{id:'current-book-root'},'/合成目录/很长的本地书目录/明确仅为布局验收');locationPanel.append(locationSummary,root);
 const progress=new Element('p',{id:'book-progress'},'正式3章 · 下一章4'),classification=new Element('div',{id:'book-classification',class:'book-classification'});
 classification.append(new Element('span',{class:'genre-tag'},'都市'),new Element('span',{class:'genre-tag'},'现代言情'),new Element('span',{class:'genre-tag'},'职场'));
 if(modern){identity.append(title,kind);heading.append(identity,progress);}else{identity.append(kind,locationPanel);heading.append(new Element('span',{class:'eyebrow'},'写作工作台'),title,identity,progress,classification);}
 const nav=new Element('button',{id:'nav-toggle'},'作品目录'),context=new Element('button',{id:'context-toggle'},'辅助信息'),refresh=new Element('button',{id:'refresh'},'刷新');
 const maintenance=new Element('details',{id:'maintenance',class:'menu'}),maintenanceSummary=new Element('summary',{},'设置'),menu=new Element('div',{class:'menu-panel'});
 const compact=new Element('button',{id:'refresh-compact'},'刷新目录'),reset=new Element('button',{id:'layout-reset'},'恢复默认布局');
 const rootLocation=new Element('details',{id:'root-location'});rootLocation.append(new Element('summary',{},'书库与材料位置'),new Element('span',{id:'root-hint'},'合成材料长路径'));
 menu.append(compact,reset,new Element('button',{id:'reload-source'},'重新读取文件'),new Element('button',{id:'recover-reload'},'保留恢复稿并重新载入'),rootLocation);maintenance.append(maintenanceSummary,menu);
 actions.append(nav,context,refresh);
 if(modern){const info=new Element('details',{id:'book-info',class:'menu'}),panel=new Element('div',{id:'book-info-panel',class:'menu-panel book-info-panel'});panel.append(new Element('strong',{id:'book-info-title'},title.textContent),classification,locationPanel);info.append(new Element('summary',{},'作品信息'),panel);actions.append(info);}
 actions.append(maintenance);header.append(heading,actions);
 const desk=new Element('div',{class:'desk'}),bookNav=new Element('nav',{id:'book-nav'}),main=new Element('main'),bookContext=new Element('aside',{id:'book-context'});
 const tools=new Element('div',{class:'tools'}),save=new Element('button',{id:'save'},'保存候选稿'),text=new Element('textarea',{id:'text'}),prose=new Element('pre',{id:'prose'},'合成验收正文');
 tools.append(save,new Element('details',{id:'more-actions',class:'menu'}),new Element('details',{id:'reading-options',class:'menu'}));main.append(tools,text,prose);desk.append(bookNav,main,bookContext);body.append(header,desk);
 if(!known){heading.className='unknown-heading';nav.remove();context.remove();maintenance.remove();}
 const document={head,body,documentElement:html,readyState:'complete',activeElement:text,listeners:{},
  createElement(tag){const node=new Element(tag);node.ownerDocument=this;return node;},getElementById:id=>html.querySelector('#'+id),
  querySelector:selector=>html.querySelector(selector),querySelectorAll:selector=>html.querySelectorAll(selector),
  addEventListener(k,fn){(this.listeners[k]??=[]).push(fn);}};
 for(const node of [html,...html.querySelectorAll('*')])node.ownerDocument=document;
 const location=new URL(address),window={location};window.top=iframe?{}:window;window.self=window;
 Object.defineProperty(text,'value',{get(){throw Error('Protected textarea value was read');},set(){throw Error('Protected textarea value was written');}});
 const observers=[];
 class ControlledMutationObserver {
  constructor(callback){this.callback=callback;this.observations=[];this.disconnected=false;observers.push(this);}
  observe(target,options){this.observations.push({target,options:{...options}});}
  disconnect(){this.disconnected=true;}
  fire(target){if(!this.disconnected&&this.observations.some(item=>item.target===target))this.callback([{target,type:'childList'}],this);}
 }
 const contextVM={window,document,location,URL,console,MutationObserver:ControlledMutationObserver};
 for(const field of ['docs','active','recoveryKey','recovering'])Object.defineProperty(contextVM,field,{get(){throw Error('Protected editor state read: '+field);},set(){throw Error('Protected editor state written: '+field);}});
 const run=()=>vm.runInNewContext(asset,contextVM,{timeout:1000});
 const serialize=node=>({tag:node.tagName,attributes:{...node.attributes},text:node._text,children:node.children.map(serialize)});
 return {run,document,head,body,header,heading,actions,title,root,identity,classification,kind,progress,maintenance,locationPanel,nav,context,refresh,compact,reset,save,text,prose,main,desk,observers,serialize,tree:()=>JSON.stringify(serialize(html)),styles:()=>head.querySelectorAll('style').filter(n=>n.id!=='original-style')};
}
"""


class DesktopEditorAppearanceTests(unittest.TestCase):
    def js(self, test):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        self.assertTrue(ASSET.is_file(), 'Client appearance asset must exist')
        source = 'const asset=' + json.dumps(ASSET.read_text(encoding='utf-8')) + ';\n'
        result = subprocess.run([node, '-e', source + HARNESS + '\n' + test],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_legacy_editor_gets_one_style_and_header_adaptation(self):
        self.js("const p=page(),before=p.tree(),result=p.run();assert.equal(result.applied,true);assert.equal(result.legacy,true);assert.equal(p.styles().length,1);assert.equal(p.styles()[0].id,'story-editor-appearance');assert.ok(p.styles()[0].textContent.trim());assert.ok(p.body.classList.contains('native-compact-editor'));assert.notEqual(p.tree(),before);")

    def test_existing_controls_keep_node_identity_ids_and_handlers(self):
        self.js(r"""
const p=page(),nodes=[p.nav,p.context,p.refresh,p.compact,p.reset,p.save];let count=0;
for(const n of nodes){n.onclick=()=>count++;n.addEventListener('click',()=>count++);}
p.run();for(const n of nodes){assert.strictEqual(p.document.getElementById(n.id),n);n.click();}assert.equal(count,nodes.length*2);
""")

    def test_ids_remain_unique_and_all_original_named_nodes_survive(self):
        self.js(r"""
const p=page(),original=p.document.querySelectorAll('[id]');p.run();
for(const n of original)assert.strictEqual(p.document.getElementById(n.id),n);
const ids=p.document.querySelectorAll('[id]').map(n=>n.id);assert.equal(new Set(ids).size,ids.length);
""")

    def test_repeated_execution_is_idempotent(self):
        self.js("const p=page();p.run();const first=p.tree(),style=p.styles()[0];p.run();p.run();assert.equal(p.tree(),first);assert.strictEqual(p.styles()[0],style);assert.equal(p.styles().length,1);")

    def test_modern_editor_retains_all_existing_controls(self):
        self.js("const p=page({modern:true}),nodes=p.document.querySelectorAll('[id]'),before=p.tree(),result=p.run();assert.equal(result.applied,true);assert.equal(result.legacy,false);for(const n of nodes)assert.strictEqual(p.document.getElementById(n.id),n);assert.equal(p.tree(),before);p.run();assert.equal(p.tree(),before);")

    def test_external_invalid_and_non_editor_addresses_are_unchanged(self):
        self.js(r"""
for(const address of ['https://example.com/abcdefghijklmnopqrstuvwxyz012345/','http://localhost:12345/abcdefghijklmnopqrstuvwxyz012345/','file:///tmp/editor.html','http://127.0.0.1:12345/','http://127.0.0.1:12345/short/','http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/extra','http://user:pass@127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/','http://127.0.0.1/abcdefghijklmnopqrstuvwxyz012345/','http://127.0.0.1:12345/abcdefghijklmnopqrstuvwxyz012345/?unsafe=yes','http://127.0.0.1:12345/%61bcdefghijklmnopqrstuvwxyz012345/','http://[::1]:12345/abcdefghijklmnopqrstuvwxyz012345/']){
 const p=page({address}),before=p.tree();p.run();assert.equal(p.tree(),before,address);
}
""")

    def test_iframe_is_unchanged(self):
        self.js("const p=page({iframe:true}),before=p.tree();p.run();assert.equal(p.tree(),before);")

    def test_unknown_editor_structure_is_unchanged(self):
        self.js("const p=page({known:false}),before=p.tree();p.run();assert.equal(p.tree(),before);")

    def test_protected_state_and_everything_outside_header_remain_untouched(self):
        self.js(r"""
const p=page(),before=JSON.stringify(p.serialize(p.desk)),focus=p.document.activeElement,children=[...p.body.children];
p.main.scrollTop=456;p.text.scrollTop=123;p.text.selectionStart=17;p.text.selectionEnd=29;
p.run();assert.equal(JSON.stringify(p.serialize(p.desk)),before);assert.deepEqual(p.body.children,children);
assert.strictEqual(p.document.activeElement,focus);assert.equal(p.main.scrollTop,456);assert.equal(p.text.scrollTop,123);assert.equal(p.text.selectionStart,17);assert.equal(p.text.selectionEnd,29);
""")

    def test_long_title_location_tags_and_open_state_are_preserved(self):
        self.js(r"""
const p=page(),title=p.title.textContent,root=p.root.textContent,tags=p.classification.children.slice();
p.locationPanel.open=true;p.maintenance.open=true;p.run();
assert.equal(p.title.textContent,title);assert.equal(p.root.textContent,root);assert.deepEqual(p.classification.children,tags);
assert.ok(p.locationPanel.open);assert.ok(p.maintenance.open);
""")

    def test_opening_information_refreshes_current_title_and_progress(self):
        self.js(r"""
const p=page();p.run();const info=p.document.getElementById('book-info');
p.title.textContent='后续更新的合成标题';p.progress.textContent='后续更新的正式章进度';info.open=true;
for(const fn of info.listeners.toggle)fn({target:info});
assert.equal(p.document.getElementById('book-info-title').textContent,p.title.textContent);
assert.equal(p.document.getElementById('book-progress-detail').textContent,p.progress.textContent);
""")

    def test_observer_monitors_only_original_title_and_progress_labels(self):
        self.js(r"""
const p=page();p.run();assert.equal(p.observers.length,1);const labels=p.observers[0].observations;
assert.deepEqual(labels.map(item=>item.target),[p.title,p.progress]);
for(const item of labels)assert.deepEqual(item.options,{childList:true,characterData:true,subtree:true});
assert.ok(!labels.some(item=>[p.text,p.main,p.desk,p.body,p.header].includes(item.target)));
""")

    def test_observer_updates_open_information_without_reading_editor_state(self):
        self.js(r"""
const p=page();p.run();const info=p.document.getElementById('book-info');info.open=true;
const title=p.document.getElementById('book-info-title'),progress=p.document.getElementById('book-progress-detail');
p.title.textContent='刷新后的合成标题';p.observers[0].fire(p.title);assert.equal(title.textContent,p.title.textContent);
p.progress.textContent='刷新后的正式章进度';p.observers[0].fire(p.progress);assert.equal(progress.textContent,p.progress.textContent);
assert.ok(info.open);assert.strictEqual(p.document.activeElement,p.text);
""")

    def test_repeated_execution_does_not_register_duplicate_observers(self):
        self.js(r"""
const p=page();p.run();const observer=p.observers[0];p.run();p.run();
assert.equal(p.observers.length,1);assert.strictEqual(p.observers[0],observer);assert.equal(observer.observations.length,2);
p.progress.textContent='幂等执行之后的合成进度';observer.fire(p.progress);
assert.equal(p.document.getElementById('book-progress-detail').textContent,p.progress.textContent);
""")

    def test_information_closes_on_outside_pointer_and_escape_restores_focus(self):
        self.js(r"""
const p=page();p.run();const info=p.document.getElementById('book-info'),summary=info.querySelector('summary');
info.open=true;for(const fn of p.document.listeners.pointerdown)fn({target:p.root});assert.ok(info.open);
for(const fn of p.document.listeners.pointerdown)fn({target:p.main});assert.ok(!info.open);
info.open=true;for(const fn of p.document.listeners.keydown)fn({key:'Escape'});assert.ok(!info.open);assert.strictEqual(p.document.activeElement,summary);
""")


if __name__ == '__main__':
    unittest.main()

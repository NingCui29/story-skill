"""Shelf dropdowns share dismissal without replacing the original controls."""
import shutil
import subprocess
import unittest

import test_workbench as base


class WorkbenchShelfMenuTests(unittest.TestCase):
    def js(self, checks, with_open_manager=False):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        source = base.story.workbench._library_management_script()
        controls = source[source.index('function shelfMenus()'):
                          source.index('function managementText(')]
        manager_entry = (source[source.index('function openManager('):
                                source.index("manager.addEventListener('cancel'")]
                         if with_open_manager else '')
        fixture = r"""
const assert=require('assert');
class E {
 constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.parentElement=null;this.open=false;this.focused=0;this.value='';}
 append(...children){for(const child of children){child.parentElement=this;this.children.push(child);}}
 replaceChildren(...children){this.children=[];this.append(...children);}
 contains(target){return this===target||this.children.some(child=>child.contains(target));}
 focus(){this.focused++;document.activeElement=this;}
}
const listeners=new Map(),manager={open:false};
let directoryPicking=false;
const document={activeElement:null,querySelectorAll:selector=>{
 assert.equal(selector,'.library-actions>details,.book-management');return menus;
},addEventListener:(type,listener,capture)=>{
 assert.equal(capture,true,'toggle does not bubble; dismissal must also run before navigation');
 if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(listener);
}};
function menu(){const root=new E('details'),summary=new E('summary'),panel=new E();root.append(summary,panel);return {root,summary,panel};}
const add=menu(),tools=menu(),first=menu(),second=menu(),menus=[add.root,tools.root,first.root,second.root],outside=new E();
const input=new E('input');input.value='/原始作品目录';let added=0;input.oninput=()=>added++;add.panel.append(input);
const nested=new E('details'),nestedSummary=new E('summary'),select=new E('select');nested.append(nestedSummary,select);first.panel.append(nested);let saved=0;select.onchange=()=>saved++;
const help=new E('details'),helpSummary=new E('summary');help.append(helpSummary);tools.panel.append(help);
function emit(type,target,extra={}){const event={target,key:'',defaultPrevented:false,preventDefault(){this.defaultPrevented=true;},...extra};for(const listener of listeners.get(type)||[])listener(event);return event;}
function click(target){emit('pointerdown',target);emit('click',target);const targetMenu=menus.find(root=>root.children[0].contains(target));if(targetMenu){targetMenu.open=!targetMenu.open;emit('toggle',targetMenu);}}
const open=()=>menus.filter(root=>root.open);
"""
        result = subprocess.run(
            [node, '-e', fixture + controls + manager_entry + checks],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_topbar_and_new_card_menus_are_mutually_exclusive(self):
        self.js(r"""
for(const current of [add,tools,first,second,add]){
 click(current.summary);assert.deepEqual(open(),[current.root]);
}
click(add.summary);assert.deepEqual(open(),[],'same summary still toggles normally');
first.root.open=true;emit('toggle',first.root);second.root.open=true;emit('toggle',second.root);
assert.deepEqual(open(),[second.root],'programmatic details opening also dismisses the previous dropdown');
const later=menu();menus.push(later.root);click(later.summary);assert.deepEqual(open(),[later.root],'re-rendered cards need no rebinding');
""")

    def test_outside_pointer_and_keyboard_click_dismiss_without_moving_focus(self):
        self.js(r"""
click(add.summary);input.focus();emit('pointerdown',outside);assert.deepEqual(open(),[]);assert.strictEqual(document.activeElement,input);assert.equal(add.summary.focused,0);
click(tools.summary);emit('click',outside);assert.deepEqual(open(),[],'keyboard or assistive activation has no pointerdown');assert.equal(tools.summary.focused,0);
""")

    def test_input_and_nested_evidence_status_help_keep_original_handlers(self):
        self.js(r"""
const originalInput=input,originalHandler=input.oninput;
click(add.summary);click(input);input.oninput();assert.deepEqual(open(),[add.root]);assert.strictEqual(add.panel.children[0],originalInput);assert.strictEqual(input.oninput,originalHandler);assert.equal(input.value,'/原始作品目录');assert.equal(added,1);
click(first.summary);click(nestedSummary);nested.open=true;emit('toggle',nested);click(select);select.onchange();assert.deepEqual(open(),[first.root]);assert.equal(saved,1);assert.strictEqual(nested.children[1],select);
click(tools.summary);click(helpSummary);help.open=true;emit('toggle',help);assert.deepEqual(open(),[tools.root]);
""")

    def test_escape_closes_menu_and_returns_focus_to_its_own_summary(self):
        self.js(r"""
click(first.summary);select.focus();assert.equal(emit('keydown',select,{key:'Enter'}).defaultPrevented,false);assert.deepEqual(open(),[first.root]);
const escape=emit('keydown',select,{key:'Escape'});assert.equal(escape.defaultPrevented,true);assert.deepEqual(open(),[]);assert.strictEqual(document.activeElement,first.summary);assert.equal(first.summary.focused,1);
assert.equal(emit('keydown',outside,{key:'Escape'}).defaultPrevented,false,'an unused Escape remains available to the host');
""")

    def test_modal_transition_dismisses_without_stealing_modal_focus(self):
        self.js(r"""
const nodes={},$=id=>nodes[id]||(nodes[id]=new E());let managerBusy=false,managerGeneration=0;
const managerBody=new E(),managerActions=new E(),modalInput=new E('input');
function managementClearTimer(){}function managementButton(){}
manager.setAttribute=()=>{};manager.showModal=()=>{manager.open=true;modalInput.focus();};
click(tools.summary);openManager('恢复备份');
assert.deepEqual(open(),[]);assert.equal(tools.summary.focused,0);assert.strictEqual(document.activeElement,modalInput);
assert.equal(emit('keydown',modalInput,{key:'Escape'}).defaultPrevented,false,'modal cancel stays under the dialog guard');
assert.strictEqual(document.activeElement,modalInput);
""", with_open_manager=True)


if __name__ == '__main__':
    unittest.main()

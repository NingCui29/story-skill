"""Manuscript labels must preserve authority, recovery and unsaved boundaries."""
import shutil
import subprocess
import unittest

import test_workbench as base


class DocumentIdentityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w = base.story.workbench
        self.page = self.w._editor_page(self.w.snapshot(self.fixture.book), {}, 'test').decode()
        self.script = self.page.split('<script>', 1)[1].split('</script>', 1)[0]
        self.helpers = self.script[self.script.index('function displayTitle'):self.script.index('function downloadName')]

    def js(self, code, extra=''):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required for manuscript identity checks')
        program = "const assert=require('assert');\n" + self.helpers + '\n' + extra + '\n' + code
        result = subprocess.run([node, '-e', program], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_edited_formal_text_is_explicitly_unsaved_and_keeps_source(self):
        self.js("""
const d={id:'formal:1',kind:'formal',editable:true,text:'正式',value:'正式'};
assert.equal(documentIdentity(d).label,'正式正文');
d.value='正在修改';const identity=documentIdentity(d);
assert.equal(identity.label,'来源：正式正文');assert.ok(identity.changed);
assert.ok(identity.note.includes('尚未保存'));assert.equal(d.text,'正式');
assert.equal(documentIdentity({kind:'material',path:'02_正文/已采用.md'}).label,'创作材料');
""")

    def test_pending_and_broken_sources_override_candidate_and_recovery_labels(self):
        self.js("""
const d={id:'pending:.story/drafts/workbench/自动恢复/记录.json',kind:'candidate',category:'自动恢复'};
assert.equal(documentIdentity(d).label,'待核对恢复记录');
assert.equal(documentIdentity({...d,id:'file:draft',needs_recovery:true}).kind,'pending');
assert.equal(documentIdentity({...d,id:'file:draft',metadata_error:{code:'bad'}}).label,'来源待核对 · 只读');
assert.equal(documentIdentity({kind:'candidate',external:true}).label,'关联材料 · 只读');
""")

    def test_material_candidates_and_recoveries_stay_separate_from_prose(self):
        self.js("""
assert.equal(documentIdentity({kind:'candidate',content_kind:'material'}).label,'材料候选稿');
assert.equal(documentIdentity({kind:'candidate',content_kind:'material',category:'自动恢复'}).label,'材料恢复稿');
assert.equal(documentIdentity({kind:'candidate',category:'自动恢复'}).label,'自动恢复稿');
const same={kind:'candidate',status:'内容与当前正式稿逐字一致'};
assert.equal(documentIdentity(same).label,'候选稿');assert.ok(documentIdentity(same).note.includes('另核对'));
assert.equal(documentIdentity({kind:'plan'}).label,'章计划');
""")

    def test_badge_updates_identity_and_unsaved_text_without_changing_document(self):
        badge = self.script[self.script.index('function badge'):self.script.index('function show(d)')]
        self.js("""
const d={id:'formal:1',kind:'formal',editable:true,text:'原文',value:'修改',status:'正式稿'},before=JSON.stringify(d);
docs.set(active,d);badge();assert.equal($('document-role').textContent,'来源：正式正文');
assert.equal($('document-unsaved').hidden,false);assert.equal(JSON.stringify(d),before);
d.value=d.text;badge();assert.equal($('document-role').textContent,'正式正文');assert.equal($('document-unsaved').hidden,true);
d.needs_recovery=true;badge();assert.equal($('document-role').textContent,'待核对恢复记录');assert.equal($('save').textContent,'恢复为新候选');
""", """
let active='formal:1';const docs=new Map(),elements={};
const $=id=>elements[id]||(elements[id]={dataset:{},setAttribute(){}});
const pendingEdits=()=>{},dirty=d=>d.editable&&d.value!==d.text;
const document={querySelectorAll:()=>[]};
""" + badge)

    def test_same_name_candidates_have_distinct_visible_locations(self):
        start = self.script.index('function item')
        item = self.script[start:self.script.index('async function catalog', start)]
        self.js("""
const rows=['a','b'].map(x=>({id:'file:'+x,kind:'candidate',category:'候选与草稿',title:'第1章_候选_'+x.repeat(12),path:'.story/drafts/workbench/第1章_候选_'+x.repeat(12)+'.md'}));
const buttons=rows.map(item);assert.equal(buttons[0].children[0].textContent,buttons[1].children[0].textContent);
assert.notEqual(buttons[0].children.at(-1).textContent,buttons[1].children.at(-1).textContent);
assert.ok(buttons.every(b=>b.children[1].textContent==='候选稿'));
buttons[1].onclick();assert.equal(opened,rows[1].id);
const pending=item({...rows[0],id:'pending:record',category:'自动恢复'});assert.equal(pending.children[1].textContent,'待核对恢复记录');
""", """
let opened;const openDoc=id=>{opened=id;};
const document={createElement:()=>({dataset:{},children:[],append(x){this.children.push(x);}})};
""" + item)

    def test_same_name_unsaved_files_remain_distinguishable(self):
        pending = self.script[self.script.index('function pendingEdits'):self.script.index('function item')]
        self.js("""
for(const name of ['甲','乙'])docs.set(name,{id:name,title:'第1章',path:'草稿/'+name+'/第1章.md',editable:true,text:'旧',value:'新'});
pendingEdits();const buttons=$('pending-list').children;assert.equal(buttons.length,2);
assert.notEqual(buttons[0].children[0].textContent,buttons[1].children[0].textContent);
buttons[1].onclick();assert.equal(opened,'乙');
""", """
let opened;const docs=new Map(),elements={},openDoc=id=>{opened=id;};
const $=id=>elements[id]||(elements[id]={children:[],append(x){this.children.push(x);},replaceChildren(){this.children=[];}});
const dirty=d=>d.editable&&d.value!==d.text;
const document={createElement:()=>({children:[],append(x){this.children.push(x);}})};
""" + pending)

    def test_picker_does_not_call_pending_or_broken_source_a_completed_recovery(self):
        choices = self.script[self.script.index('function chapterChoices'):self.script.index('function updateChapterPicker')]
        self.js("""
const d={id:'pending:record',kind:'candidate',category:'自动恢复',chapter:1,title:'恢复记录',path:'自动恢复/记录.json'};
assert.ok(chapterChoices({},d)[0].label.startsWith('待核对恢复记录'));
const bad={...d,id:'file:bad',metadata_error:{code:'bad'}};
const row=chapterChoices({},bad)[0];assert.ok(row.label.startsWith('来源待核对 · 只读'));assert.equal(row.manuscript,false);
""", 'const chapterNumber=d=>d.chapter;\n' + choices)

    def test_actual_equal_candidate_does_not_assert_adoption(self):
        formal = self.w._editor_document(self.fixture.root, 'formal:1')
        path = self.fixture.root / '.story/drafts/第1章.md'
        path.write_bytes((formal['prefix'] + formal['text']).encode())
        candidate = self.w._editor_document(self.fixture.root, 'file:.story/drafts/第1章.md')
        self.assertEqual(candidate['kind'], 'candidate')
        self.assertIn('逐字一致', candidate['status'])
        self.assertIn('不据此推断采用历史', candidate['status'])
        self.assertIn('id="document-identity"', self.page)
        self.assertIn('当前文字未保存', self.page)


if __name__ == '__main__':
    unittest.main()

"""Same-chapter navigation exposes authority, chronology and actual sources."""
import json
import shutil
import subprocess
import unittest

import test_workbench as base


class WorkbenchVersionNavigationTests(unittest.TestCase):
    def setUp(self):
        fixture = base.WorkbenchTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        page = base.story.workbench._editor_page(base.story.workbench.snapshot(fixture.book), {}, 'versions').decode()
        self.page = page
        script = page.split('<script>', 1)[1].split('</script>', 1)[0]
        self.source = script[script.index('function chapterNumber('):script.index('function primaryChapterDocument(')]

    def js(self, case):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        mock = r"""
const assert=require('assert'),docs=new Map();let active='file:new';
class E{constructor(tag){this.tag=tag;this.children=[];}append(...children){this.children.push(...children);}replaceChildren(){this.children=[];}}
const nodes={},$=id=>nodes[id]||(nodes[id]=new E(id));
const document={createElement:tag=>new E(tag)},dirty=d=>d?.editable&&d.value!==d.text;
const summary={textContent:'同章版本'};$('chapter-picker').parentElement={querySelector:()=>summary};
const formal={id:'formal:1',kind:'formal',chapter:1,title:'正式正文',path:'chapters/第1章 风.md'};
const old={id:'file:old',kind:'candidate',category:'候选与草稿',chapter:1,title:'风_候选_abcdef123456',path:'.story/drafts/workbench/风_候选_abcdef123456.md',modified:'2026-10-01T00:00:00Z',source:'formal:1'};
const current={...old,id:'file:new',path:'.story/drafts/workbench/风_候选_112233445566.md',title:'风_候选_112233445566',modified:'2026-10-20T00:00:00Z',editable:true,text:'磁盘正文',value:'当前未保存修改',source:'file:'+old.path};
const recovery={...old,id:'file:recovery',category:'自动恢复',path:'.story/drafts/workbench/自动恢复/风_12345678-1234-1234-1234-123456789abc.txt',source:current.id};
const pending={...recovery,id:'pending:recovery',needs_recovery:true};
const plan={id:'plan:1',kind:'plan',chapter:1,title:'第1章 计划',path:'章计划/第1章.md',content_kind:'material'};
const material={id:'file:outline',kind:'material',chapter:1,title:'第1章 细纲',path:'01_大纲细纲/第1章 细纲.md'};
const currentCatalog={workspace:{groups:[{chapter:1,title:'第1章 风',items:[formal,plan,old,recovery,pending,material]}]}};
docs.set(current.id,current);
"""
        result = subprocess.run([node, '-'], input=mock + self.source + '\n' + case, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_versions_are_grouped_sorted_and_current_unsaved_document_is_explicit(self):
        self.js(r"""
const before=JSON.stringify([...docs]);updateChapterPicker();const groups=$('chapter-version').children;
assert.deepEqual(groups.map(group=>group.label),['正式稿','修改稿','恢复稿','计划与材料']);
assert.deepEqual(groups[1].children.map(option=>option.value),[current.id,old.id]);
const selected=groups.flatMap(group=>group.children).filter(option=>option.selected);
assert.equal(selected.length,1);assert.equal(selected[0].value,current.id);assert.ok(selected[0].textContent.startsWith('当前 · '));
assert.ok(selected[0].textContent.includes('未保存'));assert.equal(summary.textContent,'同章版本 · 7 份');
assert.equal(JSON.stringify([...docs]),before);
""")

    def test_actual_source_and_time_are_shown_without_inferred_adoption(self):
        self.js(r"""
const rows=chapterChoices(currentCatalog,current),saved=rows.find(row=>row.id===old.id);
assert.equal(saved.versionSource,'第1章正式稿');assert.ok(saved.label.includes('来源：第1章正式稿'));assert.ok(saved.versionTime.includes('2026'));
const copy=rows.find(row=>row.id===current.id);assert.equal(copy.versionSource,old.path);assert.ok(copy.label.includes(old.path));
assert.ok(!copy.label.includes('已采用'));assert.ok(!saved.label.includes('已采用'));
const unrecorded=chapterChoices({}, {...old,id:'file:unknown',source:undefined,modified:'not a time'}).find(row=>row.id==='file:unknown');
assert.equal(unrecorded.versionSource,'');assert.equal(unrecorded.versionTime,'');assert.ok(!unrecorded.label.includes('Invalid Date'));assert.ok(!unrecorded.label.includes('来源：'));
""")

    def test_open_buffers_outside_catalog_stay_in_their_chapter_and_keep_identity(self):
        self.js(r"""
const hidden={...current,id:'file:hidden',title:'同章隐藏修改稿'},other={...current,id:'file:other',chapter:2};docs.set(hidden.id,hidden);docs.set(other.id,other);
const before=JSON.stringify([...docs]),rows=chapterChoices({},current);
assert.ok(rows.some(row=>row.id===hidden.id));assert.ok(!rows.some(row=>row.id===other.id));
assert.equal(rows.find(row=>row.id===current.id).value,current.value);assert.equal(JSON.stringify([...docs]),before);
""")

    def test_recovery_pending_and_broken_metadata_never_look_like_formal_adoption(self):
        self.js(r"""
const broken={...old,id:'file:broken',metadata_error:{code:'missing'}};
const rows=chapterChoices({files:[broken],workspace:{groups:[{chapter:1,items:[formal,pending,recovery]}]}},current);
assert.equal(rows.find(row=>row.id===pending.id).versionGroup,'恢复稿');assert.ok(rows.find(row=>row.id===pending.id).label.startsWith('待核对恢复记录'));
assert.equal(rows.find(row=>row.id===broken.id).versionGroup,'计划与材料');assert.equal(rows.find(row=>row.id===broken.id).manuscript,false);
assert.equal(rows.find(row=>row.id===recovery.id).versionGroup,'恢复稿');assert.equal(rows.find(row=>row.id===formal.id).versionGroup,'正式稿');
""")

    def test_explicit_retained_versions_have_their_own_group(self):
        self.js(r"""
const checkpoint={...old,id:'file:checkpoint',checkpoint:true};currentCatalog.workspace.groups[0].items.push(checkpoint);updateChapterPicker();
const groups=$('chapter-version').children;assert.deepEqual(groups.map(group=>group.label),['正式稿','修改稿','保留版本','恢复稿','计划与材料']);
const retained=groups.find(group=>group.label==='保留版本');assert.equal(retained.children.length,1);
assert.equal(retained.children[0].value,checkpoint.id);assert.ok(retained.children[0].textContent.startsWith('保留版本'));
""")


if __name__ == '__main__':
    unittest.main()

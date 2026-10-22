"""Current work genre display follows catalog data without changing edit buffers."""
import json
import shutil
import subprocess
import unittest

import test_workbench as base


class ClassificationEditorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w = base.story.workbench
        self.page = self.w._editor_page(self.w.snapshot(self.fixture.book), {}, 'test').decode()
        script = self.page.split('<script>', 1)[1].split('</script>', 1)[0]
        self.source = self.w._book_display_script() + script[script.index('let bookChoices='):script.index('function chapterNumber')]

    def js(self, test):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required for classification display checks')
        mock = r"""
const assert=require('assert');
class E{constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.textContent='';this.value='';}
append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}
set innerHTML(v){throw Error('Classification must use text nodes');}}
const nodes={},$=id=>nodes[id]||(nodes[id]=new E());
const document={createElement:tag=>new E(tag),title:''};
$('book-title').textContent='当前作品';$('book-kind').dataset.kind='long';$('current-book-root').textContent='/书库/作品';
const text=e=>(e.textContent||'')+e.children.map(text).join(' ');
const docs=new Map([['formal:1',{text:'原文',value:'未保存正文'}]]);
"""
        result = subprocess.run([node, '-'], input=mock + self.source + '\n' + test, encoding='utf-8', capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_current_genres_have_explicit_sources_and_refresh_without_mutating_text(self):
        self.js(r"""
const before=JSON.stringify([...docs]);
showBookIdentity({title:'当前作品',kind:'long',classification:{status:'recorded',tags:['都市','悬疑'],sources:[{path:'创作约定.md',line:8,field:'题材',value:'都市＋悬疑'}]}},'/书库/作品');
assert.equal($('book-classification').hidden,false);const shown=text($('book-classification'));
for(const value of ['题材','都市','悬疑','题材依据','创作约定.md','第8行'])assert.ok(shown.includes(value),shown);
assert.equal(JSON.stringify([...docs]),before);
showBookIdentity({title:'当前作品',classification:{status:'missing',tags:[],sources:[]}},'/书库/作品');
assert.equal($('book-classification').children[0].dataset.classificationEmpty,'true');assert.equal($('book-classification').children[0].children[1].hidden,true);assert.ok(!text($('book-classification')).includes('悬疑'));
assert.equal(JSON.stringify([...docs]),before);
""")

    def test_analysis_tags_describe_the_analysis_subject_and_unsafe_text_is_plain(self):
        self.js(r"""
showBookIdentity({kind:'analysis',classification:{status:'recorded',tags:['<img onerror=bad>'],sources:[{path:'<script>.md',field:'题材',value:'<b>都市</b>'}]}},'/书库/作品');
const shown=text($('book-classification'));
assert.ok(shown.includes('分析对象题材'));assert.ok(shown.includes('<img onerror=bad>'));assert.ok(shown.includes('<b>都市</b>'));
assert.equal($('book-kind').textContent,'作品分析');
""")

    def test_unverified_classification_does_not_display_tags_as_confirmed(self):
        self.js(r"""
showBookIdentity({classification:{status:'needs_review',tags:['都市'],sources:[{path:'创作约定.md',field:'题材',value:'都市'}],note:'分类记录有冲突'}},'/书库/作品');
const row=$('book-classification').children[0];
assert.ok(text(row).includes('题材待核对'));assert.ok(text(row).includes('分类记录有冲突'));
assert.ok(row.children.find(e=>e.className==='genre-tags').children.every(e=>e.textContent!=='都市'));
""")

    def test_editor_contains_current_classification_area(self):
        self.assertIn('id="book-classification" class="book-classification" hidden', self.page)
        self.assertIn('function classificationNode(book)', self.page)
        self.assertIn('.book-classification .genre-tag', self.page)

    def test_refresh_distinguishes_conflicts_and_reading_errors_while_retaining_pending_text(self):
        self.js(r"""
const before=JSON.stringify([...docs]);
for(const [reason,label]of [['conflict','题材记录冲突'],['read_error','题材读取失败'],['limit','题材读取受限']]){
 showBookIdentity({classification:{status:'needs_review',reason,tags:['不应确认为当前题材'],
  sources:[{path:'创作约定.md',line:9,field:'作品类型',value:'都市'}],
  details:[{reason,path:'00_项目策划/项目总览.md',line:12,field:'题材',note:'读取或核对说明'}],
  checked_paths:['创作约定.md','00_项目策划/项目总览.md']}},'/书库/作品');
 const box=$('book-classification'),row=box.children[0],shown=text(box);
 assert.equal(box.hidden,false);assert.equal(row.dataset.classificationReason,reason);
 assert.equal(row.children[1].children[0].textContent,label);
 for(const value of ['创作约定.md','第9行','作品类型：都市','00_项目策划/项目总览.md','第12行','原字段：题材','读取或核对说明'])assert.ok(shown.includes(value),shown);
 assert.ok(!shown.includes('不应确认为当前题材'));assert.equal(JSON.stringify([...docs]),before);
}
showBookIdentity({classification:{status:'recorded',reason:'recorded',tags:['玄幻'],sources:[{path:'创作约定.md',line:9,field:'题材',value:'玄幻'}]}},'/书库/作品');
assert.ok(text($('book-classification')).includes('玄幻'));assert.ok(!text($('book-classification')).includes('题材记录冲突'));
assert.equal(JSON.stringify([...docs]),before);
""")

    def test_missing_and_unconfirmed_records_are_reading_states_without_manual_classification_prompt(self):
        self.js(r"""
for(const reason of ['no_sources','no_declaration','unconfirmed','unsupported_format']){
 showBookIdentity({classification:{status:'missing',reason,tags:[],sources:[],checked_paths:['创作约定.md']}},'/书库/作品');
 const row=$('book-classification').children[0],shown=text(row);assert.equal(row.dataset.classificationEmpty,'true');assert.equal(row.children[1].hidden,true);assert.equal(row.children[1].children.length,0);assert.ok(shown.includes('已检查材料：'));
 assert.ok(!shown.includes('待分类'));assert.ok(!shown.includes('请选择'));assert.ok(!shown.includes('手工'));
}
""")

    def test_real_catalog_sources_reach_editor_without_replacing_pending_buffer(self):
        path = self.fixture.root / '创作约定.md'
        path.write_text('# 当前创作约定\n\n题材：都市＋悬疑\n', encoding='utf-8')
        packet = self.w._editor_catalog(self.fixture.root)['workspace']
        self.assertEqual(packet['classification']['status'], 'recorded')
        self.assertEqual(packet['classification']['tags'], ['都市', '悬疑'])
        self.js('const packet=' + json.dumps(packet, ensure_ascii=False) + ';\n' + r"""
const before=JSON.stringify([...docs]);showBookIdentity(packet,'/书库/作品');
const row=$('book-classification').children[0];assert.equal(row.dataset.classificationStatus,'recorded');
assert.deepEqual(row.children[1].children.map(tag=>tag.textContent),['都市','悬疑']);
const shown=text(row);for(const value of ['创作约定.md','第3行','题材：都市＋悬疑'])assert.ok(shown.includes(value),shown);
assert.equal(JSON.stringify([...docs]),before);
""")

    def test_real_catalog_read_failure_is_distinct_from_no_record_in_editor(self):
        path = self.fixture.root / '创作约定.md'
        path.write_bytes(b'\xff\xfe\x00invalid-utf8')
        packet = self.w._editor_catalog(self.fixture.root)['workspace']
        self.assertEqual(packet['classification']['reason'], 'read_error')
        self.js('const packet=' + json.dumps(packet, ensure_ascii=False) + ';\n' + r"""
const before=JSON.stringify([...docs]);showBookIdentity(packet,'/书库/作品');
const row=$('book-classification').children[0];assert.equal(row.children[1].children[0].textContent,'题材读取失败');
const shown=text(row);assert.ok(shown.includes('创作约定.md'));assert.ok(shown.includes('编码或读取权限'));
const details=row.children.find(child=>child.tag==='details');
assert.equal(details.children.filter(child=>child.className==='classification-note').length,0);
assert.equal(row.dataset.classificationEmpty,'false');assert.equal(JSON.stringify([...docs]),before);
""")


if __name__ == '__main__':
    unittest.main()

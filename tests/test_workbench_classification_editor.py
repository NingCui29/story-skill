"""Current work genre display follows catalog data without changing edit buffers."""
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
        result = subprocess.run([node, '-e', mock + self.source + '\n' + test], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_current_genres_have_explicit_sources_and_refresh_without_mutating_text(self):
        self.js(r"""
const before=JSON.stringify([...docs]);
showBookIdentity({title:'当前作品',kind:'long',classification:{status:'recorded',tags:['都市','悬疑'],sources:[{path:'创作约定.md',line:8,field:'题材',value:'都市＋悬疑'}]}},'/书库/作品');
assert.equal($('book-classification').hidden,false);const shown=text($('book-classification'));
for(const value of ['题材','都市','悬疑','分类依据','创作约定.md','第8行'])assert.ok(shown.includes(value),shown);
assert.equal(JSON.stringify([...docs]),before);
showBookIdentity({title:'当前作品',classification:{status:'missing',tags:[],sources:[]}},'/书库/作品');
assert.ok(text($('book-classification')).includes('题材待分类'));assert.ok(!text($('book-classification')).includes('悬疑'));
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


if __name__ == '__main__':
    unittest.main()

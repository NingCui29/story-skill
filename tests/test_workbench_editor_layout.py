"""Compact work identity retains safe metadata and the existing editing controls."""
import base64
import hashlib
from html.parser import HTMLParser
import re
import shutil
import subprocess
import unittest

import test_workbench as base


class PageTree(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.nodes = {}
        self.ids = []
        self.stack = []
        self.feed(page)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        node = {'tag': tag, 'attrs': attrs, 'parents': list(self.stack)}
        if 'id' in attrs:
            self.ids.append(attrs['id'])
            self.nodes[attrs['id']] = node
        if tag not in {'meta', 'input', 'img', 'br', 'hr', 'link'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                break

    def beneath(self, child, parent):
        return self.nodes[parent] in self.nodes[child]['parents']


class WorkbenchEditorLayoutTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w = base.story.workbench
        self.packet = self.w.snapshot(self.fixture.book)
        self.page = self.w._editor_page(self.packet, {}, 'layout-test').decode()
        self.script = self.page.split('<script>', 1)[1].split('</script>', 1)[0]
        self.tree = PageTree(self.page)

    def js(self, source, test):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        mock = r"""
const assert=require('assert');
class E {
 constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.value='';this.textContent='';this.attributes={};this.listeners={};this.open=false;}
 set innerHTML(value){throw Error('Metadata must be plain text');}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;}
 setAttribute(key,value){this.attributes[key]=value;}
 addEventListener(key,fn){this.listeners[key]=fn;}
 contains(target){return target===this||this.children.some(child=>child.contains(target));}
}
const nodes={},$=id=>nodes[id]||(nodes[id]=new E());
const handlers={},document={title:'',createElement:tag=>new E(tag),addEventListener:(key,fn)=>handlers[key]=fn,querySelector:()=>({inert:false})};
$('book-title').textContent='当前作品';$('book-kind').dataset.kind='long';$('current-book-root').textContent='/当前/作品';
const docs=new Map([['formal:1',{text:'正式原文',value:'未保存的当前文字',editable:true,saving:true}]]);
let active='formal:1';
"""
        result = subprocess.run([node, '-e', mock + source + '\n' + test],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def identity_source(self):
        return (self.w._book_display_script()
                + self.script[self.script.index('let bookChoices='):self.script.index('function chapterNumber')])

    def test_work_metadata_is_inside_closed_information_menu(self):
        self.assertEqual(self.tree.nodes['book-info']['tag'], 'details')
        self.assertNotIn('open', self.tree.nodes['book-info']['attrs'])
        self.assertIn('menu', self.tree.nodes['book-info']['attrs']['class'].split())
        for identity in ('book-info-title', 'book-progress-detail', 'book-classification',
                         'current-book-location', 'current-book-root', 'root-location', 'root-hint'):
            self.assertTrue(self.tree.beneath(identity, 'book-info'), identity)
        for identity in ('book-title', 'book-kind', 'book-progress'):
            self.assertFalse(self.tree.beneath(identity, 'book-info'), identity)
        self.assertIn('<summary>作品信息</summary>', self.page)

    def test_existing_control_ids_remain_unique_and_available(self):
        required = '''book-title book-kind current-book-root book-classification book-progress
            nav-toggle context-toggle refresh refresh-compact maintenance layout-reset
            reload-source recover-reload root-location root-hint build-label
            book-nav book-context nav-close context-close panel-dismiss resize-left resize-right
            book-select book-open book-hint search full-query full-go search-status search-results
            search-prev search-next view-chapters view-files locate newer older range list
            pending-box pending-count pending-list directory-warning
            edit compare reading-options font-size line-height font-family focus-reading
            save more-actions review-task source-view download message title document-body
            document-identity document-role document-unsaved chapter-picker chapter-version
            chapter-picker-help state count-details word-count count-method history-note
            formatted prose text cover match-view match-tools match-back match-prev match-next
            match-position match-query match-text review-task-box review-task-status
            review-findings review-prompt copy-review difference compare-back diff-prev diff-next
            diff-position compare-label before after context-title chapter-info version-details
            detail file-location path adoption-help'''.split()
        self.assertEqual(len(self.tree.ids), len(set(self.tree.ids)))
        self.assertTrue(set(required).issubset(self.tree.nodes))

    def test_manuscript_identity_versions_and_saving_stay_in_document_area(self):
        for identity in ('document-role', 'document-unsaved', 'chapter-version', 'save',
                         'edit', 'compare', 'text', 'prose', 'download', 'history-note'):
            self.assertTrue(any(node['tag'] == 'main' for node in self.tree.nodes[identity]['parents']), identity)
            self.assertFalse(self.tree.beneath(identity, 'book-info'), identity)
        self.assertEqual(self.tree.nodes['chapter-version']['attrs']['aria-describedby'], 'chapter-picker-help')
        self.assertEqual(self.tree.nodes['text']['attrs']['aria-label'], '候选正文编辑')

    def test_complete_long_or_unsafe_book_title_is_safe_and_available(self):
        unsafe = '<img src=x onerror=alert(1)>很长的作品名称' * 5
        self.packet['book']['title'] = unsafe
        page = self.w._editor_page(self.packet, {}, 'layout-test').decode()
        tree = PageTree(page)
        self.assertEqual(tree.nodes['book-title']['attrs']['title'], unsafe)
        self.assertIn('book-info-title', tree.nodes)
        self.assertNotIn('<img src=x onerror=alert(1)>', page)

    def test_information_refresh_follows_renamed_work_without_touching_edit_buffers(self):
        self.js(self.identity_source(), r"""
const before=JSON.stringify([...docs]);
const title='<b>更新后的完整书名</b>'.repeat(12);
showBookIdentity({title,kind:'analysis',classification:{status:'recorded',tags:['悬疑'],sources:[{path:'分类记录.md',field:'题材',value:'悬疑'}]}},'/更新/作品');
assert.equal($('book-title').textContent,title);assert.equal($('book-title').title,title);
assert.equal($('book-info-title').textContent,title);assert.equal($('book-kind').textContent,'作品分析');
assert.equal($('current-book-root').textContent,'/更新/作品');assert.equal($('book-classification').hidden,false);
assert.equal(JSON.stringify([...docs]),before);assert.equal(active,'formal:1');
""")

    def test_full_progress_remains_inspectable_without_changing_manuscript_state(self):
        source = self.identity_source()
        source += self.script[self.script.index('function drawCatalog'):self.script.index('// Paragraph LCS')]
        self.js(source, r"""
let currentCatalog,viewMode='files';const showChapterInfo=()=>{},updateChapterPicker=()=>{};
const before=JSON.stringify([...docs]);
drawCatalog({book_root:'/当前/作品',files:[],material_roots:['/材料目录'],workspace:{title:'当前作品',kind:'long',formal_total:38,planned_total:52,next_chapter:39,captured_at:'2026-10-06T00:00:00Z'}},new Map());
const progress=$('book-progress').textContent;
assert.ok(progress.includes('正式 38 章'));assert.ok(progress.includes('已规划 52 章'));assert.ok(progress.includes('第39章'));
assert.equal($('book-progress').title,progress);assert.equal($('book-progress-detail').textContent,progress);
assert.ok($('root-hint').textContent.includes('/材料目录'));
assert.equal(JSON.stringify([...docs]),before);assert.equal(active,'formal:1');
""")

    def test_new_information_menu_closes_with_escape_or_outside_pointer_only(self):
        source = self.script[self.script.index("document.addEventListener('keydown'"):self.script.index(" $('source-view').onclick")]
        # The source registers callbacks only; closing metadata must not call catalog or mutate docs.
        self.js('let closes=0;const closePanels=()=>closes++,trapPanelFocus=()=>false;\n' + source, r"""
const before=JSON.stringify([...docs]),info=$('book-info'),child=new E();info.append(child);
info.open=true;handlers.pointerdown({target:child});assert.equal(info.open,true);
handlers.pointerdown({target:new E()});assert.equal(info.open,false);
info.open=true;$('maintenance').open=true;handlers.keydown({key:'Escape'});
assert.equal(info.open,false);assert.equal($('maintenance').open,false);assert.equal(closes,1);
info.open=true;info.listeners.click({target:{closest:()=>null}});assert.equal(info.open,true);
assert.equal(JSON.stringify([...docs]),before);
""")

    def test_generated_editor_script_keeps_valid_csp_and_javascript_syntax(self):
        digest = base64.b64encode(hashlib.sha256(self.script.encode()).digest()).decode()
        self.assertIn("script-src 'sha256-" + digest + "'", self.page)
        self.assertEqual(len(re.findall(r'<script>', self.page)), 1)
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        result = subprocess.run([node, '-e', "new (require('vm').Script)(require('fs').readFileSync(0,'utf8'));"],
                                input=self.script, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()

"""Readable work identity without relying on titles or technical identifiers alone."""
import html
import shutil
import subprocess
import unittest

import test_workbench as base


class WorkbenchBookDisplayTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w = base.story.workbench
        self.packet = self.w.snapshot(self.fixture.book)
        self.page = self.w._editor_page(self.packet, {}, 'test').decode()
        self.script = self.page.split('<script>', 1)[1].split('</script>', 1)[0]

    def js(self, source, test):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        mock = r"""
const assert=require('assert');
class E {
 constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.value='';this._text='';this.selected=false;this.attributes={};}
 set textContent(value){this._text=String(value);this.children=[];}
 get textContent(){return this._text;}
 set innerHTML(value){throw Error('Work identity must use text nodes');}
 replaceChildren(...children){this.children=children;this._text='';}
 append(...children){this.children.push(...children);}
 setAttribute(key,value){this.attributes[key]=value;}
 click(){this.clicked=true;}
}
const nodes={},$=id=>nodes[id]||(nodes[id]=new E());
const document={title:'',createElement:tag=>new E(tag),createTextNode:text=>({textContent:text})};
$('book-title').textContent='当前作品';$('book-kind').dataset.kind='long';$('current-book-root').textContent='/当前/作品';
"""
        result = subprocess.run(
            [node, '-e', mock + source + '\n' + test],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def editor_identity_source(self):
        return (self.w._book_display_script()
                + self.script[self.script.index('let bookChoices='):self.script.index('function chapterNumber')]
                + self.script[self.script.index('async function loadBooks'):self.script.index('function fitColumns')])

    def test_same_title_displays_each_work_type_and_distinguishing_directory(self):
        self.js(self.w._book_display_script(), r"""
const books=['long','short','analysis'].map((kind,index)=>({title:'灯下',kind,root:'/书库/'+index+'/同名目录',key:'TECHNICAL-KEY-'+index,book_id:'TECHNICAL-ID-'+index}));
const rows=bookDisplayRows(books);
assert.deepEqual(rows.map(row=>row.kindLabel),['长篇','短篇','作品分析']);
assert.equal(new Set(rows.map(row=>row.label)).size,3);
for(let i=0;i<3;i++){assert.ok(rows[i].label.startsWith('灯下 · '+rows[i].kindLabel));assert.ok(rows[i].label.includes(i+'/同名目录'));assert.ok(!rows[i].label.includes('TECHNICAL'));}
assert.equal(bookDisplayRows([{title:'另一部',kind:'long',root:'/书库/另一部'}])[0].label,'另一部 · 长篇');
""")

    def test_identical_leaf_and_parent_names_expand_to_unique_full_directories(self):
        self.js(self.w._book_display_script(), r"""
for(const roots of [['/甲/共同/同名','/乙/共同/同名'],['C:\\甲\\共同\\同名','D:\\甲\\共同\\同名'],['/同名','/甲/同名']]){
 const rows=bookDisplayRows(roots.map(root=>({title:'同名作品',kind:'long',root})));
 assert.equal(new Set(rows.map(row=>row.label)).size,roots.length);
 assert.ok(rows.every(row=>row.label.includes('同名')));
 if(roots[0].includes('共同'))assert.ok(rows[0].label.includes(roots[0].replace(/\\/g,'/')));
}
""")

    def test_unknown_and_unavailable_types_are_explicit_and_never_guessed(self):
        self.js(self.w._book_display_script(), r"""
assert.equal(bookKindLabel({}),'类型未确认');
assert.equal(bookKindLabel({kind:'constructor'}),'类型未确认');
assert.equal(bookKindLabel({kind:'__proto__'}),'类型未确认');
assert.equal(bookKindLabel({kind:'future_kind'}),'类型未确认');
assert.equal(bookKindLabel({kind:'long',available:false}),'类型未核对');
assert.equal(bookKindLabel({kind:'short',kind_verified:false}),'类型未核对');
assert.equal(bookDisplayRows([{title:'旧作品',root:'/旧作品'}])[0].label,'旧作品 · 类型未确认');
""")

    def test_switch_menu_keeps_safe_text_selection_and_legacy_current_type(self):
        self.js(self.editor_identity_source(), r"""
let response={current:'/当前/作品',books:[{title:'<script>同名</script>',key:'CURRENT-KEY',root:'/当前/作品'},{title:'<script>同名</script>',kind:'short',key:'OTHER-KEY',root:'/其他/作品'}]};
const call=async()=>response;
(async()=>{await loadBooks();
 const options=$('book-select').children;assert.equal(options.length,2);assert.ok(options.every(option=>option.tag==='option'));
 assert.ok(options[0].selected);assert.ok(options[0].textContent.includes('<script>同名</script> · 长篇'));
 assert.ok(options[1].textContent.includes('短篇'));assert.equal(new Set(options.map(option=>option.textContent)).size,2);
 assert.equal(options[0].title,'/当前/作品');assert.ok(options.every(option=>!option.textContent.includes('KEY')));
 $('book-select').value='OTHER-KEY';await loadBooks();assert.ok($('book-select').children[1].selected);
})().catch(error=>{console.error(error);process.exit(1);});
""")

    def test_catalog_refresh_updates_current_header_and_renamed_menu_without_touching_text(self):
        source = self.editor_identity_source()
        source += self.script[self.script.index('function drawCatalog'):self.script.index('// Paragraph LCS')]
        self.js(source, r"""
const docs=new Map([['draft',{value:'未保存正文',text:'旧正文'}]]);let active='draft',currentCatalog,viewMode='files';
const showChapterInfo=()=>{},updateChapterPicker=()=>{};
bookChoices=[{title:'当前作品',kind:'long',root:'/当前/作品',key:'CURRENT-KEY'},{title:'改名 <b>灯</b>',kind:'short',root:'/另一处/作品',key:'OTHER-KEY'}];
renderBookChoices();$('book-select').value='OTHER-KEY';
drawCatalog({book_root:'/当前/作品',files:[],workspace:{title:'改名 <b>灯</b>',kind:'analysis',kind_verified:true,formal_total:0,planned_total:0,next_chapter:1,captured_at:'2026-10-05'}},new Map());
assert.equal($('book-title').textContent,'改名 <b>灯</b>');assert.equal($('book-title').title,'改名 <b>灯</b>');assert.equal(document.title,'改名 <b>灯</b> · 写作工作台');
assert.equal($('book-kind').textContent,'作品分析');assert.equal($('current-book-root').textContent,'/当前/作品');
const options=$('book-select').children;assert.ok(options[0].textContent.includes('作品分析'));assert.ok(options[1].selected);assert.equal(new Set(options.map(option=>option.textContent)).size,2);
assert.equal(docs.get('draft').value,'未保存正文');
showBookIdentity({title:'旧字段响应'},'/当前/作品');assert.equal($('book-kind').textContent,'作品分析');
showBookIdentity({title:'未确认类型',kind:null},'/当前/作品');assert.equal($('book-kind').textContent,'类型未确认');
""")

    def test_shelf_uses_the_same_labels_safe_text_and_expandable_directories(self):
        page = self.w._library_page('test').decode()
        script = page.split('<script>', 1)[1].split('</script>', 1)[0]
        source = script[script.index('function bookName'):script.index('async function refresh')]
        self.js(source, r"""
const books=[{title:'<img onerror=bad>',kind:'long',root:'/甲/相同/目录',key:'KEY-1',available:true},{title:'<img onerror=bad>',kind:'short',root:'/乙/相同/目录',key:'KEY-2',available:true},{title:'旧作品',root:'/旧作品',key:'KEY-3',available:false}];
const expected=bookDisplayRows(books),call=async()=>({url:'http://127.0.0.1:1234/editor/'});
render({books});
const rows=$('books').children;assert.equal(rows.length,3);
for(let i=0;i<3;i++){assert.equal(rows[i].children[0].textContent,expected[i].label);assert.equal(rows[i].children[0].tag,'strong');const details=rows[i].children.find(child=>child.tag==='details');assert.equal(details.children[0].textContent,'目录位置');assert.equal(details.children[1].textContent,books[i].root);}
assert.ok(rows[2].children.find(child=>child.tag==='button').disabled);assert.ok(rows[2].children[0].textContent.includes('类型未核对'));
(async()=>{await rows[0].children.find(child=>child.tag==='button').onclick();assert.equal($('message').children[0].textContent,'点击打开：'+expected[0].label);})().catch(error=>{console.error(error);process.exit(1);});
""")

    def test_initial_current_header_escapes_book_identity_and_contains_type(self):
        title = html.escape(self.packet['book']['title'], quote=True)
        root = html.escape(self.packet['book']['root'], quote=True)
        self.assertIn('<strong id="book-title" title="' + title + '">' + title + '</strong>', self.page)
        self.assertIn('id="book-kind" class="book-kind" data-kind="long">长篇</span>', self.page)
        self.assertIn('<details id="current-book-location"><summary>目录位置</summary>', self.page)
        self.assertIn('<p id="current-book-root">' + root + '</p>', self.page)
        self.assertNotIn('<script>alert("workbench-title")</script>', self.page)
        self.assertIn(self.w._book_display_script(), self.page)
        self.assertIn(self.w._book_display_script(), self.w._library_page('test').decode())


if __name__ == '__main__':
    unittest.main()

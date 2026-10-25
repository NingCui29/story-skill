"""Bookshelf filters and evidenced genre display remain independent of saved state."""
import shutil
import subprocess
import unittest

import test_workbench as base


class WorkbenchClassificationDisplayTests(unittest.TestCase):
    def setUp(self):
        self.page = base.story.workbench._library_page('test').decode()
        script = self.page.split('<script>', 1)[1].split('</script>', 1)[0]
        self.source = script[script.index('function bookName'):script.index('async function refresh')]

    def js(self, test):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        mock = r"""
const assert=require('assert');
class E {
 constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this._text='';this.attributes={};}
 set textContent(value){this._text=String(value);this.children=[];}
 get textContent(){return this._text;}
 set innerHTML(value){throw Error('Classification must use text nodes');}
 replaceChildren(...children){this.children=children;this._text='';}
 append(...children){this.children.push(...children);}
 setAttribute(key,value){this.attributes[key]=value;}
 click(){this.clicked=true;}
}
const nodes={},$=id=>nodes[id]||(nodes[id]=new E());
const document={createElement:tag=>new E(tag),createTextNode:text=>({textContent:text})};
// Lifecycle/session hooks sit outside the extracted renderer under test.
let managementDecorations=0;const rememberedShelfBooks=[];
function decorateBookManagement(){managementDecorations++;}
function rememberShelfBook(key){rememberedShelfBooks.push(key);}
const text=node=>[node.textContent,...(node.children||[]).map(text)].join(' ');
const descendants=node=>[node,...(node.children||[]).flatMap(descendants)];
const call=async()=>({url:'http://127.0.0.1:1234/editor/'});
const books=[
 {title:'同名作品',kind:'long',kind_verified:true,root:'/甲/同名',key:'key-long',available:true,classification:{status:'recorded',tags:['都市','悬疑'],sources:[]}},
 {title:'同名作品',kind:'short',kind_verified:true,root:'/乙/同名',key:'key-short',available:true,classification:{status:'missing',tags:[],sources:[]}},
 {title:'分析作品',kind:'analysis',kind_verified:true,root:'/分析',key:'key-analysis',available:true,classification:{status:'recorded',tags:['修仙'],sources:[]}},
 {title:'不可用作品',kind:'long',kind_verified:false,root:'/旧作品',key:'key-old',available:false,error:'作品目录待核对',classification:{status:'unavailable',tags:[],sources:[]}},
];
"""
        result = subprocess.run(
            [node, '-'], input=mock + self.source + '\n' + test, encoding='utf-8',
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_all_and_verified_work_types_have_independent_counts(self):
        self.js(r"""
render({books});
assert.equal($('filter-all').textContent,'全部 4');assert.equal($('filter-long').textContent,'长篇 1');assert.equal($('filter-short').textContent,'短篇 1');assert.equal($('filter-analysis').textContent,'作品分析 1');
assert.equal($('books').children.length,4);assert.ok($('filter-summary').textContent.includes('1 部类型尚未确认'));
for(const [kind,label]of [['long','长篇'],['short','短篇'],['analysis','作品分析']]){
 $('filter-'+kind).onclick();assert.equal($('books').children.length,1);assert.equal($('filter-'+kind).attributes['aria-pressed'],'true');assert.equal($('filter-all').attributes['aria-pressed'],'false');assert.ok($('books').children[0].children[0].textContent.includes(label));
}
$('filter-all').onclick();assert.equal($('books').children.length,4);
""")

    def test_refresh_keeps_current_filter_and_updates_total_and_type_counts(self):
        self.js(r"""
render({books});$('filter-short').onclick();
render({books:[...books,{title:'新增长篇',kind:'long',root:'/新长篇',key:'new',available:true}]});
assert.equal(shelfFilter,'short');assert.equal($('books').children.length,1);assert.equal($('filter-all').textContent,'全部 5');assert.equal($('filter-long').textContent,'长篇 2');assert.equal($('filter-short').attributes['aria-pressed'],'true');assert.equal($('filter-summary').textContent,'短篇 · 1 部');
""")

    def test_same_title_directory_identity_and_disabled_open_survive_filtering(self):
        self.js(r"""
render({books});const labels=$('books').children.map(row=>row.children[0].textContent);
assert.notEqual(labels[0],labels[1]);
$('filter-long').onclick();assert.equal($('books').children[0].children[0].textContent,labels[0]);
const directory=$('books').children[0].children.find(child=>child.tag==='details');assert.equal(directory.children[0].textContent,'目录位置');assert.equal(directory.children[1].textContent,'/甲/同名');
$('filter-all').onclick();const unavailable=$('books').children[3],open=unavailable.children.find(child=>child.tag==='button');
assert.ok(open.disabled);assert.ok(text(unavailable).includes('作品目录待核对'));assert.ok(text(unavailable).includes('类型未核对'));assert.ok(text(unavailable).includes('题材读取失败'));
""")

    def test_genres_use_only_recorded_general_tags_and_name_analysis_subjects(self):
        self.js(r"""
const row=classificationNode({kind:'long',tags:['非来源标签'],platform_category:'平台悬疑',classification:{status:'recorded',tags:['都市',' 悬疑 ','都市'],sources:[]}});
const tags=descendants(row).filter(child=>child.className==='genre-tag');assert.deepEqual(tags.map(tag=>tag.textContent),['都市','悬疑']);assert.equal(row.children[0].textContent,'题材');assert.ok(!text(row).includes('非来源标签'));assert.ok(!text(row).includes('平台悬疑'));
const analysis=classificationNode(books[2]);assert.equal(analysis.children[0].textContent,'分析对象题材');assert.ok(text(analysis).includes('修仙'));
const legacy=classificationNode({kind:'long',tags:['都市'],platform_category:'悬疑'});assert.equal(legacy.dataset.classificationEmpty,'true');assert.equal(legacy.children[1].hidden,true);assert.equal(legacy.children[1].children.length,0);assert.ok(!text(legacy).includes('都市'));assert.ok(!text(legacy).includes('待分类'));
""")

    def test_missing_review_and_unavailable_classifications_have_clear_pending_labels(self):
        self.js(r"""
for(const [status,expected]of [['missing',null],['recorded',null],['needs_review','题材待核对'],['unavailable','题材读取失败'],['future_status','题材待核对']]){
 const row=classificationNode({kind:'long',classification:{status,tags:status==='needs_review'?['未核对标签']:[],sources:[],note:'请核对原材料'}});
 if(expected)assert.ok(text(row).includes(expected));else{assert.equal(row.children[1].hidden,true);assert.equal(row.children[1].children.length,0);}
 assert.ok(!text(row).includes('未核对标签'));assert.ok(text(row).includes('请核对原材料'));
}
""")

    def test_classification_sources_preserve_literal_field_value_and_relative_path(self):
        self.js(r"""
const field='<img onerror=bad>',value='**都市** [链接](javascript:bad)',path='01_策划/<script>分类.md';
const row=classificationNode({kind:'analysis',classification:{status:'recorded',tags:['<b>悬疑</b>'],sources:[{path,field,value,line:12}],note:'<script>来源说明</script>'}});
assert.ok(text(row).includes('<b>悬疑</b>'));const details=row.children.find(child=>child.tag==='details');assert.equal(details.children[0].textContent,'题材依据');assert.ok(details.children[1].textContent.includes(path+' · 第12行'));assert.ok(details.children[1].textContent.includes(field+'：'+value));assert.equal(details.children[2].textContent,'<script>来源说明</script>');
assert.ok(!descendants(row).some(child=>['a','img','script','b'].includes(child.tag)));
""")

    def test_classification_reasons_distinguish_missing_conflicting_and_unreadable_material(self):
        self.js(r"""
for(const [status,reason,expected]of [
 ['missing','no_sources',null],['missing','no_declaration',null],
 ['missing','unsupported_format',null],['missing','unconfirmed',null],
 ['needs_review','conflict','题材记录冲突'],['needs_review','read_error','题材读取失败'],
 ['needs_review','limit','题材读取受限'],['unavailable','read_error','题材读取失败']]){
 const row=classificationNode({kind:'long',classification:{status,reason,tags:['不可视为确认的标签'],sources:[]}});
 assert.equal(row.dataset.classificationReason,reason);
 if(expected){const tag=row.children[1].children[0];assert.equal(row.children[1].children.length,1);assert.equal(tag.textContent,expected);assert.equal(tag.className,'genre-tag pending');}
 else{assert.equal(row.dataset.classificationEmpty,'true');assert.equal(row.children[1].children.length,0);assert.equal(row.children[1].hidden,true);}
 assert.ok(!text(row).includes('不可视为确认的标签'));assert.ok(!text(row).includes('待分类'));
 const details=row.children.find(child=>child.tag==='details');assert.ok(details);assert.equal(details.open,undefined);
}
""")

    def test_reading_details_keep_actual_paths_lines_fields_and_error_notes(self):
        self.js(r"""
const path='00_项目策划/题材 & 定位.md',field='作品类型',note='文件读取失败：权限不足';
const row=classificationNode({kind:'long',classification:{status:'needs_review',reason:'read_error',tags:[],sources:[],
 details:[{reason:'read_error',path,line:17,field,note}],checked_paths:[path,path,'创作约定.md'],note}});
const details=row.children.find(child=>child.tag==='details');assert.equal(details.children[0].textContent,'读取详情');
const issue=details.children.find(child=>child.className==='classification-issue');
for(const value of [path,'第17行','原字段：'+field,note])assert.ok(issue.textContent.includes(value));
assert.equal(details.children.filter(child=>child.className==='classification-note').length,0);
const checked=details.children.find(child=>child.className==='classification-paths');
assert.equal(checked.textContent,'已检查材料：\n'+path+'\n创作约定.md');
""")

    def test_conflict_keeps_both_literal_sources_without_showing_confirmed_tags(self):
        self.js(r"""
const row=classificationNode({kind:'long',classification:{status:'needs_review',reason:'conflict',tags:['都市'],
 sources:[{path:'创作约定.md',line:8,field:'题材',value:'都市'},{path:'00_项目策划/项目总览.md',line:12,field:'类型',value:'玄幻'}]}});
assert.equal(row.children[1].children[0].textContent,'题材记录冲突');
const sources=descendants(row).filter(child=>child.className==='classification-source');assert.equal(sources.length,2);
assert.ok(sources[0].textContent.includes('创作约定.md · 第8行\n题材：都市'));
assert.ok(sources[1].textContent.includes('00_项目策划/项目总览.md · 第12行\n类型：玄幻'));
assert.ok(!descendants(row).some(child=>child.className==='genre-tag'));
""")

    def test_unconfirmed_source_details_show_literal_original_field_and_value(self):
        self.js(r"""
const field='定位',value='<img src=x onerror=bad> **现实悬疑** [链接](javascript:bad)',path='00_项目策划/<script>定位.md';
const row=classificationNode({kind:'long',classification:{status:'missing',reason:'unsupported_format',tags:[],sources:[],
 details:[{reason:'unsupported_format',path,line:7,field,value,note:'尚不能作为明确题材标签显示。'}]}});
const details=row.children.find(child=>child.tag==='details'),issue=details.children.find(child=>child.className==='classification-issue');
assert.ok(issue.textContent.includes(path+' · 第7行'));assert.ok(issue.textContent.includes(field+'：'+value));
assert.ok(!issue.textContent.includes('原字段：定位'));assert.ok(!descendants(row).some(child=>['a','img','script','b'].includes(child.tag)));
assert.equal(row.children[1].hidden,true);assert.equal(row.children[1].children.length,0);
""")

    def test_recorded_labels_require_consistent_status_and_do_not_repeat_checked_paths(self):
        self.js(r"""
const good=classificationNode({classification:{status:'recorded',reason:'recorded',tags:['都市'],
 sources:[{path:'创作约定.md',line:8,field:'题材',value:'都市'}],checked_paths:['创作约定.md']}});
assert.equal(good.children[1].children[0].textContent,'都市');assert.equal(good.children[1].children[0].className,'genre-tag');
assert.ok(!descendants(good).some(child=>child.className==='classification-paths'));
const mismatched=classificationNode({classification:{status:'recorded',reason:'conflict',tags:['都市'],sources:[]}});
assert.equal(mismatched.children[1].children[0].textContent,'题材记录冲突');assert.ok(!text(mismatched).includes('都市'));
""")

    def test_empty_classification_is_folded_from_card_without_another_placeholder(self):
        self.assertIn('.book>.classification[data-classification-empty="true"]{display:none}', self.page)
        self.assertNotIn('题材待分类', self.page)
        self.assertNotIn('未读取到题材', self.page)
        self.assertNotIn('题材格式待核对', self.page)
        self.assertNotIn('暂无题材资料', self.page)

    def test_empty_filter_and_empty_shelf_clear_old_cards_and_keep_zero_counts(self):
        self.js(r"""
render({books:[books[0]]});$('filter-analysis').onclick();assert.equal($('books').children.length,0);assert.ok($('books').textContent.includes('当前分类暂无作品'));assert.equal($('filter-analysis').textContent,'作品分析 0');assert.equal($('filter-summary').textContent,'作品分析 · 0 部');
render({books:[]});assert.equal(shelfFilter,'analysis');assert.equal($('books').children.length,0);assert.ok($('books').textContent.includes('书架还是空的'));
for(const [kind,label]of shelfKinds)assert.equal($('filter-'+kind).textContent,label+' 0');
""")

    def test_page_exposes_accessible_type_filter_controls(self):
        self.assertIn('role="group" aria-label="按作品类型筛选"', self.page)
        self.assertIn('id="filter-summary" role="status"', self.page)
        for kind in ('all', 'long', 'short', 'analysis'):
            self.assertIn('id="filter-' + kind + '" aria-pressed=', self.page)


if __name__ == '__main__':
    unittest.main()

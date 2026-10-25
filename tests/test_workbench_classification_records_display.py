"""Cards display literal author records without turning them into genre tags."""
import json
import unittest

import test_workbench as base
import test_workbench_classification_display as display_fixture
import test_workbench_classification_editor as editor_fixture


class ClassificationRecordsDisplayTests(unittest.TestCase):
    def setUp(self):
        self.display = display_fixture.WorkbenchClassificationDisplayTests('runTest')
        self.display.setUp()

    def js(self, case):
        self.display.js(r"""
const record=(kind,value,more={})=>({kind,path:'创作约定.md',line:6,field:kind==='positioning'?'定位':'原字段',value,display_text:value,local_status:'declared',backend_status:'not_claimed',...more});
const book=(records,more={})=>({title:'作品',kind:'short',available:true,classification:{status:'recorded',reason:'recorded',tags:[],sources:[],records,primary_record:records[0],...more}});
const main=row=>row.children.find(child=>child.className==='genre-tags');
""" + case)

    def test_positioning_shows_literal_author_sentence_with_label_and_full_source(self):
        self.js(r"""
const value='现实都市家庭喜剧、旅行享受、中奖后的开心爽文。读者跟随女儿，看一家人重新认识彼此。';
const item=record('positioning',value,{display_text:'现实都市家庭喜剧、旅行享受、中奖后的开心爽文。'});
const row=classificationNode(book([item]));
assert.equal(row.children[0].textContent,'作品定位');assert.equal(row.children[0].className,'genre-label record-label');
assert.equal(main(row).children.length,1);assert.equal(main(row).children[0].className,'classification-record');
assert.equal(main(row).children[0].textContent,item.display_text);assert.equal(main(row).children[0].title,value);
assert.equal(row.dataset.classificationKind,'positioning');
const details=row.children.find(child=>child.tag==='details');assert.equal(details.children[0].textContent,'资料依据');
assert.ok(text(details).includes('创作约定.md · 第6行'));assert.ok(text(details).includes('定位：'+value));
assert.ok(!descendants(row).some(child=>child.className==='genre-tag'));
assert.ok(!text(row).includes('格式待核对'));assert.ok(!text(row).includes('暂无题材资料'));
""")

    def test_each_record_kind_has_accurate_label_and_synopsis_is_not_genre(self):
        self.js(r"""
for(const [kind,label]of [['positioning','作品定位'],['content_keywords','内容关键词'],['audience','读者定位'],['synopsis','作品简介']]){
 const item=record(kind,'作者原句，原样保留。'),row=classificationNode(book([item]));
 assert.equal(row.children[0].textContent,label);assert.equal(row.dataset.classificationKind,kind);
 assert.equal(main(row).children[0].textContent,item.value);assert.ok(!descendants(row).some(child=>child.className==='genre-tag'));
}
const creative=classificationNode(book([record('positioning','用人物行动推动叙事。',{field:'故事核心'})]));
assert.equal(creative.children[0].textContent,'创作定位');
""")

    def test_genre_tags_and_author_records_have_one_best_display_without_stacking(self):
        self.js(r"""
const suggested=record('platform_suggestion','古代言情／婚姻家庭',{local_status:'proposed',backend_status:'unverified'});
const intro=record('synopsis','一句话讲清这段故事。'),position=record('positioning','古代背景／婚姻背叛／女性脱身。');
const row=classificationNode(book([suggested,intro,position]));
assert.equal(row.dataset.classificationKind,'positioning');assert.equal(main(row).children.length,1);
assert.equal(main(row).children[0].textContent,position.value);
const genre=classificationNode(book([position,suggested],{tags:['都市','悬疑']}));
assert.equal(genre.dataset.classificationKind,'genre');assert.deepEqual(main(genre).children.map(child=>child.textContent),['都市','悬疑']);
assert.equal(descendants(main(genre)).filter(child=>child.className==='classification-record').length,0);
assert.equal(classificationPresentation(book([suggested,intro])).kind,'synopsis');
""")

    def test_platform_suggestion_stays_explicitly_proposed_and_backend_unverified(self):
        self.js(r"""
const item=record('platform_suggestion','女频／古代言情／宫闱宅斗',{field:'番茄拟选分类',platform:'fanqie',local_status:'proposed',backend_status:'unverified'});
const row=classificationNode(book([item]));assert.equal(row.children[0].textContent,'分类建议 · 拟选');
assert.equal(main(row).children[0].textContent,item.value);assert.ok(text(row).includes('本地状态：拟选；平台后台：未核对'));
assert.ok(!text(row).includes('已采用'));assert.ok(!text(row).includes('已确认'));assert.ok(!descendants(row).some(child=>child.className==='genre-tag'));
const local=record('positioning','历史悬疑与女性成长。');
assert.equal(classificationPresentation(book([item,local])).record.value,local.value);
""")

    def test_literal_record_text_paths_fields_and_notes_are_never_html(self):
        self.js(r"""
const value='<img src=x onerror=bad> **现实悬疑** [链接](javascript:bad)\n<script>bad()</script>';
const item=record('synopsis',value,{field:'一句话故事 <svg onload=bad>',path:'00_项目策划/<script>简介.md'});
const row=classificationNode(book([item]));assert.equal(main(row).children[0].textContent,value);
assert.equal(main(row).children[0].title,value);assert.ok(text(row).includes(item.path));assert.ok(text(row).includes(item.field+'：'+value));
assert.ok(!descendants(row).some(child=>['a','img','script','svg','b'].includes(child.tag)));
""")

    def test_record_search_follows_the_displayed_author_text_and_preserves_safety_boundaries(self):
        self.js(r"""
const item=record('positioning','现实都市家庭喜剧。女儿从豪华团长变成有自己假期的电灯泡。',{display_text:'现实都市家庭喜剧。'}),entry=book([item]);
assert.equal(shelfSearchMatch(entry,'家庭喜剧'),true);assert.equal(shelfSearchMatch(entry,'作品定位 电灯泡'),true);
assert.equal(shelfSearchMatch(entry,'原字段'),false);assert.equal(shelfSearchMatch(entry,'暂无题材资料'),false);
assert.equal(shelfSearchMatch(book([item],{status:'missing'}),'家庭喜剧'),false);
assert.equal(shelfSearchMatch(book([item],{status:'needs_review',reason:'conflict'}),'家庭喜剧'),false);
assert.equal(shelfSearchMatch(book([item],{tags:['玄幻']}),'家庭喜剧'),false);
assert.equal(shelfSearchMatch(book([item],{tags:['玄幻']}),'玄幻'),true);
""")

    def test_empty_records_fold_the_card_row_and_keep_reading_evidence_closed(self):
        self.js(r"""
const row=classificationNode({classification:{status:'missing',reason:'no_declaration',tags:[],sources:[],checked_paths:['创作约定.md'],note:'现行材料没有可展示的定位记录。'}});
assert.equal(main(row).children.length,0);assert.equal(main(row).hidden,true);assert.equal(row.children[0].hidden,true);
assert.equal(row.dataset.classificationEmpty,'true');
assert.equal(row.children.filter(child=>child.tag==='details').length,1);const details=row.children.find(child=>child.tag==='details');assert.ok(!details.open);
assert.ok(text(details).includes('创作约定.md'));assert.ok(!text(row).includes('暂无题材资料'));
""")

    def test_record_preview_has_two_line_css_limit_and_visible_source_kind(self):
        css = base.story.workbench._workbench_theme_css()
        self.assertIn('.classification-record{display:-webkit-box;', css)
        self.assertIn('-webkit-line-clamp:2', css)
        self.assertIn('.book>.classification>.genre-label:not(.record-label)', self.display.page)

    def test_real_author_positioning_reaches_editor_without_changing_pending_text(self):
        fixture = editor_fixture.ClassificationEditorTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        value = '古代背景／婚姻背叛／灾后生还／女性脱身／追悔不复合。'
        (fixture.fixture.root / '创作约定.md').write_text('# 当前创作约定\n\n定位：' + value + '\n', encoding='utf-8')
        packet = fixture.w._editor_catalog(fixture.fixture.root)['workspace']
        self.assertEqual(packet['classification']['status'], 'recorded')
        self.assertEqual(packet['classification']['tags'], [])
        self.assertEqual(packet['classification']['primary_record']['kind'], 'positioning')
        fixture.js('const packet=' + json.dumps(packet, ensure_ascii=False) + ';\n' + r"""
const before=JSON.stringify([...docs]);showBookIdentity(packet,'/书库/作品');
const row=$('book-classification').children[0];assert.equal(row.children[0].textContent,'作品定位');
assert.equal(row.children[1].children.length,1);assert.equal(row.children[1].children[0].textContent,packet.classification.primary_record.display_text);
const shown=text(row);for(const value of ['创作约定.md','第3行','定位：'+packet.classification.primary_record.value])assert.ok(shown.includes(value),shown);
assert.ok(!shown.includes('格式待核对'));assert.equal(JSON.stringify([...docs]),before);
""")


if __name__ == '__main__':
    unittest.main()

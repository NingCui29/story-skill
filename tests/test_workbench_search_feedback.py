"""Search feedback must never expose results from a previous request."""
from pathlib import Path
import shutil
import subprocess
import unittest


class WorkbenchSearchFeedbackTests(unittest.TestCase):
    def js(self, test):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        source = (Path(__file__).resolve().parents[1] /
                  'skills/story-skill/scripts/story_workbench.py').read_text()
        function = source.split('async function fullSearch(){', 1)[1].split('\n', 1)[0]
        harness = r"""
const assert=require('assert');
class E{constructor(){this.children=[];this.textContent='';this.value='';this.disabled=false;}replaceChildren(){this.children=[];}append(child){this.children.push(child);}}
const nodes={},$=id=>nodes[id]??=new E(),document={createElement:()=>new E()};
let searchSequence=0,fullSearchOffset=0,requests=[],opened=[];
const item=row=>{const b=new E();b.textContent=row.title;return b;};
const openDoc=(...args)=>opened.push(args);
const call=(name,args)=>new Promise((resolve,reject)=>requests.push({name,args,resolve,reject}));
const result=(id,more=false)=>({results:[{id,title:id,excerpt:'命中摘要'}],total:3,checked:5,complete:true,warnings:[],has_more:more});
"""
        output = subprocess.run([node, '-'], input=harness + 'async function fullSearch(){' +
                                 function + '\n(async()=>{' + test +
                                 '})().catch(e=>{console.error(e);process.exitCode=1;});', encoding='utf-8',
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(output.returncode, 0, output.stdout + output.stderr)

    def test_new_request_and_failure_clear_old_results_and_paging(self):
        self.js(r"""
$('full-query').value='旧词';const first=fullSearch();requests[0].resolve(result('old',true));await first;
assert.equal($('search-results').children.length,1);assert.equal($('search-next').disabled,false);
$('full-query').value='新词';const next=fullSearch();assert.equal($('search-results').children.length,0);
assert.equal($('search-prev').disabled,true);assert.equal($('search-next').disabled,true);
requests[1].reject(Error('合成离线'));await next;
assert.equal($('search-results').children.length,0);assert.equal($('search-status').textContent,'搜索失败：合成离线');
assert.equal($('search-prev').disabled,true);assert.equal($('search-next').disabled,true);assert.deepEqual(opened,[]);
""")

    def test_out_of_order_success_and_failure_do_not_replace_new_results(self):
        self.js(r"""
$('full-query').value='慢词';const slow=fullSearch();$('full-query').value='快词';const fast=fullSearch();
requests[1].resolve(result('new'));await fast;requests[0].resolve(result('old',true));await slow;
assert.equal($('search-results').children[0].textContent,'new');assert.equal($('search-next').disabled,true);
$('search-results').children[0].onclick();assert.deepEqual(opened,[['new','快词']]);
const stale=fullSearch(),current=fullSearch();requests[3].resolve(result('latest'));await current;
requests[2].reject(Error('旧失败'));await stale;assert.equal($('search-results').children[0].textContent,'latest');
assert.ok(!$('search-status').textContent.includes('旧失败'));
""")

    def test_empty_query_invalidates_pending_response_and_resets_offset(self):
        self.js(r"""
$('full-query').value='词';fullSearchOffset=30;const pending=fullSearch();assert.equal(requests[0].args.offset,30);
$('full-query').value='  ';await fullSearch();requests[0].resolve(result('late',true));await pending;
assert.equal(fullSearchOffset,0);assert.equal($('search-results').children.length,0);assert.equal($('search-status').textContent,'');
assert.equal($('search-next').disabled,true);assert.equal(requests.length,1);
""")

    def test_successful_page_restores_only_valid_controls(self):
        self.js(r"""
$('full-query').value='词';fullSearchOffset=30;const pending=fullSearch();requests[0].resolve(result('page-two',true));await pending;
assert.equal($('search-prev').disabled,false);assert.equal($('search-next').disabled,false);
assert.equal($('search-results').children[0].children[0].textContent,'命中摘要');
""")

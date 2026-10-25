"""Authenticated draft routes and UI state transitions, without platform writes."""
import http.client
import json
import shutil
import subprocess
import threading
import unittest
from unittest.mock import patch

import test_workbench as base


class PlatformDraftUITests(unittest.TestCase):
    def js(self, test):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        mock = r'''
const assert=require('assert');
const controls={},timers=new Map(),requests=[];let timerId=0,handler;
class Element {
 constructor(tag){this.tagName=tag;this.children=[];this._value='';this.listeners={};this.disabled=false;this.textContent='';this.classList={add:()=>{}};this.open=false;}
 set id(value){this._id=value;controls[value]=this;}get id(){return this._id;}
 get options(){return this.children.filter(row=>row.tagName==='option');}
 set value(value){this._value=value;}get value(){return this._value||this.options[0]?.value||'';}
 append(...rows){this.children.push(...rows);}replaceChildren(...rows){this.children=[...rows];this._value='';}
 setAttribute(){}addEventListener(name,fn){(this.listeners[name]||=[]).push(fn);}
 showModal(){this.open=true;}close(){this.open=false;for(const fn of this.listeners.close||[])fn();}focus(){}remove(){}
}
const $=id=>controls[id]||(controls[id]=new Element('button'));
const document={createElement:tag=>new Element(tag),body:new Element('body')};
const editorDialog=title=>{const dialog=new Element('dialog'),content=new Element('div');dialog.append(content);dialog.showModal();return {dialog,content};};
const docs=new Map([['editor',{text:'旧正式稿',value:'用户未保存的全部文字',editable:true}]]),dirty=d=>!!d&&d.text!==d.value;
const currentBookInfo={kind:'long',kind_verified:true,title:'验收作品'};
const setTimeout=(fn,delay)=>{const id=++timerId;timers.set(id,{fn,delay});return id;},clearTimeout=id=>timers.delete(id);
const call=async(action,payload={})=>{requests.push({action,payload:structuredClone(payload)});return handler(action,payload);};
const plan=(id='plan-1',extra={})=>({id,platform:'fanqie',status:'prepared',book_title:'验收作品',account_id:'account',remote_book_id:'remote',chapters:[1,2],...extra});
const profile={id:'本机.json',label:'番茄页面',valid:true,platform:'fanqie',sha256:'profile-sha'};
const catalog=(plans=[plan()],jobs=[],extra={})=>({book_id:'book-one',revision:7,formal_first:1,formal_last:13,formal_count:13,max_batch:1000,plans,profiles:[profile],jobs,next_offset:null,...extra});
const preview=payload=>({comparison_policy:payload.comparison_policy,body_encoding:payload.body_encoding,book_id:'book-one',plan_id:payload.plan_id,profile_id:payload.profile_id,profile_sha256:payload.profile_id?'profile-sha':null,mode:payload.reconcile_only?'reconcile_only':payload.sync_drafts?'sync_drafts':'save_draft',preview_token:payload.profile_id?(Math.floor(Date.now()/1000)+600)+'.signed':null,can_start:!!payload.profile_id,blockers:payload.profile_id?[]:['profile_required'],preview:{platform:'fanqie',account_id:'account',remote_book_id:'remote',chapters:[{chapter:1,title:'标题一',body_characters:100},{chapter:2,title:'标题二',body_characters:200}],results:[{chapter:1,state:'pending',last_observed_at:null,reason:null,observed_this_run:false,title_body_verified:false}]}});
const job=(extra={})=>({comparison_policy:'strict',body_encoding:'plain_paragraphs_v1',id:'job-one',book_id:'book-one',plan_id:'plan-1',mode:'sync_drafts',state:'running',stage:'waiting_login',done:false,total_chapters:2,verified_chapters:0,result:null,...extra});
handler=async(action,payload)=>{if(action.endsWith('/catalog'))return catalog();if(action.endsWith('/preview'))return preview(payload);if(action.endsWith('/start'))return job();if(action.endsWith('/job'))return job();throw Error(action);};
const textTree=node=>node.textContent+'\n'+node.children.map(textTree).join('\n');
'''
        source = base.story.workbench._editor_platform_drafts_script()
        script = mock + source + '\n(async()=>{' + test + \
                 '\n})().catch(error=>{console.error(error);process.exitCode=1;});'
        result = subprocess.run([node, '-'], input=script, encoding='utf-8',
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_complete_paging_preview_and_one_batch_click_preserve_dirty_editor(self):
        self.js(r'''
handler=async(action,payload)=>{
 if(action.endsWith('/catalog'))return payload.offset===0?catalog([plan()],[],{next_offset:1}):catalog([plan('plan-2')]);
 if(action.endsWith('/preview'))return preview(payload);
 if(action.endsWith('/start'))return job();throw Error(action);
};
const before=JSON.stringify([...docs]);await platformDraftOpen();const p=platformDraftPanel;
assert.deepEqual(requests.filter(r=>r.action.endsWith('/catalog')).map(r=>r.payload.offset),[0,1]);
assert.equal(p.plan.options.length,2);assert.equal(p.run.disabled,false);assert.equal(p.preview.preview.chapters.length,2);assert.equal(p.progress.max,2);assert.ok(p.progressLabel.textContent.includes('0 / 2'));assert.equal(p.results.children[0].textContent,'第1章 · 本次未处理');
assert.ok(!p.plan.options.some(o=>o.textContent.includes('plan-')));assert.ok(textTree(p.content).includes('当前仍有未保存编辑'));
await p.run.onclick();assert.equal(requests.filter(r=>r.action.endsWith('/start')).length,1);
const sent=requests.find(r=>r.action.endsWith('/start')).payload;
assert.equal(sent.execute,true);assert.equal(sent.profile_sha256,'profile-sha');assert.equal(sent.reconcile_only,false);
assert.equal(JSON.stringify([...docs]),before);assert.equal(p.progress.value,0);assert.equal(p.run.disabled,true);
''')

    def test_duplicate_click_and_close_reopen_reconnect_without_resend(self):
        self.js(r'''
let release;handler=async(action,payload)=>{if(action.endsWith('/catalog'))return catalog([],requests.some(r=>r.action.endsWith('/start'))?[job()]:[]);if(action.endsWith('/preview'))return preview(payload);if(action.endsWith('/start'))return new Promise(resolve=>{release=resolve;});throw Error(action);};
// Give the initial catalog a plan; reopening will return the active job.
const previous=handler;let catalogs=0;handler=async(action,payload)=>action.endsWith('/catalog')&&++catalogs===1?catalog():previous(action,payload);
await platformDraftOpen();const p=platformDraftPanel;const first=p.run.onclick();await p.run.onclick();
assert.equal(requests.filter(r=>r.action.endsWith('/start')).length,1);p.dialog.close();release(job());await first;
assert.equal(platformDraftPanel,null);assert.equal([...timers.values()].filter(t=>t.delay===1500).length,0);
await platformDraftOpen();const reopened=platformDraftPanel;assert.equal(reopened.job.id,'job-one');assert.equal(reopened.run.disabled,true);
assert.equal(requests.filter(r=>r.action.endsWith('/start')).length,1);assert.equal([...timers.values()].filter(t=>t.delay===1500).length,1);
reopened.dialog.close();assert.equal([...timers.values()].filter(t=>t.delay===1500).length,0);
''')

    def test_missing_configuration_and_dependency_disable_start(self):
        self.js(r'''
handler=async(action,payload)=>action.endsWith('/catalog')?catalog([plan()],[],{profiles:[]}):preview(payload);
await platformDraftOpen();let p=platformDraftPanel;assert.equal(p.run.disabled,true);assert.ok(p.configNote.textContent.includes('无需填写 JSON'));await p.run.onclick();assert.equal(requests.filter(r=>r.action.endsWith('/start')).length,0);
p.dialog.close();handler=async(action,payload)=>action.endsWith('/catalog')?catalog():{...preview(payload),can_start:false,blockers:['platform_browser_dependency_missing'],preview_token:null};
await platformDraftOpen();p=platformDraftPanel;assert.equal(p.run.disabled,true);assert.ok(p.status.textContent.includes('浏览器依赖'));assert.ok(platformDraftErrorCode('platform_browser_unsupported_editor').includes('正文编辑结构'));assert.ok(platformDraftErrorCode('platform_browser_navigation_timeout').includes('登录入口未能完成导航'));assert.ok(platformDraftErrorCode('platform_browser_unnumbered_draft').includes('无法核对章号的草稿'));assert.ok(platformDraftErrorCode('platform_browser_incomplete_scope').includes('筛选条件'));assert.ok(platformDraftErrorCode('platform_browser_ambiguous_draft').includes('同章草稿不唯一'));
''')

    def test_late_preview_and_expiry_never_authorize_wrong_selection(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;let release;
handler=async(action,payload)=>payload.profile_id==='old-profile'?new Promise(resolve=>{release=()=>resolve(preview(payload));}):preview(payload);
p.profile.value='old-profile';const old=platformDraftPreview(p);p.profile.value='本机.json';await platformDraftPreview(p);release();await old;
assert.equal(p.preview.profile_id,'本机.json');assert.equal(p.run.disabled,false);
const expiry=timers.get(p.expiryTimer);expiry.fn();assert.equal(p.run.disabled,true);assert.equal(p.preview,null);
await p.run.onclick();assert.equal(requests.filter(r=>r.action.endsWith('/start')).length,0);
''')

    def test_failed_start_and_readonly_poll_retry_never_claim_saved_or_resend(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;handler=async()=>{throw Error('预览已经过期');};await p.run.onclick();
assert.ok(p.status.textContent.includes('未能确认任务启动'));assert.ok(!p.status.textContent.includes('已保存'));assert.equal(p.run.disabled,true);
platformDraftDrawJob(p,job());const sent=requests.filter(r=>r.action.endsWith('/start')).length;
await platformDraftPoll(p);assert.ok(p.status.textContent.includes('后台可能仍在执行'));assert.equal(p.run.disabled,true);
assert.equal(requests.filter(r=>r.action.endsWith('/start')).length,sent);assert.equal([...timers.values()].filter(t=>t.delay===1500).length,1);
''')

    def test_verified_count_and_history_results_remain_distinct_and_read_only_mode_is_bound(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;
platformDraftDrawJob(p,job({done:true,state:'partial_failure',verified_chapters:1,result:{source_matches_current:false,local_version_changed:true,results:[{chapter:1,state:'saved',observed_this_run:false,title_body_verified:false},{chapter:2,state:'conflict',reason:'remote_content_differs',observed_this_run:true,title_body_verified:false}]}}));
assert.equal(p.progress.value,1);assert.ok(p.results.children[0].textContent.includes('本次未处理'));assert.ok(p.historyResults.children[0].textContent.includes('历史状态'));assert.ok(p.results.children[1].textContent.includes('本次观察'));assert.ok(p.results.children[1].textContent.includes('远端草稿内容与冻结清单不同'));assert.ok(p.status.textContent.includes('本地正式稿已有变化'));
p.mode.value='reconcile_only';await platformDraftPreview(p);assert.equal(p.run.disabled,false);await p.run.onclick();
assert.equal(requests.findLast(r=>r.action.endsWith('/start')).payload.reconcile_only,true);
''')

    def test_refresh_reconnect_clears_previous_preview_and_aligns_task_controls(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;assert.ok(textTree(p.previewBox).includes('绑定账号'));
handler=async()=>catalog([plan(),plan('old-plan',{platform:'qidian'})],[job({plan_id:'old-plan',profile_id:'起点.json',platform:'qidian',mode:'reconcile_only'})],{profiles:[profile,{id:'起点.json',label:'起点页面',valid:true,platform:'qidian'}]});
await platformDraftCatalog(p);assert.equal(p.previewBox.children.length,0);assert.equal(p.plan.value,'old-plan');assert.equal(p.profile.value,'起点.json');assert.equal(p.mode.value,'reconcile_only');assert.ok(p.jobNote.textContent.includes('起点'));assert.equal(p.run.disabled,true);
''')

    def test_analysis_and_invalid_pagination_stop_before_preview(self):
        self.js(r'''
currentBookInfo.kind='analysis';platformDraftAvailability();await platformDraftOpen();assert.equal(requests.length,0);assert.equal($('platform-drafts-open').disabled,true);
currentBookInfo.kind='long';handler=async()=>catalog([plan()],[],{next_offset:0});await platformDraftOpen();
assert.ok(platformDraftPanel.status.textContent.includes('分页无效'));assert.equal(platformDraftPanel.run.disabled,true);assert.equal(requests.length,1);
''')

    def test_fixed_format_and_strict_comparison_are_bound_to_preview_and_start(self):
        self.js(r'''
handler=async(action,payload)=>{
 if(action.endsWith('/catalog'))return catalog();
 if(action.endsWith('/preview'))return {...preview(payload),comparison_policy:payload.comparison_policy,body_encoding:payload.body_encoding};
 if(action.endsWith('/start'))return job({comparison_policy:payload.comparison_policy,body_encoding:payload.body_encoding});throw Error(action);
};
await platformDraftOpen();const p=platformDraftPanel;
assert.equal(p.comparison.value,'strict');assert.equal(p.encoding.value,'plain_paragraphs_v1');
p.comparison.value='fanqie_terminal_lf_v1';p.encoding.value='markdown_paragraphs_v1';
assert.equal(platformDraftCanStart(p),false);await p.encoding.onchange();
assert.ok(textTree(p.previewBox).includes('每段只换行一次'));assert.ok(textTree(p.previewBox).includes('包括末尾换行在内逐字核对'));
await p.run.onclick();const sent=requests.findLast(r=>r.action.endsWith('/start')).payload;
assert.equal(sent.comparison_policy,'strict');assert.equal(sent.body_encoding,'plain_paragraphs_v1');
assert.equal(p.comparison.disabled,true);assert.equal(p.encoding.disabled,true);
''')

    def test_stale_format_preview_does_not_authorize_changed_selection(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;let release;
let previews=0;handler=async(action,payload)=>++previews===1?new Promise(resolve=>{release=()=>resolve({...preview(payload),comparison_policy:'fanqie_terminal_lf_v1',body_encoding:'verbatim'});}):preview(payload);
p.comparison.value='fanqie_terminal_lf_v1';const stale=platformDraftPreview(p);
await platformDraftPreview(p);release();await stale;
assert.equal(p.preview.comparison_policy,'strict');assert.equal(p.run.disabled,false);
p.encoding.value='markdown_paragraphs_v1';await p.run.onclick();assert.equal(requests.filter(r=>r.action.endsWith('/start')).length,0);
''')

    def test_platform_format_is_fixed_and_stale_selection_cannot_start(self):
        self.js(r'''await platformDraftOpen();const p=platformDraftPanel;
assert.equal(p.encoding.value,'plain_paragraphs_v1');assert.equal(p.encoding.type,'hidden');
assert.ok(textTree(p.content).includes('统一去掉空白行'));
p.encoding.value='markdown_paragraphs_v2';await p.run.onclick();
assert.equal(requests.filter(r=>r.action.endsWith('/start')).length,0);
await platformDraftPreview(p);assert.equal(p.encoding.value,'plain_paragraphs_v1');
assert.ok(textTree(p.previewBox).includes('去掉全部空白行'));
await p.run.onclick();assert.equal(requests.findLast(r=>r.action.endsWith('/start')).payload.body_encoding,'plain_paragraphs_v1');
''')

    def test_equivalent_results_are_not_labelled_as_exact_success(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;
platformDraftDrawJob(p,job({done:true,state:'succeeded_equivalent',verified_chapters:0,accepted_chapters:2,equivalent_chapters:2,body_encoding:'markdown_paragraphs_v1',comparison_policy:'fanqie_terminal_lf_v1',result:{results:[{chapter:1,state:'reconciled_equivalent',observed_this_run:true,title_body_verified:false,accepted_this_run:true,body_encoding:'markdown_paragraphs_v1'},{chapter:2,state:'saved_equivalent',observed_this_run:true,title_body_verified:false,accepted_this_run:true,body_encoding:'markdown_paragraphs_v1'}]}}));
assert.equal(p.progress.value,2);assert.ok(p.progressLabel.textContent.includes('逐字一致 0'));assert.ok(p.progressLabel.textContent.includes('按选定格式核对 2'));
assert.ok(p.status.textContent.includes('按所选段落排版'));assert.ok(!p.status.textContent.includes('已保存并完整回读'));
assert.ok(p.results.children[0].textContent.includes('按段落排版核对'));
platformDraftDrawJob(p,job({done:true,state:'succeeded_equivalent',verified_chapters:0,accepted_chapters:1,equivalent_chapters:1}));assert.ok(p.status.textContent.includes('仍有章节待核实'));
''')

    def test_format_authorization_does_not_follow_another_plan_or_platform(self):
        self.js(r'''
handler=async(action,payload)=>action.endsWith('/catalog')?catalog([plan(),plan('another',{platform:'qidian'})],[],{profiles:[profile,{...profile,id:'qidian.json',platform:'qidian'}]}):preview(payload);
await platformDraftOpen();const p=platformDraftPanel;
p.comparison.value='fanqie_terminal_lf_v1';p.encoding.value='markdown_paragraphs_v1';p.plan.value='another';await p.plan.onchange();
assert.equal(p.comparison.value,'strict');assert.equal(p.encoding.value,'plain_paragraphs_v1');assert.equal(p.comparison.disabled,true);assert.equal(p.encoding.disabled,true);
''')

    def test_local_binding_selects_returned_plan_and_profile_without_start_or_editor_change(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel,before=JSON.stringify([...docs]);
assert.equal(p.bindStart.value,'13');assert.equal(p.bindEnd.value,'13');
assert.equal(p.bindingInputs[0].options.filter(o=>o.disabled).length,2);
let bound=false;handler=async(action,payload)=>{
 if(action.endsWith('/bind')){bound=true;return {ok:true,book_id:'book-one',revision:7,platform:'fanqie',account_id:'123456',remote_book_id:'876543',remote_book_title:'新作品',profile_id:'generated.json',plan_id:'new-plan',plan_reused:false,remote_accessed:false,submission_performed:false,scope:'local_binding_and_frozen_plan_only'};}
 if(action.endsWith('/catalog'))return catalog([plan(),plan('new-plan')],[],{profiles:[profile,{...profile,id:'generated.json'}]});
 if(action.endsWith('/preview'))return {...preview(payload),can_start:false,blockers:['platform_chrome_worker_unavailable']};
 throw Error(action);
};
p.bindAccount.value='123456';p.bindURL.value='https://fanqienovel.com/main/writer/chapter-manage/876543&%E6%96%B0%E4%BD%9C%E5%93%81?type=2';p.bindAccount.oninput();
assert.equal(p.previewBox.children.length,0);assert.equal(p.run.disabled,true);await p.bindForm.onsubmit({preventDefault(){}});
assert.equal(bound,true);assert.equal(p.plan.value,'new-plan');assert.equal(p.profile.value,'generated.json');
const sent=requests.find(r=>r.action.endsWith('/bind')).payload;
assert.deepEqual(sent,{account_id:'123456',book_management_url:p.bindURL.value,start:13,end:13,expected_revision:7});
assert.ok(!('book_id' in sent));assert.equal(requests.filter(r=>r.action.endsWith('/start')).length,0);
assert.equal(JSON.stringify([...docs]),before);assert.ok(p.bindNote.textContent.includes('尚未访问平台'));assert.equal(p.run.disabled,true);
''')

    def test_invalid_binding_and_unsaved_binding_never_start_and_duplicate_submit_is_local_once(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;
p.bindAccount.value='昵称';await platformDraftBind(p);assert.equal(requests.filter(r=>r.action.endsWith('/bind')).length,0);
p.bindAccount.value='123456';p.bindURL.value='https://other.example/book';await platformDraftBind(p);assert.equal(requests.filter(r=>r.action.endsWith('/bind')).length,0);
p.bindURL.value='https://fanqienovel.com/main/writer/chapter-manage/876543&Title?type=1';p.bindEnd.value='1';await platformDraftBind(p);assert.equal(requests.filter(r=>r.action.endsWith('/bind')).length,0);
p.bindEnd.value='13';p.bindAccount.oninput();await p.run.onclick();assert.equal(requests.filter(r=>r.action.endsWith('/start')).length,0);
let release;handler=async(action,payload)=>action.endsWith('/bind')?new Promise(resolve=>{release=resolve;}):catalog();
const first=platformDraftBind(p);await platformDraftBind(p);assert.equal(requests.filter(r=>r.action.endsWith('/bind')).length,1);
p.dialog.close();release({book_id:'book-one',platform:'fanqie',account_id:'123456',plan_id:'new',profile_id:'generated',remote_accessed:false,submission_performed:false,scope:'local_binding_and_frozen_plan_only'});await first;
assert.equal(platformDraftPanel,null);assert.equal(requests.filter(r=>r.action.endsWith('/start')).length,0);
''')

    def test_generated_profile_labels_and_empty_plan_guidance_are_author_facing(self):
        self.js(r'''
const generatedId='fanqie-'+ 'a'.repeat(32)+'.json';
handler=async(action,payload)=>action.endsWith('/catalog')?catalog([plan()],[],{profiles:[{...profile,id:generatedId,label:generatedId}]}):preview(payload);
await platformDraftOpen();let p=platformDraftPanel;
assert.equal(p.profile.value,generatedId);assert.equal(p.profile.options[0].textContent,'番茄本地配置 1');assert.ok(!textTree(p.content).includes(generatedId));
p.dialog.close();handler=async()=>catalog([]);await platformDraftOpen();p=platformDraftPanel;
assert.ok(p.status.textContent.includes('上方配置番茄账号与作品'));assert.ok(!p.status.textContent.includes('请让助手'));assert.equal(p.bindDetails.open,true);
''')

    def test_empty_formal_range_and_active_job_disable_binding_and_cancel_preserves_existing_plan(self):
        self.js(r'''
handler=async(action,payload)=>action.endsWith('/catalog')?catalog([],[],{formal_first:null,formal_last:null,formal_count:0}):preview(payload);
await platformDraftOpen();let p=platformDraftPanel;assert.equal(p.bindButton.disabled,true);assert.ok(p.bindRange.textContent.includes('暂无'));
p.dialog.close();handler=async(action,payload)=>action.endsWith('/catalog')?catalog():preview(payload);
await platformDraftOpen();p=platformDraftPanel;const selected=p.plan.value;p.bindAccount.value='123456';p.bindAccount.oninput();assert.equal(p.run.disabled,true);
await p.bindCancel.onclick();assert.equal(p.plan.value,selected);assert.equal(p.bindingDirty,false);assert.equal(p.run.disabled,false);
platformDraftDrawJob(p,job());assert.equal(p.bindButton.disabled,true);assert.ok(p.bindingInputs.every(input=>input.disabled));
await platformDraftBind(p);assert.equal(requests.filter(r=>r.action.endsWith('/bind')).length,0);
''')


class PlatformDraftHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.WorkbenchTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w = base.story.workbench
        self.server = self.w.editor_server(self.fixture.root)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.token = self.server.editor_url.rstrip('/').split('/')[-1]

    def stop(self):
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()

    def post(self, action, data=None, **overrides):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {'Content-Type': 'application/json', 'X-Story-Token': self.token,
                   'Origin': f'http://127.0.0.1:{self.server.server_port}', **overrides}
        try:
            client.request('POST', '/' + self.token + '/api/platform-drafts/' + action, json.dumps(data or {}), headers)
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()

    def test_four_routes_bind_server_book_and_explicit_mode_without_payload_identity(self):
        backend = base.story.platform_drafts
        book_id = self.fixture.book.meta('id')
        for action, method, payload in (
            ('catalog', 'workbench_catalog', {'offset': 3, 'limit': 5}),
            ('preview', 'workbench_preview', {'plan_id': 'plan', 'profile_id': 'profile', 'reconcile_only': True}),
            ('start', 'workbench_start', {'plan_id': 'plan', 'profile_id': 'profile', 'profile_sha256': 'hash',
                                        'preview_token': 'signed', 'execute': True, 'reconcile_only': True}),
            ('job', 'workbench_job', {'id': 'job'}),
        ):
            with self.subTest(action=action), patch.object(backend, method, return_value={'ok': True}) as mocked:
                code, result = self.post(action, {**payload, 'book_id': 'attacker-other-book', 'root': '/other'})
                self.assertEqual(code, 200, result)
                self.assertEqual(mocked.call_args.args[0], self.fixture.root)
                self.assertEqual(mocked.call_args.kwargs['expected_book_id'], book_id)
                if action in ('preview', 'start'):
                    self.assertTrue(mocked.call_args.kwargs['reconcile_only'])
                if action == 'start':
                    self.assertTrue(mocked.call_args.kwargs['execute'])
                    self.assertEqual(mocked.call_args.kwargs['expected_profile_sha256'], 'hash')

    def test_csrf_origin_and_replaced_book_are_rejected_before_backend(self):
        with patch.object(base.story.platform_drafts, 'workbench_start') as mocked:
            self.assertEqual(self.post('start', **{'X-Story-Token': 'wrong'})[0], 403)
            self.assertEqual(self.post('start', Origin='https://untrusted.example')[0], 403)
            with patch.object(self.w, '_library_entry', side_effect=base.story.StoryError('workbench_book_changed', '作品已替换')):
                self.assertEqual(self.post('start')[0], 409)
            mocked.assert_not_called()

    def test_format_choices_are_forwarded_without_rebinding_the_book(self):
        for action, method in [('preview', 'workbench_preview'), ('start', 'workbench_start')]:
            with self.subTest(action=action), patch.object(base.story.platform_drafts, method, return_value={'ok': True}) as mocked:
                code, _ = self.post(action, {'comparison_policy': 'strict',
                                             'body_encoding': 'plain_paragraphs_v1', 'book_id': 'other'})
                self.assertEqual(code, 200)
                self.assertEqual(mocked.call_args.kwargs['comparison_policy'], 'strict')
                self.assertEqual(mocked.call_args.kwargs['body_encoding'], 'plain_paragraphs_v1')
                self.assertEqual(mocked.call_args.kwargs['expected_book_id'], self.fixture.book.meta('id'))

    def test_unified_mode_is_explicit_and_old_update_request_is_rejected(self):
        for action, method in [('preview', 'workbench_preview'), ('start', 'workbench_start')]:
            with self.subTest(action=action), patch.object(base.story.platform_drafts, method, return_value={'ok': True}) as mocked:
                code, result = self.post(action, {'sync_drafts': True, 'body_encoding': 'plain_paragraphs_v1'})
                self.assertEqual(code, 200, result)
                self.assertTrue(mocked.call_args.kwargs['sync_drafts'])
                mocked.reset_mock()
                code, result = self.post(action, {'update_existing': True, 'body_encoding': 'plain_paragraphs_v1'})
                self.assertEqual(code, 409, result)
                self.assertEqual(result['error'], 'invalid_input')
                mocked.assert_not_called()

    def test_bind_route_uses_server_identity_and_rejects_extra_configuration_fields(self):
        data = {'account_id': '123', 'book_management_url': 'official observed URL',
                'start': 1, 'end': 2, 'expected_revision': 4}
        with patch.object(base.story.platform_drafts, 'workbench_bind', return_value={'ok': True}) as mocked:
            code, result = self.post('bind', data)
            self.assertEqual(code, 200, result)
            self.assertEqual(mocked.call_args.args, (self.fixture.root, '123', 'official observed URL', 1, 2))
            self.assertEqual(mocked.call_args.kwargs, {'expected_book_id': self.fixture.book.meta('id'), 'expected_revision': 4})
        for extra in ({'book_id': 'other-book'}, {'root': '/other'}, {'selector': 'untrusted'}, {'profile_path': '/other.json'}):
            with patch.object(base.story.platform_drafts, 'workbench_bind') as mocked:
                self.assertEqual(self.post('bind', {**data, **extra})[0], 409)
                mocked.assert_not_called()
        with patch.object(base.story.platform_drafts, 'workbench_bind') as mocked:
            self.assertEqual(self.post('bind', data, **{'X-Story-Token': 'wrong'})[0], 403)
            self.assertEqual(self.post('bind', data, Origin='https://untrusted.example')[0], 403)
            mocked.assert_not_called()

    def test_page_has_toolbar_entry_without_embedding_platform_targets(self):
        page = self.w._editor_page(self.w.snapshot(self.fixture.book), {}, 'test').decode()
        self.assertIn('id="platform-drafts-open"', page)
        self.assertIn('function platformDraftOpen()', page)
        self.assertNotIn(self.fixture.account_id, page)
        self.assertNotIn(self.fixture.remote_book_id, page)
        self.assertNotIn('__PLATFORM_DRAFTS__', page)
        self.assertNotIn('__PLATFORM_DISABLED__', page)


if __name__ == '__main__':
    unittest.main()

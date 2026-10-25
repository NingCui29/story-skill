"""Fake DOM behavior for automatic Chrome connection; no model/browser calls."""
import shutil
import subprocess
import unittest
from unittest.mock import patch

from tests import test_publish as fixtures


class WorkbenchAutoConnectUITests(unittest.TestCase):
    def js(self, assertions):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js required")
        mock = r'''
const assert=require('node:assert/strict');
const controls={},timers=new Map(),requests=[];let timerId=0,handler,clockNow=2000000000000;
Date.now=()=>clockNow;
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
const docs=new Map([['editor',{text:'正式稿',value:'用户未保存的文字',editable:true}]]),dirty=d=>!!d&&d.text!==d.value;
const currentBookInfo={kind:'long',kind_verified:true,title:'合成验收作品'};
const setTimeout=(fn,delay)=>{const id=++timerId;timers.set(id,{fn,delay});return id;},clearTimeout=id=>timers.delete(id);
const call=async(action,payload={})=>{requests.push({action,payload:structuredClone(payload)});return handler(action,payload);};
const plan=(id='plan-one')=>({id,platform:'fanqie',status:'prepared',book_title:'合成验收作品',chapters:[1,2]});
const profile={id:'synthetic.json',label:'番茄合成配置',valid:true,platform:'fanqie',sha256:'profile-sha'};
const catalog=(jobs=[])=>({book_id:'book-one',revision:7,formal_first:1,formal_last:2,formal_count:2,max_batch:1000,plans:[plan()],profiles:[profile],jobs,next_offset:null});
const token=(suffix='connect',seconds=600)=>(Math.floor(Date.now()/1000)+seconds)+'.'+suffix;
const connect=payload=>({comparison_policy:payload.comparison_policy,body_encoding:payload.body_encoding,book_id:'book-one',plan_id:payload.plan_id,profile_id:payload.profile_id,profile_sha256:'profile-sha',mode:payload.reconcile_only?'reconcile_only':'sync_drafts',can_start:false,connection_ready:false,preview_token:null,can_connect:true,connect_token:token(payload.reconcile_only?'read-connect':'connect'),automatic_connection:{available:true,uses_codex_quota:true},blockers:['platform_chrome_worker_unavailable'],preview:{platform:'fanqie',account_id:'synthetic-account',remote_book_id:'synthetic-target',chapters:[{chapter:1,title:'合成标题',body_characters:100}],results:[]}});
const ready=payload=>({...connect(payload),can_start:true,connection_ready:true,preview_token:token('ready'),can_connect:false,connect_token:null,blockers:[]});
const job=()=>({comparison_policy:'strict',body_encoding:'plain_paragraphs_v1',id:'job-one',book_id:'book-one',plan_id:'plan-one',mode:'sync_drafts',state:'running',stage:'connecting_chrome',done:false,total_chapters:1,verified_chapters:0,result:null});
handler=async(action,payload)=>{if(action.endsWith('/catalog'))return catalog();if(action.endsWith('/preview'))return connect(payload);if(action.endsWith('/start'))return job();if(action.endsWith('/job'))return job();throw Error(action);};
const starts=()=>requests.filter(row=>row.action.endsWith('/start'));
'''
        source = fixtures.story.workbench._editor_platform_drafts_script()
        script = mock + source + "\n(async()=>{\n" + assertions + \
                 "\n})().catch(error=>{console.error(error);process.exitCode=1;});"
        # The complete editor script exceeds Windows' command-line limit.
        command = [node, "-"]
        original_popen = subprocess.Popen
        def only_node(arguments, *args, **kwargs):
            self.assertEqual(arguments, command)
            self.assertFalse(kwargs.get("shell", False))
            return original_popen(arguments, *args, **kwargs)
        with patch.object(subprocess, "Popen", side_effect=only_node) as guarded:
            result = subprocess.run(command, input=script, encoding="utf-8", capture_output=True, timeout=15)
        self.assertEqual(guarded.call_count, 1)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_connect_only_preview_enables_button_with_connect_expiry_and_preserves_editor(self):
        self.js(r'''
const before=JSON.stringify([...docs]);await platformDraftOpen();const p=platformDraftPanel;
assert.equal(p.preview.can_start,false);assert.equal(p.preview.can_connect,true);
assert.equal(p.previewExpires,2000000600000);assert.equal(p.run.disabled,false);
assert.equal(p.run.textContent,'连接并同步所选第1—2章（2章）到平台草稿');
assert.ok(p.status.textContent.includes('Codex 额度'));assert.equal(starts().length,0);
assert.equal(JSON.stringify([...docs]),before);
''')

    def test_explicit_connect_click_uses_connect_token_and_sets_auto_connect_once(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel,selected=p.preview.connect_token;
await p.run.onclick();assert.equal(starts().length,1);const payload=starts()[0].payload;
assert.equal(payload.preview_token,selected);assert.equal(payload.auto_connect,true);
assert.equal(payload.execute,true);assert.equal(payload.reconcile_only,false);
assert.equal(payload.profile_sha256,'profile-sha');assert.equal(p.run.disabled,true);
assert.equal(p.job.stage,'connecting_chrome');
''')

    def test_waiting_chrome_status_does_not_enable_duplicate_start_or_claim_saved(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;await p.run.onclick();
platformDraftDrawJob(p,{...job(),stage:'waiting_for_chrome'});
assert.ok(p.status.textContent.includes('正在等待现有 Chrome 连接'));
assert.ok(p.status.textContent.includes('自动检测中'));
assert.ok(p.status.textContent.includes('尚未开始保存'));
assert.equal(p.run.disabled,true);assert.equal(Number(p.progress.value),0);
await p.run.onclick();assert.equal(starts().length,1);
platformDraftDrawJob(p,{...job(),stage:'connecting_chrome'});
assert.equal(p.status.textContent,'正在连接现有 Chrome');
platformDraftDrawJob(p,{...job(),done:true,state:'failed',stage:'finished',error:'platform_chrome_worker_browser_wait_timeout'});
assert.ok(p.status.textContent.includes('自动检测后仍未发现现有 Chrome 连接'));
assert.ok(p.status.textContent.includes('本次尚未开始保存'));
assert.equal(p.run.disabled,true);assert.equal(starts().length,1);
''')

    def test_ready_preview_never_automatically_starts_and_explicit_click_does_not_connect_model(self):
        self.js(r'''
handler=async(action,payload)=>action.endsWith('/catalog')?catalog():action.endsWith('/preview')?ready(payload):job();
await platformDraftOpen();const p=platformDraftPanel;
assert.equal(p.run.disabled,false);assert.equal(p.run.textContent,'同步所选第1—2章（2章）到平台草稿');
assert.equal(starts().length,0);await platformDraftPreview(p);assert.equal(starts().length,0);
const selected=p.preview.preview_token;await p.run.onclick();
assert.equal(starts().length,1);assert.equal(starts()[0].payload.preview_token,selected);
assert.equal(starts()[0].payload.auto_connect,false);
''')

    def test_connect_expiry_invalidates_authorization_and_cannot_send(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;
const expiry=[...timers.values()].find(item=>item.delay===600000);assert.ok(expiry);
clockNow+=600000;assert.equal(platformDraftCanStart(p),false);
expiry.fn();assert.equal(p.preview,null);assert.equal(p.run.disabled,true);
await p.run.onclick();assert.equal(starts().length,0);
''')

    def test_missing_invalid_or_expired_connect_token_cannot_enable_action(self):
        self.js(r'''
for(const value of [null,'invalid',token('expired',-1)]){
 handler=async(action,payload)=>action.endsWith('/catalog')?catalog():{...connect(payload),connect_token:value};
 await platformDraftOpen();const p=platformDraftPanel;assert.equal(p.run.disabled,true);
 await p.run.onclick();assert.equal(starts().length,0);p.dialog.close();
}
''')

    def test_binding_dirty_and_every_busy_state_disable_connect_without_touching_edits(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel,before=JSON.stringify([...docs]);
for(const property of ['starting','binding','loadingCatalog']){
 p[property]=true;platformDraftButtons(p);assert.equal(p.run.disabled,true);
 await p.run.onclick();assert.equal(starts().length,0);p[property]=false;platformDraftButtons(p);
 assert.equal(p.run.disabled,false);
}
p.jobs=[job()];platformDraftButtons(p);assert.equal(p.run.disabled,true);p.jobs=[];
platformDraftBindingChanged(p);assert.equal(p.preview,null);assert.equal(p.previewExpires,0);
assert.equal(p.run.disabled,true);await p.run.onclick();assert.equal(starts().length,0);
assert.equal(JSON.stringify([...docs]),before);
''')

    def test_duplicate_connect_click_and_closed_panel_do_not_create_another_model_job(self):
        self.js(r'''
let release;handler=async(action,payload)=>action.endsWith('/catalog')?catalog():action.endsWith('/preview')?connect(payload):new Promise(resolve=>{release=resolve;});
await platformDraftOpen();const p=platformDraftPanel;const pending=p.run.onclick();
await p.run.onclick();assert.equal(starts().length,1);p.dialog.close();release(job());await pending;
assert.equal(platformDraftPanel,null);assert.equal(starts().length,1);
assert.equal([...timers.values()].filter(item=>item.delay===1500).length,0);
''')

    def test_late_connect_preview_does_not_replace_new_ready_selection(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;let release;
handler=async(action,payload)=>payload.profile_id==='old'?new Promise(resolve=>{release=()=>resolve(connect(payload));}):ready(payload);
p.profile.value='old';const pending=platformDraftPreview(p);p.profile.value='synthetic.json';await platformDraftPreview(p);release();await pending;
assert.equal(p.preview.profile_id,'synthetic.json');assert.equal(p.preview.can_start,true);
assert.equal(p.run.textContent,'同步所选第1—2章（2章）到平台草稿');assert.equal(starts().length,0);
''')

    def test_readonly_connect_click_preserves_mode_and_uses_its_new_token(self):
        self.js(r'''
await platformDraftOpen();const p=platformDraftPanel;
p.mode.value='reconcile_only';await p.mode.onchange();
assert.equal(p.run.textContent,'连接并只读核对所选批次');const selected=p.preview.connect_token;
assert.ok(selected.endsWith('.read-connect'));assert.equal(starts().length,0);
await p.run.onclick();assert.equal(starts()[0].payload.reconcile_only,true);
assert.equal(starts()[0].payload.preview_token,selected);assert.equal(starts()[0].payload.auto_connect,true);
''')

    def test_ready_token_has_priority_over_extra_connect_token_and_uses_its_expiry(self):
        self.js(r'''
handler=async(action,payload)=>action.endsWith('/catalog')?catalog():{...ready(payload),can_connect:true,connect_token:token('extra',1200)};
await platformDraftOpen();const p=platformDraftPanel,selected=p.preview.preview_token;
assert.equal(p.previewExpires,2000000600000);assert.equal(p.run.textContent,'同步所选第1—2章（2章）到平台草稿');
await p.run.onclick();assert.equal(starts()[0].payload.preview_token,selected);
assert.equal(starts()[0].payload.auto_connect,false);
''')

    def test_origin_denial_explains_permission_separately_from_extension_connection(self):
        self.js(r'''
const message=platformDraftErrorCode('platform_chrome_worker_origin_permission_required');
assert.ok(message.includes('页面访问授权'));assert.ok(message.includes('操作已停止'));
assert.ok(!message.includes('扩展连接'));assert.equal(starts().length,0);
''')


if __name__ == "__main__":
    unittest.main()
